"""JsonWorkspaceStore: persistence + repo-scoped filtering."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.errors import WorkspaceNotFound
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(workspace_id: str, repo_root: str, *, title: str = "t") -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=title,
        repo_root=repo_root,
        branch=f"grove/{workspace_id}",
        base_branch="HEAD",
        worktree_path=f"/wt/{workspace_id}",
        tmux_session=f"grove-{workspace_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    s = _state("a", "/repo/x")
    store.save(s)
    out = store.load_all()
    assert len(out) == 1
    assert out[0].id == "a"
    assert out[0].repo_root == "/repo/x"
    assert out[0].status == WorkspaceStatus.RUNNING


def test_for_repo_filters_by_canonical_path(tmp_path: Path) -> None:
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("1", str(repo_a.resolve())))
    store.save(_state("2", str(repo_a.resolve())))
    store.save(_state("3", str(repo_b.resolve())))

    a_only = {s.id for s in store.for_repo(repo_a)}
    b_only = {s.id for s in store.for_repo(repo_b)}
    assert a_only == {"1", "2"}
    assert b_only == {"3"}


def test_get_raises_for_unknown_id(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    with pytest.raises(WorkspaceNotFound):
        store.get("does-not-exist")


def test_delete_removes_record(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("a", "/x"))
    store.save(_state("b", "/x"))
    store.delete("a")
    remaining = {s.id for s in store.load_all()}
    assert remaining == {"b"}


def test_save_replaces_by_id(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    s = _state("a", "/x", title="first")
    store.save(s)
    s2 = _state("a", "/x", title="second")
    store.save(s2)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].title == "second"
