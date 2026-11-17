"""Runtime readiness for the launch surfaces: probed rarely, shared, never on the loop.

A launch preview is requested per keystroke of a create form, and deciding
whether a container create would run or fall back means the container-scoped
preflight checks (``docker info``, the devcontainer CLI and friends): a
handful of subprocesses. This memo answers the preview from one reading per
repo that is a few seconds old at most, and a burst of previews arriving while
a probe runs waits for that probe rather than starting its own.

It serves the preview only. ``create`` probes live through the same preflight
every time, so a stale reading can make a preview optimistic or pessimistic
for a few seconds and can never make a create skip its own check.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Literal

#: One reading's identity: the repo and which runtime the reading is about.
type _Key = tuple[Path, Literal["host", "container", "host_refusal"]]


class _ReadinessMemo:
    """Per-repo, per-runtime, time-bounded, single-flight readiness."""

    #: How old a reading may be. Docker readiness changes on a human timescale
    #: (an engine started, a CLI installed); a preview being wrong about it for
    #: this long is honest because the create re-checks.
    TTL_SECONDS: ClassVar[float] = 5.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._readings: dict[_Key, tuple[float, str | None]] = {}
        self._inflight: dict[_Key, asyncio.Future[str | None]] = {}

    async def reason(
        self,
        root: Path,
        probe: Callable[[], str | None],
        runtime: Literal["host", "container", "host_refusal"] = "container",
    ) -> str | None:
        """Why ``runtime`` is unavailable for ``root`` (``None`` when it is), probing when stale."""
        key: _Key = (root, runtime)
        held = self._readings.get(key)
        if held is not None and self._clock() - held[0] < self.TTL_SECONDS:
            return held[1]
        inflight = self._inflight.get(key)
        if inflight is not None:
            return await asyncio.shield(inflight)
        # No `await` between the miss and claiming the slot, so two previews on
        # the loop cannot both decide to probe.
        future = asyncio.ensure_future(asyncio.to_thread(probe))
        self._inflight[key] = future
        # Only the probe's own completion releases the slot and stores the
        # reading: a cancelled initiator (a closed browser tab) did not stop
        # the thread, and its result is still the freshest reading there is.
        future.add_done_callback(lambda done: self._settle(key, done))
        return await asyncio.shield(future)

    def _settle(self, key: _Key, done: asyncio.Future[str | None]) -> None:
        if self._inflight.get(key) is done:
            del self._inflight[key]
        if not done.cancelled() and done.exception() is None:
            self._readings[key] = (self._clock(), done.result())


__all__: list[str] = []
