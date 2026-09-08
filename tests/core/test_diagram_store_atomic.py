"""Diagram descriptor writes preserve concurrent workspace state changes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from grove.core.contracts.diagrams import DiagramSessionView
from grove.core.errors import DiagramConflict
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(diagram: DiagramSessionView | None = None) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id="a" * 32,
        title="original",
        repo_root="/repo",
        branch="main",
        base_branch="main",
        worktree_path="/repo/worktree",
        tmux_session="diagram",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        diagram=diagram,
    )


def _diagram(mode: Literal["active", "read_only"] = "active") -> DiagramSessionView:
    return DiagramSessionView(path="architecture.drawio", session_id="b" * 32, mode=mode)


@pytest.fixture
def store(tmp_path: Path) -> JsonWorkspaceStore:
    return JsonWorkspaceStore(path=tmp_path / "state.json")


def test_stale_metadata_save_cannot_revive_stopped_diagram(store: JsonWorkspaceStore) -> None:
    active = _diagram()
    store.save(_state(active))
    stale_metadata = store.get("a" * 32)
    stopped = _diagram("read_only")
    store.update_diagram_descriptor("a" * 32, expected=active, replacement=stopped)

    store.save(replace(stale_metadata, title="renamed"))

    saved = store.get("a" * 32)
    assert saved.title == "renamed"
    assert saved.diagram == stopped


def test_stale_metadata_save_cannot_erase_opened_diagram(store: JsonWorkspaceStore) -> None:
    store.save(_state())
    stale_metadata = store.get("a" * 32)
    opened = _diagram()
    store.update_diagram_descriptor("a" * 32, expected=None, replacement=opened)

    store.save(replace(stale_metadata, title="renamed"))

    saved = store.get("a" * 32)
    assert saved.title == "renamed"
    assert saved.diagram == opened


def test_diagram_update_preserves_newer_metadata(store: JsonWorkspaceStore) -> None:
    active = _diagram()
    store.save(_state(active))
    store.save(replace(store.get("a" * 32), title="renamed"))
    stopped = _diagram("read_only")

    store.update_diagram_descriptor("a" * 32, expected=active, replacement=stopped)

    saved = store.get("a" * 32)
    assert saved.title == "renamed"
    assert saved.diagram == stopped


def test_diagram_update_requires_expected_descriptor(store: JsonWorkspaceStore) -> None:
    active = _diagram()
    store.save(_state(active))

    with pytest.raises(DiagramConflict, match="descriptor changed"):
        store.update_diagram_descriptor("a" * 32, expected=None, replacement=_diagram("read_only"))
