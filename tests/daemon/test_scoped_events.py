"""A public subscriber can observe one capability, never the fleet payload."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.activity import DashboardDelta
from grove.daemon._scoped_events import ScopedWorkspaceEvents
from tests.core.test_activity_projection import ProjectionSource, row


@pytest.mark.asyncio
async def test_public_changes_are_scoped_and_carry_no_private_payload(tmp_path: Path) -> None:
    activity = ProjectionSource(tmp_path)
    state = row(tmp_path, "one").state
    source = ScopedWorkspaceEvents(activity, state, lambda: state)
    events = source.events()
    assert await anext(events) == "event: snapshot\ndata: {}\n\n"
    pending = asyncio.create_task(anext(events))
    activity._emit(
        DashboardDelta(kind="workspace_changed", seq=2, workspace_id="two", repo_root="private")
    )
    await asyncio.sleep(0)
    assert not pending.done()
    activity._emit(
        DashboardDelta(kind="session_activity", seq=3, workspace_id="one", repo_root="private")
    )
    assert await asyncio.wait_for(pending, timeout=1) == "event: changed\ndata: {}\n\n"
    await events.aclose()


@pytest.mark.asyncio
async def test_public_revocation_is_checked_before_change_publication(tmp_path: Path) -> None:
    activity = ProjectionSource(tmp_path)
    state = row(tmp_path, "one").state
    allowed = [True]

    def authorize():
        if not allowed[0]:
            raise PermissionError("revoked")
        return state

    events = ScopedWorkspaceEvents(activity, state, authorize).events()
    await anext(events)
    allowed[0] = False
    activity._emit(DashboardDelta(kind="workspace_changed", seq=2, workspace_id="one"))
    with pytest.raises(PermissionError):
        await asyncio.wait_for(anext(events), timeout=1)
    assert activity._subs == []


@pytest.mark.asyncio
async def test_public_expiry_ends_without_another_discovery_read(tmp_path: Path) -> None:
    activity = ProjectionSource(tmp_path)
    state = row(tmp_path, "one").state
    state.share_expires_at = datetime.now(UTC) + timedelta(milliseconds=20)
    calls = []

    def authorize():
        calls.append(1)
        return state

    events = ScopedWorkspaceEvents(activity, state, authorize).events()
    await anext(events)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(events), timeout=1)
    assert calls == [1]
