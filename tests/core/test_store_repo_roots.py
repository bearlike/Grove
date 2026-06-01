"""Store enumerates distinct repo_root values for daemon multi-repo support."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(ws_id: str, repo_root: str) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root=repo_root,
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"{repo_root}/.grove/worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def test_list_repo_roots_empty(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(tmp_path / "state.json")
    assert store.list_repo_roots() == []


def test_list_repo_roots_distinct(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(tmp_path / "state.json")
    store.save(_state("a1", "/repos/foo"))
    store.save(_state("a2", "/repos/foo"))
    store.save(_state("b1", "/repos/bar"))
    roots = sorted(store.list_repo_roots())
    assert roots == [Path("/repos/bar"), Path("/repos/foo")]


def test_list_repo_roots_returns_paths_not_strings(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(tmp_path / "state.json")
    store.save(_state("a1", "/repos/foo"))
    [root] = store.list_repo_roots()
    assert isinstance(root, Path)
