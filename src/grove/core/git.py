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
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from loguru import logger

from grove.core import paths
from grove.core.contracts.branch_info import BranchInfo
from grove.core.errors import GitError
from grove.core.workspace import CommitScope, CommitSummary

_DIFFSTAT_INSERTIONS = re.compile(r"(\d+) insertion")
_DIFFSTAT_DELETIONS = re.compile(r"(\d+) deletion")


def _assert_not_flaglike(**values: str | None) -> None:
    """Refuse a ref/branch value git would parse as an option.

    The second of two layers, and the one that makes the fix stay fixed: the
    contract (`contracts/branch_plan.BRANCH_NAME_PATTERN`) validates intent at
    the boundary, and this refuses an argv the side-effect module should never
    have been handed — so a future call site that bypasses the contract, or a
    new variant that forgets the pattern, cannot reintroduce the hole.

    **`shell=False` and list-form argv do not help here**, which is the whole
    trap: they prevent a value being *embedded* in a command, and this value
    *is* the command. Measured against real git 2.43 —
    `git worktree add -b -m <path> origin/main` renamed the checked-out branch,
    and `-b -D <path> feature/x` printed `Deleted branch feature/x`. Both then
    exited `fatal:`, so the caller saw a failure while the damage was done.

    Nor can `--` or `--end-of-options` fix it: `git worktree add` hands the
    `-b` value to an internal `git branch` that re-parses it as its own argv,
    where no separator of ours is present. Validation is the only defence.

    Applied to the MUTATING ref-takers only. The read-only helpers pass refs
    too, but a misparsed flag there costs a wrong answer, not a destroyed
    branch, and they are already `check=False` best-effort — guarding them
    would trade a real property for noise.
    """
    for label, value in values.items():
        if value is not None and value.startswith("-"):
            raise GitError(
                f"refusing to run git with {label}={value!r}: a leading '-' would be "
                "parsed as a command-line flag, not a ref"
            )


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

    #: Wall-clock bound on every git subprocess. Generous rather than
    #: tuned: the point is that no git invocation can hang FOREVER, because a
    #: single one that does takes the whole caller with it — the daemon runs
    #: these on its event loop, so one wedged `git` (a credential prompt on a
    #: `fetch`-shaped call, a stale NFS mount, a `.git/index.lock` holder) stops
    #: every repo's dashboard, not just this repo's. A bound sized to the
    #: slowest legitimate operation (`worktree add` materializing a large tree)
    #: costs nothing on the healthy path and is the only thing standing between
    #: a hung child and an unrecoverable surface. Not a config knob: `GitRepo`
    #: is constructed from a bare path at a dozen call sites, and threading a
    #: cascade value through all of them would buy nothing a constant does not.
    TIMEOUT_SECONDS: ClassVar[float] = 120.0

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
        _assert_not_flaglike(new_branch=new_branch, existing_branch=existing_branch, base=base)
        paths.ensure_dir(worktree_path.parent)
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
        _assert_not_flaglike(branch=branch)
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
        _assert_not_flaglike(branch=branch, upstream=upstream)
        self._run(
            ["git", "branch", "--set-upstream-to", upstream, branch],
            cwd=self._root,
        )

    # ─── worktree state ────────────────────────────────────────────────────

    def is_clean(self, worktree_path: Path) -> bool:
        """True iff removing this worktree would verifiably discard nothing.

        **Untracked files count**, and that is the whole correctness of this
        method rather than a detail. It answers exactly one question — may
        `pause` remove this worktree? — and `git worktree remove` refuses on
        "modified **or untracked** files". The original `--untracked-files=no`
        form disagreed with the very command it was gating: a worktree holding
        only new files reported clean and git still refused, which is the
        commonest shape there is, since an agent's first act is usually to
        create files. Wiring that version as the precondition would have left
        the half-torn-down state intact for the majority case while looking
        fixed. Verified against real git, not assumed.

        ``False`` also covers "cannot tell" — a missing directory, a git that
        errored. That is deliberate and fail-closed: the caller's remedy is
        ``force``, and refusing to pause something we cannot inspect is strictly
        safer than tearing down a session and a container to find out.
        """
        if not worktree_path.is_dir():
            return False
        result = self._run(["git", "status", "--porcelain"], cwd=worktree_path, check=False)
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

    # ─── working-tree patch (the Files tab's only source) ──────────────────

    def tracked_patch(
        self, worktree_path: Path, *, base: str = "HEAD", path: str | None = None
    ) -> str:
        """Unified `git diff <base>` for the worktree — TRACKED changes only.

        `base` is the revision the caller measures from; the `"HEAD"` default is
        "uncommitted changes only" and stays the answer for a workspace with no
        recorded creation anchor. A caller passing that anchor gets everything
        the workspace has done — committed and not — in one patch, which is the
        only form in which a file committed an hour ago still appears.

        Returned verbatim, never parsed: `git diff` produces the format and the
        client's diff renderer consumes it, so anything in between is a second
        model of a format git already owns. Binary files therefore arrive as
        git's own `Binary files … differ` line rather than being filtered out.

        `--no-color` and `--no-ext-diff` are load-bearing rather than tidy: a
        user's `color.diff=always` or a configured `diff.external` would
        otherwise hand back ANSI escapes or some other tool's output entirely,
        and the consumer is a parser expecting plain unified diff.

        Best-effort like every other peek-shaped read — a failure is `""`, and
        the CALLER distinguishes "no changes" from "could not read" (see
        `WorkspaceManager.working_diff`); this returning empty never means the
        second thing on its own.
        """
        cmd = ["git", "diff", "--no-color", "--no-ext-diff", base]
        if path is not None:
            cmd += ["--", path]
        result = self._run(cmd, cwd=worktree_path, check=False)
        return result.stdout if result.returncode == 0 else ""

    def untracked_files(self, worktree_path: Path) -> tuple[str, ...]:
        """Worktree-relative paths git can see but does not track, honouring ignores.

        Separate from `tracked_patch` because **`git diff HEAD` does not show a
        new file at all**, and creating files is an agent's usual first act — so
        a diff built from `git diff` alone is blank for exactly the work a
        reviewer most wants to see. `--exclude-standard` applies the same ignore
        rules `git status` does, so build output never reaches the patch.
        """
        result = self._run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=worktree_path,
            check=False,
        )
        if result.returncode != 0:
            return ()
        return tuple(entry for entry in result.stdout.split("\0") if entry)

    def untracked_patch(self, worktree_path: Path, rel_path: str) -> str:
        """Unified diff for ONE untracked file, as an all-additions patch.

        **`--no-index` is the whole point: it never touches the index.** The
        obvious alternative — `git add --intent-to-add` — would stage into the
        user's own worktree, and Grove does not mutate a user's git state to
        answer a read (the same rule that keeps `commit`/`push` out of this
        module). Diffing against `/dev/null` gets the same all-additions patch
        with no side effect at all.

        `--no-index` exits **1 when the files differ**, which is the normal case
        here, so a zero exit would mean an EMPTY new file; both are real answers
        and only a higher code is a failure.
        """
        cmd = ["git", "diff", "--no-color", "--no-ext-diff", "--no-index", "--"]
        result = self._run([*cmd, "/dev/null", rel_path], cwd=worktree_path, check=False)
        return result.stdout if result.returncode in (0, 1) else ""

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
        return self._parse_commit_log(["git", "log", f"-n{limit}", branch], scope=None)

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
        return self._parse_commit_log(cmd, scope="since_fork_point")

    def branch_commits_since(
        self,
        branch: str,
        since: datetime,
        *,
        limit: int | None = None,
    ) -> tuple[CommitSummary, ...]:
        """Commits on `branch` dated at or after `since`, newest first.

        The honest degradation for a workspace with **no recorded fork point**.
        A ref range cannot answer there: `diff_base` falls back to `base_branch`,
        which for a ROOT workspace is the literal string ``"HEAD"``, and
        ``git log HEAD..<branch>`` is *structurally* empty however much work was
        done — measured on this repo's own root workspace, 0 against a true 106.
        A timestamp is a different kind of answer rather than a better guess: it
        never claims to be the anchor, and `WorkspaceState.created_at` is a
        recorded fact, which is exactly what a merge-base backfill would not be.

        The answer **errs high** — see `CommitScope`, which is how the caller
        tells this apart from a fork-point answer.

        `since` is a `datetime` rather than a string ON PURPOSE, and that type is
        the flag guard: this repo's recorded incident is a value that BECOMES an
        option, which `shell=False` and list-form argv do not defend against. A
        `datetime` cannot spell one, `isoformat()` of it always begins with a
        digit, and it is interpolated into a SINGLE ``--since=<value>`` token, so
        there is no argv position at which it could be read as an option of its
        own. Formatting it ourselves also closes git's quieter trap: ``--since``
        is parsed by approxidate, which never fails — an unparseable string is
        silently taken as *now*, i.e. an empty log that looks like an answer.

        Naive input is read as UTC, matching `WorkspaceState.adopts_session`'s
        coercion so the two cannot disagree about what a stored stamp meant.
        """
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        cmd = ["git", "log"]
        if limit is not None:
            cmd.append(f"-n{limit}")
        cmd.append(f"--since={since.astimezone(UTC).isoformat()}")
        cmd.append(branch)
        return self._parse_commit_log(cmd, scope="since_created_at")

    def _parse_commit_log(
        self,
        cmd: list[str],
        *,
        scope: CommitScope | None,
    ) -> tuple[CommitSummary, ...]:
        """Run a ``git log`` invocation that ends in the rev-spec and parse it.

        Caller passes the prefix (``["git", "log", "-n3", branch]`` or
        ``["git", "log", "base..branch"]``); this helper appends the format
        flag + ``--`` separator, executes, and parses the tab-delimited
        output. Centralised so the three callers can't drift on tab parsing
        or date handling.

        `scope` is required and unguessable from `cmd`, so each caller states
        which question its range answered rather than this helper inferring one.
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
            commits.append(
                CommitSummary(sha=sha, subject=subject, committed_at=committed_at, scope=scope)
            )
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

    def common_dir(self) -> Path | None:
        """Absolute path of the repo's SHARED git dir, or `None` if unreadable.

        For the main checkout this is its own `.git` directory; for a linked
        worktree it is `<main>/.git`, because a worktree's `.git` is only a
        pointer *file* (`gitdir: <main>/.git/worktrees/<name>`). Anything that
        gives a worktree its own filesystem namespace — a container bind mount
        above all — must carry this directory across too, or every git command
        inside it fails to resolve the repository.
        """
        result = self._run(["git", "rev-parse", "--git-common-dir"], cwd=self._root, check=False)
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        # git answers relatively (`.git`) for the main checkout, absolutely for
        # a linked worktree — resolve against the root so callers get one shape.
        return (self._root / raw).resolve()

    def remote_urls(self) -> tuple[str, ...]:
        """Every configured remote URL, deduped in `git remote -v` order.

        The one input the egress allowlist cannot derive without git: a
        containerized agent must be able to fetch and push its own repository,
        and a self-hosted LAN forge is first-class — so the destinations come
        from the repo itself rather than a list the user has to restate. Raw URLs,
        never parsed here: host extraction is pure policy and lives in
        ``core.container_policy``. Best-effort — an unreadable repo yields ``()``.
        """
        result = self._run(["git", "remote", "-v"], cwd=self._root, check=False)
        if result.returncode != 0:
            return ()
        seen: dict[str, None] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                seen.setdefault(parts[1], None)
        return tuple(seen)

    def ensure_excluded(self, *patterns: str) -> None:
        """Add *patterns* to the repo's local exclude file, idempotently.

        For Grove's OWN generated artifacts — the complete devcontainer override
        and the egress script, which must live inside the worktree for relative
        config paths and the container mount to resolve. Without this they are
        untracked files, and untracked files have two costs, both real:
        ``git worktree remove`` refuses (so ``pause`` and ``kill`` fail on every
        containerized workspace), and every ``git status`` the user runs inside
        the workspace shows Grove's plumbing as their own uncommitted work.

        It must be the **common** dir's ``info/exclude``: a linked worktree's own
        ``$GIT_DIR/info/exclude`` is not read at all (verified against real git),
        so the per-worktree location that looks right silently does nothing. The
        file is local-only and never committed, which is exactly what it is for.

        Best-effort — a read-only ``.git`` yields noisier status output, not a
        failed workspace.
        """
        common = self.common_dir() or (self._root / ".git")
        target = common / "info" / "exclude"
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            missing = [p for p in patterns if p not in existing.splitlines()]
            if not missing:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            with target.open("a", encoding="utf-8") as handle:
                handle.write(prefix + "\n".join(missing) + "\n")
        except OSError as exc:
            logger.warning("git: could not update {}: {}", target, exc)

    @classmethod
    def global_config(cls) -> dict[str, str]:
        """The host's global git settings as a flat mapping. Best-effort, never raises.

        Read so ``CuratedGitConfig`` can forward an ALLOWLIST of them into a
        container — the host ``~/.gitconfig`` is never mounted, because it
        carries credential helpers and signing keys. Reading the whole file here
        and filtering there is the right split: the subprocess is a side effect
        and belongs in this module, the choice of what may cross is pure policy.

        A repeated multivar key keeps its LAST value, matching how git itself
        resolves a single-valued read.
        """
        result = cls(Path.cwd())._run(
            ["git", "config", "--global", "--list", "--null"], check=False
        )
        if result.returncode != 0:
            return {}
        entries: dict[str, str] = {}
        # `--null` separates ENTRIES with NUL and key from value with a newline,
        # so a value containing newlines (a multi-line editor command) parses
        # correctly where the default `key=value` line format would not.
        for record in result.stdout.split("\0"):
            if not record:
                continue
            key, _, value = record.partition("\n")
            entries[key.strip()] = value
        return entries

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

    @classmethod
    def _run(
        cls,
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

        Bounded by `TIMEOUT_SECONDS`, and a timeout is reported through the
        SAME two channels a non-zero exit is: `GitError` when `check=True`, a
        failed `CompletedProcess` when not. That asymmetry is the whole reason
        it is not simply left to raise `TimeoutExpired` — the `check=False`
        callers are peek/dashboard reads whose contract is "never raise", so a
        bare `TimeoutExpired` there would break exactly the render loops the
        `check=False` was chosen to protect. Exit code 124 is `timeout(1)`'s
        convention; nothing branches on it, it just must not be 0.
        """
        logger.debug("git: {} (cwd={})", " ".join(cmd), cwd)
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=cls.TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            message = (
                f"`{' '.join(cmd)}` timed out after {cls.TIMEOUT_SECONDS:g}s "
                "and was killed (cwd=" + str(cwd) + ")"
            )
            logger.warning("git: {}", message)
            if check:
                raise GitError(message) from None
            return subprocess.CompletedProcess(cmd, 124, "", message)
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
