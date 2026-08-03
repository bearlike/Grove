"""Single-flight guard around a slow, non-reentrant-safe sync callable.

``ActivityService.poll_once()`` has two independent triggers on the daemon: the
lifespan's own ~2s timer (`_poll_loop`) and every native Claude Code hook event
(`POST /hooks/agent-events` — no debounce, up to a dozen event types per
tool call across a fleet). Without a guard between them, a burst of hook events
would each schedule their OWN full-fleet `poll_once()` onto the executor thread
pool, independently of the timer and of each other — several concurrent
full-fleet scans at once is a much closer match for "multiple cores pegged"
than the timer's single serialized 2s loop.

``_PollCoalescer`` fixes that without touching either trigger's intent: a caller
arriving while a run is already in flight awaits that SAME run instead of
starting its own, so a whole burst collapses to at most one run in flight plus
one queued-by-arrival-order continuation — never N concurrent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class _PollCoalescer:
    """Wraps a blocking ``fn`` so concurrent ``run()`` callers share one executor call."""

    def __init__(self, fn: Callable[[], None]) -> None:
        self._fn = fn
        self._inflight: asyncio.Future[None] | None = None

    async def run(self) -> None:
        """Run ``fn`` in the default executor, or join the run already in flight.

        The check-and-set below has no ``await`` between reading and writing
        ``self._inflight``, so it can't race another ``run()`` call — asyncio
        callbacks never interleave mid-statement on a single-threaded loop.
        """
        inflight = self._inflight
        if inflight is not None:
            await asyncio.shield(inflight)
            return
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, self._fn)
        self._inflight = fut
        try:
            await fut
        finally:
            self._inflight = None
