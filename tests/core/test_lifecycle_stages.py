"""#969: a lifecycle verb records its stages, unwinds what began, and is finished after a crash."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from grove.core import paths
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.lifecycle_stages import Stages
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


def test_unwind_runs_inverses_newest_first_and_reports_what_stayed(tmp_path: Path) -> None:
    stages = Stages("create", tmp_path / "p.json")
    for name in ("record", "worktree", "session"):
        stages.enter(name)
    ran: list[str] = []

    def fail() -> None:
        ran.append("worktree")
        raise OSError("busy")

    residue = stages.unwind(
        {
            "record": lambda: ran.append("record"),
            "worktree": fail,
            "session": lambda: ran.append("session"),
        }
    )

    assert ran == ["session", "worktree", "record"]
    assert list(residue) == ["worktree"]


@pytest.fixture
def registry(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> RepoRegistry:
    del fake_tmux
    cfg = GroveConfig.model_validate({"worktree": {"root_template": str(tmp_path / "trees")}})
    return RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json"))


def _orphan_create(registry: RepoRegistry, tmp_repo: Path, entered: list[str]) -> str:
    """A real workspace whose progress file says a create died after *entered*."""
    state = registry.get(tmp_repo).create(CreateWorkspaceRequest(agent_name="claude", title="x"))
    stages = Stages("create", Stages.path_for(state.id))
    for name in entered:
        stages.enter(name)
    return state.id


def test_start_unwinds_a_create_whose_process_died(registry: RepoRegistry, tmp_repo: Path) -> None:
    ws = _orphan_create(registry, tmp_repo, ["record", "worktree", "session"])
    worktree = Path(registry.get(tmp_repo).store.get(ws).worktree_path)

    assert registry.resolve_interrupted() == [ws]

    assert [s.id for s in registry.workspace_states()] == []
    assert not worktree.exists()
    assert not Stages.path_for(ws).exists()


def test_start_leaves_a_create_still_running_elsewhere_alone(
    registry: RepoRegistry, tmp_repo: Path
) -> None:
    """The incident's own input: the progress file exists AND its lock is held."""
    ws = _orphan_create(registry, tmp_repo, ["record"])
    lock = Stages.path_for(ws).with_name(Stages.path_for(ws).name + ".lock")
    paths.ensure_dir(lock.parent)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    # A separate open file description: flock excludes it as another process would.
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert registry.resolve_interrupted() == []
        assert [s.id for s in registry.workspace_states()] == [ws]
    finally:
        os.close(fd)


def test_a_completed_create_and_kill_leave_no_progress_file(
    registry: RepoRegistry, tmp_repo: Path
) -> None:
    manager = registry.get(tmp_repo)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="x"))
    assert not Stages.path_for(state.id).exists()
    manager.kill(state.id)
    assert not Stages.path_for(state.id).exists()
    assert Stages.interrupted() == []
