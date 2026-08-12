"""How long a session WORKED — the two clocks, derived from one message spine.

One question: *given an adapter's normalized messages, what are this session's
durations?* The answer is deliberately three numbers and a confidence
(:class:`~grove.core.contracts.usage.DurationView`), because one number is a
lie: the union of the active intervals is a wall clock, their sum is a labour
total across the root agent and every concurrent sub-agent, and the birth→last
event span is neither.

**Nothing here derives an interval or reduces one.** Both already exist and are
reused verbatim: ``usage.projector._derived_intervals`` pairs generation and
tool intervals off the spine (human waits excluded, sub-agent threads included),
and ``usage._intervals.ActiveIntervals`` is the ONE reduction — ``union_ms`` for
the wall clock, ``sum_ms`` for the compute total. This module exists only
because the *session listing* surfaces need those same two numbers and the usage
audit is not on their read path: the audit's SQLite projection is gated on
``usage.enabled``, pruned by ``usage.retention_days`` and refreshed on its own
schedule, so a browse column sourced from it would go null as a session aged
out. Same computation, different durability.

The reach into ``grove.core.usage`` is the awkward part and is stated rather
than hidden: ``_derived_intervals`` is the single definition of "which spans of
a transcript are work", and a second copy here is exactly the drift
``ActiveIntervals`` was created to stop. It belongs in a module both callers can
import without one subsystem reaching into another's internals; moving it is a
usage-package change this one deliberately does not make.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from grove.core.contracts.usage import DurationView, GenerationLatencyView
from grove.core.usage.projector import _derived_intervals, _generation_duration_ms

if TYPE_CHECKING:
    from datetime import datetime

    from grove.core.agents import AgentMessage


def duration_of(messages: Sequence[AgentMessage]) -> DurationView:
    """The session's two clocks plus its span, from an adapter message spine.

    ``active_ms`` is the union of the active intervals (concurrency counted
    ONCE — the real elapsed working time), ``execution_ms`` is the same
    intervals summed across every agent that ran (ten sub-agents of ten minutes
    side by side report 10m and 100m), and ``elapsed_span_ms`` is first
    timestamp to last. The divergence between the first two is the measurement,
    not a double count. ``generation_ms``/``tool_ms`` split that sum by what
    the agent was waiting on, straight off the same pass — the reach into
    ``_derived_intervals`` is what keeps this view and the audit's own columns
    agreeing on where a session's time went.

    An empty or timestamp-less spine yields a view whose fields are all
    ``None`` and whose confidence is ``unknown`` — *timed, nothing measurable*,
    which is a different fact from the caller's own ``None`` (*not timed*). A
    remote adapter with no spine at all lands here honestly.

    Pure: no I/O, no clock. ``elapsed_span_ms`` comes from the messages' own
    timestamps rather than the transcript's birth/mtime, so the invariant
    ``active_ms <= elapsed_span_ms`` holds by construction — every interval is
    built from a pair of those same timestamps.
    """
    intervals = _derived_intervals(tuple(messages))
    combined = intervals.combined
    active_ms = combined.union_ms()
    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    elapsed_span_ms = int((max(stamps) - min(stamps)).total_seconds() * 1000) if stamps else None
    return DurationView(
        active_ms=active_ms,
        execution_ms=combined.sum_ms(),
        generation_ms=intervals.generation.sum_ms(),
        tool_ms=intervals.tool.sum_ms(),
        elapsed_span_ms=elapsed_span_ms,
        confidence="derived" if active_ms is not None or elapsed_span_ms is not None else "unknown",
    )


def generation_latency_of(messages: Sequence[AgentMessage]) -> GenerationLatencyView:
    """The model's own average response wait, from one message spine.

    Distinct from ``duration_of``'s ``execution_ms``, which sums generation
    AND tool time together into one labour total: the moment a session runs
    any tools at all, that number stops answering "how slow is the model
    itself". This means just the generation intervals — reusing
    ``_generation_duration_ms`` verbatim rather than re-deriving it, the same
    reach into ``usage.projector`` and the same reason ``duration_of``'s
    module docstring states, so the historical per-model breakdown (which
    reads the identical per-event value back off ``usage_events``) and this
    live figure can never disagree about what "waiting on the model" means.

    Root and sub-agent generations both count, matching ``execution_ms``'s
    population. Returns the empty view (``avg_ms=None, calls=0``) when no
    generation had a measurable interval — never a fabricated 0ms average.

    Pure: no I/O, no clock.
    """
    previous_by_thread: dict[str | None, tuple[str, datetime]] = {}
    latencies: list[int] = []
    for message in messages:
        if message.role == "assistant":
            latency = _generation_duration_ms(message, previous_by_thread)
            if latency is not None:
                latencies.append(latency)
        if message.timestamp is not None:
            previous_by_thread[message.thread_id] = (message.role, message.timestamp)
    if not latencies:
        return GenerationLatencyView()
    return GenerationLatencyView(
        avg_ms=round(sum(latencies) / len(latencies)), calls=len(latencies)
    )


__all__ = ["duration_of", "generation_latency_of"]
