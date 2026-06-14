"""Self-paced live-pane SSE producer for one workspace (#19).

This is the *streaming* half of the live pane-preview wall: the dashboard's
focused card upgrades from a 1 s ``GET .../pane`` poll to a server push. It is
deliberately NOT routed through ``_sse.py``'s ``_SseHub`` — that hub fans ONE
cross-project activity stream out to many clients at a ~2 s cadence, whereas a
pane stream is **per-workspace**, **per-focus**, and **~1 Hz**. Muxing the two
into one queue would let pane frames (10x the cadence) dominate the bounded
queue and starve the activity deltas the bound was sized for. So each focused
pane gets its own self-paced producer instead.

The visibility/activity gate the issue calls for lives on the *client*: it
opens this stream only for the single focused, WORKING card (it already holds
each session's ``AgentActivityState`` from the activity stream — recomputing the
blend per pane-tick here would duplicate that policy). Off-screen and idle panes
"cost nothing" because the client never opens a stream for them; a stream that
*is* open self-throttles to its own clock and back-pressures on its own write,
so a slow client only slows its own capture loop — never the engine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime

from grove.core.contracts.activity import DashboardEvent
from grove.core.contracts.views import WorkspacePaneView

# One capture per second — matches the cadence the focused-pane poll shipped at
# (the issue's "~1-2 Hz") and the captured depth the manager already bounds via
# ``cfg.tmux.peek_history_lines``. Not config: there is no daemon-side pane knob,
# and the one tunable that matters (capture depth) lives on the manager.
_PANE_STREAM_INTERVAL_SECONDS = 1.0

# Capture callback: returns ``(ansi, taken_at)`` — exactly ``peek_pane``'s shape.
# Awaitable so the route can hand off the blocking tmux read to an executor while
# the producer stays pure orchestration (side effects at the edge).
PaneCapture = Callable[[], Awaitable[tuple[str | None, datetime | None]]]

# Sentinel distinct from any capture result (including ``None`` for an idle pane),
# so the very first tick always emits a frame — even when the pane starts empty,
# the client needs one frame to drop its "connecting…" state.
_UNSET: object = object()


class _PaneStreamer:
    """Emits ``pane_snapshot`` events for one workspace until the client leaves.

    Atomic over its own state: the workspace id, the injected capture + seq +
    sleep seams, and the last ANSI it pushed (the diff guard). ``events()`` is an
    infinite async generator; the route disposes it by cancellation when the
    client disconnects (Starlette's own disconnect watcher), exactly like the
    ``/events`` stream — no manual ``is_disconnected`` poll.
    """

    def __init__(
        self,
        *,
        workspace_id: str,
        capture: PaneCapture,
        next_seq: Callable[[], int],
        interval: float = _PANE_STREAM_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._workspace_id = workspace_id
        self._capture = capture
        self._next_seq = next_seq
        self._interval = interval
        self._sleep = sleep
        self._last_ansi: str | None | object = _UNSET

    async def events(self) -> AsyncGenerator[DashboardEvent | None, None]:
        """Yield a ``pane_snapshot`` event when the pane changed, else ``None``.

        ``None`` is the keepalive beat: an unchanged pane (a quiet agent, or one
        the client keeps focused while it thinks) ships zero payload, only a
        comment line the route emits to keep proxies and the browser warm — so a
        static pane streams next to nothing while staying live.
        """
        while True:
            ansi, taken_at = await self._capture()
            if ansi != self._last_ansi:
                self._last_ansi = ansi
                yield DashboardEvent.pane_event(
                    WorkspacePaneView.from_capture(self._workspace_id, ansi, taken_at),
                    seq=self._next_seq(),
                )
            else:
                yield None
            await self._sleep(self._interval)
