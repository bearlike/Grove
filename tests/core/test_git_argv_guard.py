"""`GitRepo` refuses a ref git would parse as a flag.

The second of two layers. The contract
(`contracts/branch_plan.BRANCH_NAME_PATTERN`) validates intent at the boundary;
this module pins the half that keeps that guarantee — a call site that
bypasses the contract, or a future variant that forgets the pattern, still
cannot hand git an argv it would misparse.

Every test here runs against a REAL throwaway repo and asserts two things: the
call was refused, **and no git command ran**. The second is the one that
matters. Asserting a raised error alone would pass equally well against a
version that destroys a branch and then complains: `git worktree add -b -D
<path> feature/x` prints `Deleted branch feature/x` and *then* exits `fatal:`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core import git as git_mod
from grove.core.errors import GitError
from grove.core.git import GitRepo


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo with a second branch to serve as the victim."""
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "init", "-q", "-b", "main")
    _run(root, "config", "user.email", "t@t")
    _run(root, "config", "user.name", "t")
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    _run(root, "add", "-A")
    _run(root, "commit", "-qm", "init")
    _run(root, "branch", "feature/x")
    return root


def _snapshot(root: Path) -> tuple[list[str], str]:
    branches = _run(root, "branch", "--format=%(refname:short)").split()
    return branches, _run(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("label", "call"),
    [
        ("worktree_add -b", lambda r, p: r.worktree_add(p, new_branch="-D", base="feature/x")),
        ("worktree_add base", lambda r, p: r.worktree_add(p, new_branch="ok", base="-x/y")),
        ("worktree_add checkout", lambda r, p: r.worktree_add(p, existing_branch="-m")),
        ("branch_delete", lambda r, _p: r.branch_delete("-D")),
        ("branch_set_upstream", lambda r, _p: r.branch_set_upstream("-m", "origin/main")),
    ],
)
def test_a_flaglike_ref_is_refused_before_any_git_runs(
    label: str,
    call,
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused, nothing executed, repository provably untouched."""
    before = _snapshot(repo)
    executed: list[list[str]] = []
    real = subprocess.run

    def _spy(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        executed.append(list(cmd))
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(git_mod.subprocess, "run", _spy)

    with pytest.raises(GitError, match="parsed as a command-line flag"):
        call(GitRepo(repo), tmp_path / "wt")

    assert executed == [], f"{label} shelled out to git: {executed}"
    assert _snapshot(repo) == before


def test_the_guard_does_not_block_ordinary_refs(repo: Path, tmp_path: Path) -> None:
    """The guard is about a leading dash only — a ref containing one is fine,
    and so is every ordinary branch name."""
    worktree = tmp_path / "wt-ok"

    GitRepo(repo).worktree_add(worktree, new_branch="feat/my-branch", base="main")

    assert worktree.is_dir()
    assert "feat/my-branch" in _run(repo, "branch", "--format=%(refname:short)").split()


def test_the_unfixed_argv_really_was_destructive(repo: Path, tmp_path: Path) -> None:
    """Pins the vulnerability itself, so the guard can never be removed as
    unnecessary.

    Runs the exact argv the pre-fix code built, bypassing `GitRepo` entirely.
    If git ever stops treating the `-b` value as its own flags this test fails
    and the guard's justification can be revisited deliberately — rather than
    the guard being deleted on an assumption about what git does.
    """
    assert "feature/x" in _run(repo, "branch", "--format=%(refname:short)").split()

    subprocess.run(
        ["git", "worktree", "add", "-b", "-D", str(tmp_path / "wt"), "feature/x"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert "feature/x" not in _run(repo, "branch", "--format=%(refname:short)").split()
