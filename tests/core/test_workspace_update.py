"""WorkspaceManager.update() — metadata-only rename + optional description.

Pinned invariants:
- Renaming the title does NOT change the persisted worktree_path or
  tmux_session — the on-disk dir and live session keep the slug they were
  born with. (See ``CLAUDE.md`` "title is identity-seed-only at create".)
- Description round-trips through JSON store; legacy records without the
  field load with description=None.
- update emits an ``"updated"`` event with title_changed /
  description_changed flags, so the TUI's subscriber knows what shifted
  without diffing.
- ORPHANED workspaces refuse update (record is headed for kill).
- Calling update with no args, or with values identical to current
  state, refuses / no-ops as documented.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import WorkspaceStateError
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


@pytest.fixture
def manager(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[WorkspaceManager]:
    del fake_tmux
    cfg = GroveConfig()
    store = JsonWorkspaceStore()
    yield WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _create(mgr: WorkspaceManager, *, title: str = "task one") -> WorkspaceState:
    return mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title=title, repo_root=mgr.repo_root)
    )


def test_update_renames_title_only(manager: WorkspaceManager) -> None:
    state = _create(manager, title="initial title")
    initial_session = state.tmux_session
    initial_worktree = state.worktree_path

    updated = manager.update(state.id, title="renamed title")

    assert updated.title == "renamed title"
    # Identity stays — only the displayed title changes.
    assert updated.tmux_session == initial_session
    assert updated.worktree_path == initial_worktree
    assert updated.branch == state.branch
    assert updated.description is None


def test_update_sets_description(manager: WorkspaceManager) -> None:
    state = _create(manager)
    updated = manager.update(state.id, description="see ticket #1234")
    assert updated.description == "see ticket #1234"
    # Title is left alone.
    assert updated.title == state.title


def test_update_clears_description_with_empty_string(manager: WorkspaceManager) -> None:
    state = _create(manager)
    manager.update(state.id, description="something")
    cleared = manager.update(state.id, description="")
    assert cleared.description is None


def test_update_clears_description_with_none(manager: WorkspaceManager) -> None:
    state = _create(manager)
    manager.update(state.id, description="something")
    cleared = manager.update(state.id, description=None)
    assert cleared.description is None


def test_update_strips_whitespace(manager: WorkspaceManager) -> None:
    state = _create(manager)
    updated = manager.update(state.id, title="  renamed  ", description="  hello  ")
    assert updated.title == "renamed"
    assert updated.description == "hello"


def test_update_with_no_args_raises(manager: WorkspaceManager) -> None:
    state = _create(manager)
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id)


def test_update_rejects_empty_title(manager: WorkspaceManager) -> None:
    state = _create(manager)
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, title="")
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, title="   ")


def test_update_rejects_overlong_title(manager: WorkspaceManager) -> None:
    state = _create(manager)
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, title="x" * 121)


def test_update_rejects_overlong_description(manager: WorkspaceManager) -> None:
    state = _create(manager)
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, description="x" * 2001)


def test_update_orphaned_raises(manager: WorkspaceManager) -> None:
    state = _create(manager)
    # Make the worktree dir vanish so reconciliation flips status to ORPHANED.
    import shutil  # noqa: PLC0415

    shutil.rmtree(state.worktree_path)
    # Force a reconcile by reading through list().
    promoted = next(s for s in manager.list() if s.id == state.id)
    assert promoted.status == WorkspaceStatus.ORPHANED
    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, title="renamed")


def test_update_no_op_when_values_match_current(manager: WorkspaceManager) -> None:
    state = _create(manager, title="same")
    # Same title + no description set; should not bump updated_at.
    same = manager.update(state.id, title="same")
    assert same.title == "same"
    assert same.updated_at == state.updated_at


def test_update_emits_updated_event(manager: WorkspaceManager) -> None:
    state = _create(manager)
    captured: list[WorkspaceEvent] = []
    manager.subscribe(captured.append)
    manager.update(state.id, title="new title", description="hello")
    update_events = [e for e in captured if e.kind == "updated"]
    assert len(update_events) == 1
    event = update_events[0]
    assert event.workspace_id == state.id
    assert event.detail["title_changed"] == "true"
    assert event.detail["description_changed"] == "true"


def test_update_event_marks_only_changed_field(manager: WorkspaceManager) -> None:
    state = _create(manager)
    captured: list[WorkspaceEvent] = []
    manager.subscribe(captured.append)
    manager.update(state.id, description="just description")
    update_events = [e for e in captured if e.kind == "updated"]
    assert len(update_events) == 1
    assert update_events[0].detail["title_changed"] == "false"
    assert update_events[0].detail["description_changed"] == "true"


def test_update_persists_across_reload(manager: WorkspaceManager, tmp_repo: Path) -> None:
    state = _create(manager)
    manager.update(state.id, title="persisted name", description="persisted desc")
    # Re-read from a fresh store instance to prove disk round-trip.
    reloaded = JsonWorkspaceStore().get(state.id)
    assert reloaded.title == "persisted name"
    assert reloaded.description == "persisted desc"
    del tmp_repo


def test_update_does_not_touch_worktree_or_session(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    sessions_before = set(fake_tmux.sessions)
    layouts_before = list(fake_tmux.layouts)
    manager.update(state.id, title="renamed", description="annotated")
    assert fake_tmux.sessions == sessions_before
    assert fake_tmux.layouts == layouts_before


def test_legacy_state_without_description_loads_as_none(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """A state.json written before description existed must round-trip."""
    del fake_tmux
    cfg = GroveConfig()
    store = JsonWorkspaceStore()
    mgr = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)
    state = _create(mgr)
    # Surgically drop the field from the on-disk JSON to simulate a legacy
    # record. The store's _deserialize uses .get() so missing → None.
    import json  # noqa: PLC0415

    payload = json.loads(store.path.read_text())
    payload["workspaces"][state.id].pop("description", None)
    store.path.write_text(json.dumps(payload))
    reloaded = JsonWorkspaceStore().get(state.id)
    assert reloaded.description is None
    del tmp_state_dir
