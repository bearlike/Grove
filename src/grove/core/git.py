"""All git subprocess operations Grove ever runs, bound to one repository.

`GitRepo` is the canonical surface: every method takes whatever extra
arguments it needs (worktree path, branch name, …) and operates against
the repo it was constructed for. The class form means one place owns the
"how do I shell out to git" concern, with a single `_run` helper enforcing
`shell=False` + list args (no injection risk, cross-platform).

Read-only branch helpers (`list_local_branches`, `list_remote_branches`,
`current_branch`, `default_branch`, `find_branch`, `checkout_location`)
populate the data the TUI's create-modal dropdowns need and the validation
the engine runs before any worktree is touched.

The contract is deliberately small: detect a repo root, add/remove/prune
worktrees, delete a branch, check cleanliness, plus four read-only stat
helpers used by `peek()`, plus the new branch-read helpers above. No
commits, no pushes — those are the user's job (in their shell, via
lazygit, gh, etc.).
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from loguru import logger

from grove.core.contracts.branch_info import BranchInfo
from grove.core.errors import GitError
from grove.core.workspace import CommitSummary

_DIFFSTAT_INSERTIONS = re.compile(r"(\d+) insertion")
_DIFFSTAT_DELETIONS = re.compile(r"(\d+) deletion")


class GitRepo:
    """All git operations bound to a single repository root.

    Construct via ``GitRepo(root)`` when the path is known, or
    ``GitRepo.detect(cwd)`` to find the enclosing repo for an arbitrary
    directory. Methods are pure subprocess wrappers — no caching, no
    state beyond the bound `root`. Errors raised on failure are
    `GitError`; the read-only helpers prefer `check=False` and return
    empty / zeros on failure so the caller (peek loops, branch
    dropdowns) doesn't break on transient issues.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    # ─── identity ──────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        """Canonical absolute path of the repo this instance is bound to."""
        return self._root

    @classmethod
    def detect(cls, cwd: Path) -> GitRepo | None:
        """Build a `GitRepo` for the enclosing repo of `cwd`.

        Returns `None` when `cwd` is not inside a git repository. Cheap
        wrapper around the stateless `detect_root` query — useful when
        the caller actually wants the bound object rather than just the
        path.
        """
        root = cls.detect_root(cwd)
        return cls(root) if root else None

    @classmethod
    def detect_root(cls, cwd: Path) -> Path | None:
        """Stateless query: canonical absolute path of the enclosing git repo, or None."""
        result = cls._run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, check=False)
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        return Path(raw).resolve() if raw else None

    # ─── worktree lifecycle ────────────────────────────────────────────────

    def worktree_add(
        self,
        worktree_path: Path,
        *,
        new_branch: str | None = None,
        existing_branch: str | None = None,
        base: str = "HEAD",
    ) -> None:
        """Create a worktree.

        Exactly one of `new_branch` (uses `git worktree add -b`) or
        `existing_branch` (uses `git worktree add` to check out an
        existing ref) must be supplied. `base` is ignored when
        `existing_branch` is set.
        """
        if bool(new_branch) == bool(existing_branch):
            raise ValueError("specify exactly one of new_branch or existing_branch")
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if new_branch:
            cmd = ["git", "worktree", "add", "-b", new_branch, str(worktree_path), base]
        else:
            cmd = ["git", "worktree", "add", str(worktree_path), existing_branch or ""]
        self._run(cmd, cwd=self._root)

    def worktree_remove(self, worktree_path: Path, *, force: bool = False) -> None:
        """Remove a worktree directory. `force=True` discards uncommitted changes."""
        cmd = ["git", "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(worktree_path))
        self._run(cmd, cwd=self._root)

    def worktree_prune(self) -> None:
        """Drop administrative entries for worktrees that no longer exist on disk."""
        self._run(["git", "worktree", "prune"], cwd=self._root, check=False)

    def worktree_paths(self) -> list[Path]:
        """Every worktree of this repo, **main worktree first** (git's order).

        Read-only `git worktree list --porcelain` parse. Works when bound to a
        *linked* worktree too — git reports the whole family either way — which
        is what lets the session explorer find sibling worktrees from inside
        any of them. Returns ``[root]`` on failure so callers always have at
        least the bound root to scan.
        """
        result = self._run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return [self._root]
        paths = [
            Path(line[len("worktree ") :].strip())
            for line in result.stdout.splitlines()
            if line.startswith("worktree ")
        ]
        return paths or [self._root]

    def branch_delete(self, branch: str, *, force: bool = True) -> bool:
        """Delete a branch. Returns False (no raise) if it was already gone."""
        flag = "-D" if force else "-d"
        result = self._run(["git", "branch", flag, branch], cwd=self._root, check=False)
        if result.returncode == 0:
            return True
        stderr = result.stderr.lower()
        if "not found" in stderr or "no such branch" in stderr:
            return False
        raise GitError(
            f"`git branch {flag} {branch}` failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    def branch_set_upstream(self, branch: str, upstream: str) -> None:
        """Configure `branch` to track `upstream` (e.g. `origin/feature/x`).

        Best-effort: if the upstream ref does not exist this raises
        `GitError`. Used by `WorkspaceManager.create()` after a
        `TrackRemoteBranch` resolved into a fresh local tracking branch.
        """
        self._run(
            ["git", "branch", "--set-upstream-to", upstream, branch],
            cwd=self._root,
        )

    # ─── worktree state ────────────────────────────────────────────────────

    def is_clean(self, worktree_path: Path) -> bool:
        """True iff the worktree has no staged or unstaged changes (untracked ignored)."""
        result = self._run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=worktree_path,
            check=False,
        )
        return result.returncode == 0 and not result.stdout.strip()

    def dirty_file_count(self, worktree_path: Path) -> int:
        """Count of files with staged, unstaged, or untracked changes.

        Returns 0 if the worktree directory does not exist (paused workspaces).
        """
        if not worktree_path.exists():
            return 0
        result = self._run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            check=False,
        )
        if result.returncode != 0:
            return 0
        return sum(1 for line in result.stdout.splitlines() if line.strip())

    # ─── branch read helpers (peek + create dropdowns + validation) ────────

    def ahead_behind(self, branch: str, base: str) -> tuple[int, int]:
        """Return (ahead, behind) commit counts of `branch` relative to `base`.

        Uses `git rev-list --left-right --count base...branch`, whose output is
        "<behind>\\t<ahead>" by symmetric-difference convention. We swap them so
        callers read it in the natural order. Resolves "HEAD" against `root`
        (the parent repo) — slightly stale if the parent has moved on, but that's
        the closest signal available without bookkeeping at create time.
        Returns (0, 0) if either ref is missing.
        """
        result = self._run(
            ["git", "rev-list", "--left-right", "--count", f"{base}...{branch}"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return (0, 0)
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            return (0, 0)
        try:
            behind, ahead = int(parts[0]), int(parts[1])
        except ValueError:
            return (0, 0)
        return (ahead, behind)

    def diff_stats(self, branch: str, base: str) -> tuple[int, int]:
        """Return (added, removed) line counts of `branch` vs `base`."""
        result = self._run(
            ["git", "diff", "--shortstat", f"{base}...{branch}"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return (0, 0)
        text = result.stdout
        added_match = _DIFFSTAT_INSERTIONS.search(text)
        removed_match = _DIFFSTAT_DELETIONS.search(text)
        added = int(added_match.group(1)) if added_match else 0
        removed = int(removed_match.group(1)) if removed_match else 0
        return (added, removed)

    def recent_commits(self, branch: str, *, limit: int = 3) -> tuple[CommitSummary, ...]:
        """Most recent N commits on `branch`, newest first.

        Walks all of `branch`'s history (no fork-point filter); kept for the
        TUI peek rail's tight 3-row summary. For "what was done in this
        workspace?" use ``branch_commits(branch, base)`` — that filter is
        the comprehensive view a detail screen wants.
        """
        return self._parse_commit_log(["git", "log", f"-n{limit}", branch])

    def branch_commits(
        self,
        branch: str,
        base: str,
        *,
        limit: int | None = None,
    ) -> tuple[CommitSummary, ...]:
        """Commits on `branch` since it diverged from `base`, newest first.

        The comprehensive history view — semantically ``git log base..branch``.
        Default is uncapped because the consumer (the webapp's detail page)
        wants the full log; pass `limit` for rail-style top-N renderings.
        Empty tuple when the range is empty (branch == base) or the command
        fails. No git side effects.
        """
        cmd = ["git", "log"]
        if limit is not None:
            cmd.append(f"-n{limit}")
        cmd.append(f"{base}..{branch}")
        return self._parse_commit_log(cmd)

    def _parse_commit_log(self, cmd: list[str]) -> tuple[CommitSummary, ...]:
        """Run a ``git log`` invocation that ends in the rev-spec and parse it.

        Caller passes the prefix (``["git", "log", "-n3", branch]`` or
        ``["git", "log", "base..branch"]``); this helper appends the format
        flag + ``--`` separator, executes, and parses the tab-delimited
        output. Centralised so the two callers can't drift on tab parsing
        or date handling.
        """
        fmt = "%h%x09%s%x09%cI"  # short-sha \t subject \t committer-iso-date
        full = [*cmd, f"--pretty=format:{fmt}", "--"]
        result = self._run(full, cwd=self._root, check=False)
        if result.returncode != 0:
            return ()
        commits: list[CommitSummary] = []
        for line in result.stdout.splitlines():
            sha, _, rest = line.partition("\t")
            subject, _, when = rest.rpartition("\t")
            if not sha or not when:
                continue
            try:
                committed_at = datetime.fromisoformat(when)
            except ValueError:
                continue
            commits.append(CommitSummary(sha=sha, subject=subject, committed_at=committed_at))
        return tuple(commits)

    def list_local_branches(self) -> list[BranchInfo]:
        """Every local branch, annotated with HEAD marker, upstream, and checkout site.

        `checked_out_in` is populated for branches currently checked out
        in any worktree (including the main one); the create-modal uses
        it to gray out unselectable rows in the Existing-branch dropdown
        and the engine uses it to raise `BranchAlreadyCheckedOut`.
        """
        locations = self._worktree_branches()
        result = self._run(
            [
                "git",
                "for-each-ref",
                "refs/heads/",
                "--format=%(refname:short)\t%(HEAD)\t%(upstream:short)",
            ],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return []
        branches: list[BranchInfo] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, head_marker, upstream = parts[0], parts[1], parts[2]
            branches.append(
                BranchInfo(
                    name=name,
                    kind="local",
                    is_current=(head_marker == "*"),
                    upstream=upstream or None,
                    checked_out_in=locations.get(name),
                )
            )
        return branches

    def list_remote_branches(self) -> list[BranchInfo]:
        """Every remote-tracking branch (e.g. `origin/feature/x`).

        `origin/HEAD` and similar symref entries are filtered out — they
        point at another remote branch already in the list and would be
        misleading in a dropdown.
        """
        result = self._run(
            ["git", "for-each-ref", "refs/remotes/", "--format=%(refname:short)"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return []
        branches: list[BranchInfo] = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if not name or name.endswith("/HEAD"):
                continue
            branches.append(BranchInfo(name=name, kind="remote"))
        return branches

    def current_branch(self) -> str | None:
        """The local branch HEAD points to, or `None` if HEAD is detached."""
        result = self._run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return None
        name = result.stdout.strip()
        return name or None

    def default_branch(self) -> str:
        """Best-effort detection of the repo's default branch.

        Probes in order: ``origin/HEAD`` symref → ``init.defaultBranch``
        config → literal ``main``. Used by the create-modal as the
        prepopulated default for `base_ref` selectors.
        """
        result = self._run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=self._root,
            check=False,
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            if ref.startswith("origin/"):
                return ref[len("origin/") :]
        result = self._run(
            ["git", "config", "--get", "init.defaultBranch"],
            cwd=self._root,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return "main"

    def find_branch(self, name: str) -> BranchInfo | None:
        """Look up a branch by name across local and remote refs.

        Local matches take precedence over remote. Returns `None` if no
        ref matches; the engine raises `BranchNotFound` from there.
        """
        for b in self.list_local_branches():
            if b.name == name:
                return b
        for b in self.list_remote_branches():
            if b.name == name:
                return b
        return None

    def checkout_location(self, branch: str) -> Path | None:
        """Worktree path where `branch` is currently checked out, or `None`.

        Drives the `BranchAlreadyCheckedOut` validation in
        `WorkspaceManager.create()` and the grayed-out rows in the
        create-modal's Existing-branch dropdown.
        """
        return self._worktree_branches().get(branch)

    def rev_parse(self, ref: str) -> str | None:
        """Resolve `ref` to a full SHA, or `None` if the ref is unknown.

        Used by the engine to validate that a `base_ref` (which may be a
        branch, tag, or sha) actually exists before issuing a `git
        worktree add` that would fail on it.
        """
        result = self._run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None

    # ─── internal ──────────────────────────────────────────────────────────

    def _worktree_branches(self) -> dict[str, Path]:
        """Map of branch name → worktree path for every checked-out branch.

        Single subprocess call, parsed once and reused by callers
        (`list_local_branches`, `checkout_location`). Detached worktrees
        produce no entry in the map; only entries with an explicit
        `branch refs/heads/<name>` line are tracked.
        """
        result = self._run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self._root,
            check=False,
        )
        if result.returncode != 0:
            return {}
        locations: dict[str, Path] = {}
        current_path: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line[len("worktree ") :].strip())
            elif line.startswith("branch refs/heads/") and current_path is not None:
                branch_name = line[len("branch refs/heads/") :]
                locations[branch_name] = current_path
            elif not line.strip():
                current_path = None
        return locations

    @staticmethod
    def _run(
        cmd: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git subprocess; raise `GitError` on non-zero exit when `check=True`.

        Always `shell=False` with list args, so cross-platform and no
        injection risk. The single home for "how does Grove shell out to
        git"; the various code paths (worktree lifecycle, branch reads,
        peek stats) all funnel through here.
        """
        logger.debug("git: {} (cwd={})", " ".join(cmd), cwd)
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if check and result.returncode != 0:
            raise GitError(
                f"`{' '.join(cmd)}` failed with exit {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result


# ─── module-level alias for stateless callers ──────────────────────────────


def detect_root(cwd: Path) -> Path | None:
    """Find the canonical absolute path of the enclosing git repo, or None.

    Thin wrapper around `GitRepo.detect_root` for callers that need only
    the path (the CLI's repo-detection flow before any manager exists).
    Stateless — no `GitRepo` instance constructed — so it stays cheap to
    call from one-shot subcommands.
    """
    return GitRepo.detect_root(cwd)


__all__ = ["GitRepo", "detect_root"]
