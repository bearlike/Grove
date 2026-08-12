"""One session's active intervals — one set, two reducers that disagree.

A session's intervals belong to the root agent AND to every sub-agent thread it
spawned, and sub-agents run CONCURRENTLY. Adding them up answers *how much agent
time did this task cost*; merging the overlaps first answers *how long was
something running*, which is the only one of the two a wall clock would agree
with. Grove published the sum under the union's name until 2026-08-11, when one
real fleet session (2997 messages, 1849 of them sidechain across 13 sub-agent
threads) reported 6.93 h of "active" work inside a 4.56 h lifespan — a duration
longer than the session containing it, at a concurrency factor of 2.22x.

Pure arithmetic over epoch milliseconds: no transcript, no database, no clock.
That is what lets the projector (which derives intervals from the message spine)
and the query service (which reads them back off the event spine) reduce through
one implementation rather than two that drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActiveIntervals:
    """Half-open ``[start, end)`` epoch-millisecond intervals, in any order.

    Holds every agent's intervals together, deliberately unlabelled by thread:
    which agent an interval belonged to changes neither reducer's answer, and a
    per-thread structure would invite a third number nobody can define.
    """

    intervals: tuple[tuple[int, int], ...] = ()

    @classmethod
    def of(cls, intervals: Iterable[tuple[int, int]]) -> ActiveIntervals:
        """Collect *intervals*, clamping an inverted pair to zero length.

        The two endpoints are routinely written by two different clocks (an
        agent's own and its tool result's), so an end before its start is a
        clock artefact rather than negative work.
        """
        return cls(tuple((start, max(start, end)) for start, end in intervals))

    def sum_ms(self) -> int | None:
        """Every interval added up — a labour total, not a clock.

        ``None`` for no intervals at all: an unmeasurable duration is absent,
        never zero. A measured zero (an instantaneous tool) stays zero.
        """
        return sum(end - start for start, end in self.intervals) if self.intervals else None

    def union_ms(self) -> int | None:
        """Time covered by at least one interval — overlaps counted ONCE.

        The wall clock, and the only reducer that can be compared against the
        session's own lifespan. ``None`` for no intervals, as for ``sum_ms``.
        """
        if not self.intervals:
            return None
        ordered = sorted(self.intervals)
        total = 0
        start, end = ordered[0]
        for next_start, next_end in ordered[1:]:
            if next_start > end:
                total += end - start
                start, end = next_start, next_end
                continue
            end = max(end, next_end)
        return total + end - start


@dataclass(frozen=True, slots=True)
class WorkIntervals:
    """The same interval set, kept apart by WHAT the agent was waiting on.

    One derivation pass, two labelled halves. ``execution_ms`` — the sum of
    every interval — answers *how much agent time did this cost* and cannot
    answer *what was it doing*, yet those are the two costs a reader acts on
    differently: a session dominated by generation is a model-speed or
    prompt-size problem, one dominated by tools is a test suite or a network.

    **Only the SUM partitions.** ``sum_ms(generation) + sum_ms(tool)`` equals
    ``sum_ms(combined)`` by construction, because addition is what the sum
    reducer does. The UNION does not: a tool running while a sub-agent
    generates is one span of wall clock and would be counted twice by adding
    two unions, which is the exact defect ``union_ms`` exists to prevent. So
    ``active_ms`` stays a property of :attr:`combined` alone and is never
    published per half.
    """

    generation: ActiveIntervals = ActiveIntervals()
    """Spans where a request was out at the model — a user or tool record
    followed by the assistant reply it produced, on any thread."""

    tool: ActiveIntervals = ActiveIntervals()
    """Spans where a tool was running — a ``tool_use`` followed by its
    correlated ``tool_result``, on any thread."""

    @property
    def combined(self) -> ActiveIntervals:
        """Both halves as the one set the two existing reducers read.

        Constructed by concatenation rather than by re-deriving: both halves
        were already built through :meth:`ActiveIntervals.of`, so the inverted-
        pair clamp has been applied and re-applying it would be a second copy
        of a rule that lives in one place.
        """
        return ActiveIntervals(self.generation.intervals + self.tool.intervals)
