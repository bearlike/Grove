"""Server-Sent-Events fan-out for the activity stream.

One ``_SseHub`` bridges the **synchronous** ``ActivityService`` delta bus to the
**async** world of many concurrent ``GET /events`` connections. The service emits
``DashboardDelta`` from whichever thread did the work (a ``poll_once`` running in
an executor, or an HTTP handler on the loop); the hub marshals each one onto the
event loop with ``call_soon_threadsafe`` and fans it out to every connected
client's bounded queue.

Two invariants keep a slow browser from ever hurting the engine (CLAUDE.md's
"best-effort side effects isolate their failures"):

- **Per-connection queues are bounded and drop-oldest.** A client that can't keep
  up loses its stalest buffered event, never back-pressures the publisher.
- **A shared ring buffer holds the last N events** so a brief reconnect can replay
  from ``Last-Event-ID`` instead of re-downloading a full snapshot.

We deliberately format SSE frames by hand with a ``StreamingResponse`` rather than
add ``sse-starlette``: the dependency lockfile can't be regenerated in this
environment (the docs wheel is unreachable), and the framing is a few lines. The
hub owns no formatting — it traffics ``DashboardEvent`` objects; the route turns
them into wire frames.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.contracts.activity import DashboardEvent
from grove.daemon._audience import _PollAudience

# Per-connection queue depth. Generous — one event per workspace per tick is tiny;
# the bound only matters for a wedged client, where drop-oldest kicks in.
_QUEUE_SIZE = 256
# Shared replay window. Covers a reconnect gap of this many events before we fall
# back to a full snapshot.
_RING_SIZE = 512


class _SseHub:
    """Fan-out + replay buffer for one ``ActivityService``'s delta stream."""

    def __init__(
        self,
        service: ActivityService,
        *,
        queue_size: int = _QUEUE_SIZE,
        ring_size: int = _RING_SIZE,
        audience: _PollAudience | None = None,
    ) -> None:
        self._service = service
        # Each live connection is one activity-poll consumer. Injected so the
        # daemon's always-on consumers (notification broker, status publisher)
        # share the same room; a hub built without one still counts, it just
        # gates nothing else.
        self._audience = audience if audience is not None else _PollAudience()
        self._queue_size = queue_size
        self._ring: deque[DashboardEvent] = deque(maxlen=ring_size)
        self._queues: set[asyncio.Queue[DashboardEvent]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsub: Callable[[], None] | None = None

    # ─── lifecycle ─────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the loop and subscribe to the service. Call once, at lifespan-entry."""
        self._loop = loop
        self._unsub = self._service.subscribe(self._on_delta)

    def stop(self) -> None:
        """Unsubscribe and forget all state. Call at lifespan-exit."""
        if self._unsub is not None:
            self._unsub()
        self._unsub = None
        for queue in list(self._queues):
            self.unregister(queue)
        self._ring.clear()

    # ─── per-connection registration ───────────────────────────────────────

    def register(self) -> asyncio.Queue[DashboardEvent]:
        queue: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._queues.add(queue)
        self._audience.join()
        return queue

    def unregister(self, queue: asyncio.Queue[DashboardEvent]) -> None:
        # `discard` is idempotent but `leave()` is not, so the count only moves
        # when a queue was actually removed — otherwise a double-unregister on a
        # torn-down connection would evict a consumer that is still connected.
        if queue in self._queues:
            self._queues.discard(queue)
            self._audience.leave()

    # ─── replay ────────────────────────────────────────────────────────────

    def can_replay(self, last_event_id: int) -> bool:
        """True if the ring still holds the event right after ``last_event_id``.

        When the gap is wider than the buffer (or the buffer is empty), the caller
        should send a fresh full snapshot instead of a partial replay.
        """
        return bool(self._ring) and self._ring[0].seq <= last_event_id + 1

    def replay_since(self, last_event_id: int) -> list[DashboardEvent]:
        return [event for event in self._ring if event.seq > last_event_id]

    # ─── publish (loop thread) ─────────────────────────────────────────────

    def _on_delta(self, delta: DashboardDelta) -> None:
        """Subscriber callback — runs on whatever thread emitted the delta.

        Never touch the queues here: hop to the loop thread first so all queue
        mutation is single-threaded.
        """
        loop = self._loop
        if loop is None:
            return
        event = DashboardEvent.from_delta(delta)
        loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: DashboardEvent) -> None:
        self._ring.append(event)
        for queue in list(self._queues):
            _offer(queue, event)


def _offer(queue: asyncio.Queue[DashboardEvent], event: DashboardEvent) -> None:
    """Enqueue, dropping the oldest buffered event if the consumer is full.

    A wedged browser must never back-pressure the publisher, so we evict rather
    than block. The suppressions guard the unavoidable races between ``full()``
    and the put — losing the evict is fine, the next event tries again.
    """
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(event)
