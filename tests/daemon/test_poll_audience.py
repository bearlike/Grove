"""The activity poll must not work for an empty room, and must wake promptly.

``poll_once()`` costs several git forks plus a transcript parse per workspace,
every 2 s, forever. With nothing consuming the deltas that is pure waste on an
idle host — so the loop parks on an edge (a consumer arriving) rather than
ticking into nothing.

The lock registry's leak property is pinned here too: a lock map keyed by
workspace id would otherwise grow one entry per id the daemon ever touched.
"""

from __future__ import annotations

import asyncio

import pytest

from grove.daemon._audience import _PollAudience
from grove.daemon._lifecycle import _KeyedLocks
from grove.daemon.app import _poll_loop

# Far longer than any test runs: a tick that happens under this interval can only
# have come from the audience gate releasing, never from the timer coming round.
_NEVER_INTERVAL_S = 3600.0
_WAKE_TIMEOUT_S = 5.0
# Loop passes given to a poll that must NOT happen. Without the gate the very
# first iteration polls, so this is generous by a wide margin.
_IDLE_LOOP_PASSES = 20


class _CountingCoalescer:
    """Stands in for ``_PollCoalescer`` — same ``run()`` shape, counts calls."""

    def __init__(self) -> None:
        self.runs = 0
        self.ran = asyncio.Event()

    async def run(self) -> None:
        self.runs += 1
        self.ran.set()


async def _drain_loop() -> None:
    for _ in range(_IDLE_LOOP_PASSES):
        await asyncio.sleep(0)


def test_audience_counts_rather_than_flags() -> None:
    """The LAST consumer to leave closes the room, not the first.

    A bare boolean would have one browser tab closing silence the poll for the
    other three that are still streaming.
    """
    audience = _PollAudience()
    assert not audience.occupied
    audience.join()
    audience.join()
    assert audience.occupied
    audience.leave()
    assert audience.occupied, "one of two consumers left and the room went quiet"
    audience.leave()
    assert not audience.occupied
    # An unbalanced leave must not drive the count negative — that would wedge
    # the poll off for the next consumer that joins.
    audience.leave()
    audience.join()
    assert audience.occupied


async def test_poll_does_nothing_with_no_consumers() -> None:
    """No subscriber, no work — not even the first tick."""
    audience = _PollAudience()
    coalescer = _CountingCoalescer()
    stop = asyncio.Event()
    task = asyncio.create_task(_poll_loop(coalescer, _NEVER_INTERVAL_S, stop, audience))  # type: ignore[arg-type]
    try:
        await _drain_loop()
        assert coalescer.runs == 0
    finally:
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_poll_wakes_immediately_when_a_consumer_arrives() -> None:
    """The first tick after a subscriber arrives is prompt, not an interval away.

    A user opening the dashboard must not wait out the poll interval for their
    first frame — hence gating BEFORE the tick rather than after the sleep.
    """
    audience = _PollAudience()
    coalescer = _CountingCoalescer()
    stop = asyncio.Event()
    task = asyncio.create_task(_poll_loop(coalescer, _NEVER_INTERVAL_S, stop, audience))  # type: ignore[arg-type]
    try:
        await _drain_loop()
        assert coalescer.runs == 0
        audience.join()
        # The interval is an hour, so this can only be the gate releasing.
        await asyncio.wait_for(coalescer.ran.wait(), timeout=_WAKE_TIMEOUT_S)
        assert coalescer.runs == 1
    finally:
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_keyed_locks_leave_no_entry_behind() -> None:
    """The lock map must not retain an entry per workspace id ever seen.

    Workspaces are created and destroyed for the life of the daemon, so an
    un-reaped entry per id is an unbounded leak in a process that runs for weeks.
    """
    locks = _KeyedLocks()
    async with locks.hold("ws-a"):
        assert len(locks) == 1
    assert len(locks) == 0

    # A contended key keeps exactly ONE shared entry while both need it, and
    # still drops to zero once both are done.
    inner_seen: list[int] = []

    async def _hold() -> None:
        async with locks.hold("ws-b"):
            inner_seen.append(len(locks))

    await asyncio.gather(_hold(), _hold())
    assert inner_seen == [1, 1]
    assert len(locks) == 0

    # `None` — the create case — takes no lock and creates no entry.
    async with locks.hold(None):
        assert len(locks) == 0
