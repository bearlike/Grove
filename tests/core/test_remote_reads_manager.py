"""The manager reads a REMOTE workspace through its worker, and unreachable is never empty."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.errors import PaneNotFound
from grove.core.manager import WorkspaceManager
from grove.core.native_owners import WorktreeRead
from grove.core.runtimes import RemoteHandle
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

_WS = "r" * 32


class Worker:
    """The daemon's steer client, as the manager sees it: an answer per read."""

    def __init__(self, tail: WorktreeRead) -> None:
        self.tail = tail
        self.argvs: list[list[str]] = []

    def run_in_worktree(
        self, state: WorkspaceState, argv: Sequence[str], timeout: float
    ) -> WorktreeRead:
        self.argvs.append(list(argv))
        return WorktreeRead(0, " M a.py\n")

    def transcript_tail(self, state: WorkspaceState, lines: int, timeout: float) -> WorktreeRead:
        return self.tail


def _manager(tmp_path: Path, worker: Worker) -> WorkspaceManager:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    now = datetime.now(UTC)
    store.save(
        WorkspaceState(
            id=_WS,
            title="remote",
            repo_root=str(tmp_path),
            branch="grove/r",
            base_branch="main",
            worktree_path="/remote/worktree",
            tmux_session="",
            agent_name="claude",
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
            runtime=Runtime.REMOTE,
            remote=RemoteHandle("gcp", "exec-1"),
        )
    )
    return WorkspaceManager(
        repo_root=tmp_path,
        cfg=GroveConfig(),
        store=store,
        native_steer=worker,  # type: ignore[arg-type]
    )


def test_a_remote_transcript_tail_comes_from_the_worker(tmp_path: Path) -> None:
    manager = _manager(tmp_path, Worker(WorktreeRead(0, "last\n")))
    assert manager.transcript_tail(_WS, 5) == "last\n"


def test_an_unreadable_remote_transcript_raises_rather_than_reading_empty(tmp_path: Path) -> None:
    manager = _manager(tmp_path, Worker(WorktreeRead(-1, "transcript unreadable")))
    with pytest.raises(PaneNotFound):
        manager.transcript_tail(_WS, 5)


def test_remote_git_reads_go_to_the_worker(tmp_path: Path) -> None:
    worker = Worker(WorktreeRead(0, ""))
    manager = _manager(tmp_path, worker)
    state = manager._store.get(_WS)
    assert manager.git_for(state).dirty_file_count(Path(state.worktree_path)) == 1
    assert worker.argvs == [["git", "--no-optional-locks", "status", "--porcelain"]]
    assert manager.git_for(replace(state, runtime=Runtime.HOST)) is manager._git
