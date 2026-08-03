"""Who is listening for activity deltas — the periodic poll waits when nobody is.

``ActivityService.poll_once()`` costs several git forks plus a transcript parse
*per workspace*, every 2 s, forever. On an idle host with nothing consuming the
result that is pure waste: the poll publishes into an empty room.

The room is NOT just the SSE hub. Three things consume the delta bus — the hub
(only while a browser or TUI holds a ``GET /events`` connection), the
notification broker, and the issue-ops status publisher — and the latter two are
precisely the consumers that matter when no dashboard is open, since a push
notification exists to reach a user who is *not* watching. So the gate counts
consumers, and the always-on ones join for the process's lifetime; an unwatched
daemon with notifications configured still polls, by design.

Edge, not flag: the poll ``await``s this instead of re-checking a boolean every
tick, so an empty room costs zero wakeups rather than a cheap-but-endless spin.
"""

from __future__ import annotations

import asyncio


class _PollAudience:
    """Reference-counted "someone wants activity deltas" gate.

    Counting rather than a bare flag is what makes ``leave()`` correct when
    several consumers overlap: the last one to leave closes the room, not the
    first. Mutations happen between awaits on the single-threaded loop, so the
    count needs no lock of its own.
    """

    def __init__(self) -> None:
        self._count = 0
        self._occupied = asyncio.Event()

    def __len__(self) -> int:
        """Current consumer count — what a gate test asserts on."""
        return self._count

    @property
    def occupied(self) -> bool:
        return self._occupied.is_set()

    def join(self) -> None:
        self._count += 1
        self._occupied.set()

    def leave(self) -> None:
        # Clamped: an unbalanced leave (a double-unregister on a torn-down
        # connection) must never drive the count negative and wedge the poll off
        # for a consumer that is still there.
        self._count = max(0, self._count - 1)
        if self._count == 0:
            self._occupied.clear()

    async def wait(self) -> None:
        """Return as soon as at least one consumer is present."""
        await self._occupied.wait()
