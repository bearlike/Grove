"""WorkspaceManager.commits + GitRepo.branch_commits — comprehensive log.

Pins the contract that detail-page consumers depend on: every commit
done in the workspace since fork from base, newest first, uncapped.
Distinct from peek.recent_commits (rail-shaped, no fork-point filter,
limit 3) — both are tested in test_peek.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


@pytest.fixture
def manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-", "agent_window_name": "agent"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout


# ─── GitRepo.branch_commits (the side-effect surface) ─────────────────────


def test_branch_commits_empty_when_branch_equals_base(tmp_repo: Path) -> None:
    """A branch that hasn't diverged returns no commits — `git log base..base`."""
    git = GitRepo(tmp_repo)
    assert git.branch_commits("main", "main") == ()


def test_branch_commits_returns_only_diverged_commits(tmp_repo: Path) -> None:
    """`git log base..branch` excludes pre-fork commits.

    The TUI's ``recent_commits`` walks all of branch history; this method
    is the comprehensive view scoped to "what was done in this workspace".
    """
    _git(tmp_repo, "checkout", "-b", "feat/x")
    for i in range(4):
        (tmp_repo / f"f{i}").write_text(str(i), encoding="utf-8")
        _git(tmp_repo, "add", ".")
        _git(tmp_repo, "commit", "-m", f"step {i}", "--no-verify")

    git = GitRepo(tmp_repo)
    commits = git.branch_commits("feat/x", "main")
    subjects = [c.subject for c in commits]
    # newest first; the pre-fork "init" commit is excluded by the range.
    assert subjects == ["step 3", "step 2", "step 1", "step 0"]
    # Each entry has a non-empty short sha and a parsed datetime.
    for c in commits:
        assert len(c.sha) >= 7
        assert c.committed_at is not None


def test_branch_commits_respects_limit(tmp_repo: Path) -> None:
    """`limit` caps the returned tuple — used by future rail-style consumers."""
    _git(tmp_repo, "checkout", "-b", "feat/y")
    for i in range(5):
        (tmp_repo / f"f{i}").write_text(str(i), encoding="utf-8")
        _git(tmp_repo, "add", ".")
        _git(tmp_repo, "commit", "-m", f"step {i}", "--no-verify")

    git = GitRepo(tmp_repo)
    commits = git.branch_commits("feat/y", "main", limit=2)
    assert [c.subject for c in commits] == ["step 4", "step 3"]


def test_branch_commits_returns_empty_on_invalid_range(tmp_repo: Path) -> None:
    """Unknown ref → empty tuple, never raise. Best-effort like peek."""
    git = GitRepo(tmp_repo)
    assert git.branch_commits("does-not-exist", "main") == ()


# ─── WorkspaceManager.commits (the orchestration surface) ─────────────────


def test_manager_commits_returns_full_workspace_log(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """End-to-end: create a workspace, make N commits, every one shows up."""
    del tmp_repo
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="commits-full"))
    worktree = Path(state.worktree_path)
    for i in range(7):
        (worktree / f"f{i}").write_text(str(i), encoding="utf-8")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", f"step {i}", "--no-verify")

    commits = manager.commits(state.id)
    subjects = [c.subject for c in commits]
    assert subjects == [f"step {i}" for i in range(6, -1, -1)]


def test_manager_commits_empty_for_freshly_created_workspace(
    manager: WorkspaceManager,
) -> None:
    """A workspace whose branch hasn't diverged from base returns ()."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="commits-empty"))
    assert manager.commits(state.id) == ()


def test_manager_commits_does_not_raise_on_unknown_workspace(
    manager: WorkspaceManager,
) -> None:
    """Unknown workspace id raises WorkspaceNotFound (lookup), but a
    deleted-on-disk branch should degrade to ``()`` not crash — peek's
    never-raise contract for read paths applies here too."""
    # Same path as peek: degrades when git fails, not when the workspace
    # record is missing — that's a 404.
    from grove.core.errors import WorkspaceNotFound  # noqa: PLC0415

    with pytest.raises(WorkspaceNotFound):
        manager.commits("does-not-exist")
