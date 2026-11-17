"""Daemon fan-out budgets: bounded intake is independent per slow client."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.config import GroveConfig
from grove.core.contracts.activity import DashboardEvent
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.daemon import _sse
from grove.daemon._audience import _PollAudience
from grove.daemon._sse import _SseHub


def _hub(tmp_path: Path) -> tuple[_SseHub, ActivityService, _PollAudience]:
    service = ActivityService(
        registry=RepoRegistry(
            cfg=GroveConfig.model_validate({"auth": {"enabled": False}}),
            store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        )
    )
    audience = _PollAudience()
    return _SseHub(service, queue_size=2, ring_size=8, audience=audience), service, audience


def _delta(index: int) -> DashboardDelta:
    return DashboardDelta(kind="workspace_changed", seq=index, workspace_id=f"w-{index}")


@pytest.mark.asyncio
async def test_slow_client_intake_stays_within_item_and_byte_budget(tmp_path: Path) -> None:
    """A producer burst cannot retain more than the configured global intake budget."""
    hub, service, audience = _hub(tmp_path)
    hub.start(asyncio.get_running_loop())
    client = hub.register()
    try:
        for index in range(100):
            hub._on_delta(_delta(index))
        stats = hub._intake.stats()
        assert stats.items <= 2
        assert stats.bytes <= 16 * 1024 * 1024
        assert stats.rejected > 0
        assert len(audience) == 1
        assert client.qsize() == 0  # slow delivery has not run yet
    finally:
        hub.stop()
        service.close()
    assert len(audience) == 0


@pytest.mark.asyncio
async def test_slow_client_is_resnapshotted_without_blocking_fast_client(tmp_path: Path) -> None:
    """One full connection receives a snapshot; another keeps receiving fresh events."""
    hub, service, audience = _hub(tmp_path)
    hub.start(asyncio.get_running_loop())
    slow, fast = hub.register(), hub.register()
    try:
        hub._publish(DashboardEvent.from_delta(_delta(1)))
        assert fast.get_nowait().seq == 1
        hub._publish(DashboardEvent.from_delta(_delta(2)))
        assert fast.get_nowait().seq == 2
        hub._publish(DashboardEvent.from_delta(_delta(3)))
        assert slow.qsize() == 1
        assert slow.get_nowait().kind == "snapshot"
        assert fast.get_nowait().seq == 3
        assert len(audience) == 2
    finally:
        hub.unregister(slow)
        hub.unregister(slow)
        hub.unregister(fast)
        hub.stop()
        service.close()
    assert len(audience) == 0


def test_unsubscribe_and_close_release_each_client_once(tmp_path: Path) -> None:
    """Connection teardown is idempotent, so dead clients cannot leak polling demand."""
    hub, service, audience = _hub(tmp_path)
    first, second = hub.register(), hub.register()
    assert len(audience) == 2

    hub.unregister(first)
    hub.unregister(first)
    assert len(audience) == 1
    hub.stop()

    assert len(audience) == 0
    hub.unregister(second)
    assert len(audience) == 0
    service.close()


def test_replay_ring_is_bounded_by_bytes_as_well_as_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A count alone is no bound: one row measured 2.67 MB (2026-10-29), so a
    512-deep ring of those retains over a gigabyte for a replay nobody asks
    for. Under a byte budget the ring drops its OLDEST events first and
    ``can_replay`` answers honestly for what it dropped."""
    hub, service, _ = _hub(tmp_path)
    payload = {"filler": "x" * 2_000}
    one = len(
        DashboardEvent.from_delta(
            DashboardDelta(kind="workspace_changed", seq=0, workspace_id="w", detail=payload)
        )
        .model_dump_json()
        .encode()
    )
    # Room for exactly three events; the ring's count bound (8) is not reached.
    monkeypatch.setattr(_sse, "_RING_MAX_BYTES", one * 3)
    try:
        for index in range(6):
            hub._publish(
                DashboardEvent.from_delta(
                    DashboardDelta(
                        kind="workspace_changed", seq=index, workspace_id="w", detail=payload
                    )
                )
            )
        assert [event.seq for event in hub._ring] == [3, 4, 5]
        assert hub._ring_bytes == one * 3
        assert hub.can_replay(3)
        assert not hub.can_replay(1)
        hub._clear_ring()
        assert hub._ring_bytes == 0 and not hub._ring
    finally:
        service.close()
