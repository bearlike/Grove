"""When is a trace complete enough to transform?

**A child arrives before its parent, and that is the normal case rather than a
race to be tolerated.** Measured against Claude Code 2.1.227 on 2026-08-11 with
``OTEL_TRACES_EXPORT_INTERVAL=2000``: the turn root ``claude_code.interaction``
shipped in the FIFTH and last export batch of the run, five batches after the
first span that named it as parent. Every earlier batch therefore contained
spans whose parent did not exist anywhere the consumer could see — which is
exactly how a backend ends up picking a leaf as the trace root and hanging the
orchestrator's own work underneath it. The root closes last because it spans
the whole turn; no export-interval setting can reorder that.

So the transform cannot run per batch. It runs per TRACE, over a hold that ends
when the root shows up — the ``groupbytrace`` shape, with the two bounds that
make it safe to run in a long-lived process: a trace is released on age even if
its root never arrives, and the number of held traces is capped.

**Nothing here reads a clock.** ``now`` is a parameter on every method, which is
what keeps a time-dependent release rule testable by passing three datetimes
instead of sleeping.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal

from grove.core.telemetry._otlp import SpanEnvelope

ReleaseReason = Literal["root", "age", "size", "capacity", "drain"]
"""Why a trace left the buffer.

Narrow enough to branch on, and worth carrying rather than logging at the
release site: ``root`` is the healthy path and any *other* reason on a busy
buffer is the signal that the bounds are mis-tuned for the fleet's turn length.
"""

DEFAULT_MAX_AGE: Final = timedelta(seconds=30)
"""How long a trace may wait for a root that may never come.

Held against the measured turn: the probe run's root closed 22.3 s after the
interaction began, so a bound under that would release mid-turn on every real
turn and re-create the orphaning this module exists to end. Thirty seconds is
the smallest round number above it; a fleet whose turns routinely run longer
should raise it rather than accept the partial releases.
"""

DEFAULT_MAX_SPANS_PER_TRACE: Final = 2048
"""Span ceiling per held trace. The largest trace observed on this host holds
270 observations, so this is roughly 8x headroom — it exists to bound memory
against a pathological session, not to shape normal traffic."""

DEFAULT_MAX_TRACES: Final = 512
"""Concurrently held traces. One per in-flight agent turn, so this is a fleet
size with headroom, not a throughput knob."""


@dataclass(slots=True, frozen=True)
class ReleasedTrace:
    """Every span the buffer is willing to hand the transform for one trace.

    ``root_span_id`` is the buffer's answer to "which of these is the turn", and
    it is ``None`` for an age- or size-forced release. The transform needs the
    distinction: with a root it can re-parent an orphan onto the turn, and
    without one it must leave the hierarchy alone rather than invent a root out
    of whichever span happens to be parentless — the precise mistake that
    produced the inverted trace in the first place.
    """

    trace_id: bytes
    envelopes: tuple[SpanEnvelope, ...]
    root_span_id: bytes | None
    reason: ReleaseReason


@dataclass(slots=True)
class _Held:
    """One trace's pending spans and the two facts the release rules read."""

    first_seen: datetime
    last_seen: datetime
    pending: list[SpanEnvelope] = field(default_factory=list)
    root_span_id: bytes | None = None


class TraceBuffer:
    """Hold spans by trace; release when the root lands, ages out or fills up.

    **A trace entry outlives its own release.** Once a root has been seen its id
    is remembered on the entry, so a span that arrives after the flush is still
    released *knowing* which span is the turn. Without that memory the tail of
    every turn — and every span of a second turn on the same trace id — would be
    handed over rootless and read as an orphan, which is the same defect one
    export interval later.

    Nothing is ever dropped here. A trace over its span ceiling or a buffer over
    its trace ceiling RELEASES early rather than shedding: a partial trace is a
    degraded record, an absent one is a lie about what the agent did. Shedding
    belongs at the ingest queue, where it can be counted and reported back to
    the sender.
    """

    def __init__(
        self,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        max_spans_per_trace: int = DEFAULT_MAX_SPANS_PER_TRACE,
        max_traces: int = DEFAULT_MAX_TRACES,
    ) -> None:
        self._max_age = max_age
        self._max_spans_per_trace = max_spans_per_trace
        self._max_traces = max_traces
        # Insertion-ordered, which is what makes "evict the oldest" a `next(iter(...))`
        # rather than a scan — this runs per batch on the transform pool.
        self._held: dict[bytes, _Held] = {}

    @property
    def held_spans(self) -> int:
        """Spans currently waiting for a root. The number an operator watches
        when deciding whether ``max_age`` is tuned for their fleet's turns."""
        return sum(len(entry.pending) for entry in self._held.values())

    def offer(
        self, envelopes: Sequence[SpanEnvelope], *, now: datetime
    ) -> tuple[ReleasedTrace, ...]:
        """Take one batch in; hand back whatever became releasable."""
        for envelope in envelopes:
            entry = self._held.get(envelope.span.trace_id)
            if entry is None:
                entry = _Held(first_seen=now, last_seen=now)
                self._held[envelope.span.trace_id] = entry
            if not entry.pending:
                # The age bound measures how long THESE spans have waited, so
                # it restarts whenever a hold begins — otherwise a long-lived
                # trace releases every batch the moment its first turn ages out.
                entry.first_seen = now
            entry.last_seen = now
            entry.pending.append(envelope)
            if not envelope.span.parent_span_id:
                entry.root_span_id = envelope.span.span_id

        released = [
            self._release(trace_id, "root" if entry.root_span_id else "size")
            for trace_id, entry in list(self._held.items())
            if entry.pending
            and (entry.root_span_id is not None or len(entry.pending) >= self._max_spans_per_trace)
        ]
        released.extend(self._evict_overflow())
        released.extend(self.drain(now=now))
        return tuple(released)

    def drain(self, *, now: datetime, force: bool = False) -> tuple[ReleasedTrace, ...]:
        """Release traces that have waited long enough (``force``: all of them).

        ``force`` is the shutdown path. It is deliberately not the same call as
        ``offer`` with a distant ``now``, because a caller shutting down wants
        every pending span out even from a trace that arrived a millisecond ago.
        """
        released = [
            self._release(trace_id, "drain" if force else "age")
            for trace_id, entry in self._held.items()
            if entry.pending and (force or now - entry.first_seen >= self._max_age)
        ]
        # An entry outlives its pending spans only to remember the root for a
        # late arrival. Once nothing has arrived for a whole `max_age` there is
        # nothing left to remember for, and keeping it would leak one dict
        # entry per trace the process ever saw.
        for trace_id in [
            trace_id
            for trace_id, entry in self._held.items()
            if force or now - entry.last_seen >= self._max_age
        ]:
            del self._held[trace_id]
        return tuple(released)

    def _evict_overflow(self) -> list[ReleasedTrace]:
        """Release oldest-first until the buffer is back inside ``max_traces``."""
        released: list[ReleasedTrace] = []
        while len(self._held) > self._max_traces:
            trace_id = next(iter(self._held))
            if self._held[trace_id].pending:
                released.append(self._release(trace_id, "capacity"))
            del self._held[trace_id]
        return released

    def _release(self, trace_id: bytes, reason: ReleaseReason) -> ReleasedTrace:
        """Hand over a trace's pending spans, keeping the entry's root memory."""
        entry = self._held[trace_id]
        pending, entry.pending = entry.pending, []
        return ReleasedTrace(
            trace_id=trace_id,
            envelopes=tuple(pending),
            root_span_id=entry.root_span_id,
            reason=reason,
        )
