"""The two clocks (`grove.core.session_duration.duration_of`).

Pure arithmetic over a synthetic message spine — no transcript, no adapter, no
clock — because the thing worth pinning is the DIVERGENCE the feature exists
for: ten minutes of wall time that cost thirty minutes of agent work must report
both numbers, and the invariant ``active_ms <= elapsed_span_ms`` is what proves
the wall clock is still a union and has not silently become a sum.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grove.core.agents.model import AgentMessage, ContentBlock
from grove.core.session_duration import duration_of

START = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


def _at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def _exchange(*, start: float, end: float, thread: str | None = None) -> list[AgentMessage]:
    """A prompt and the reply it produced — one generation interval.

    A sub-agent thread carries ``is_sidechain``/``thread_id``, which is what
    keeps its interval its OWN rather than an extension of the main thread's:
    the pairing is per thread, so concurrent threads produce overlapping
    intervals instead of one long one.
    """
    return [
        AgentMessage(
            role="user",
            timestamp=_at(start),
            is_sidechain=thread is not None,
            thread_id=thread,
        ),
        AgentMessage(
            role="assistant",
            timestamp=_at(end),
            is_sidechain=thread is not None,
            thread_id=thread,
        ),
    ]


def test_concurrent_subagents_diverge_the_wall_clock_from_the_compute_total() -> None:
    """The whole point of two numbers: three agents working the same ten
    minutes cost thirty minutes and took ten. Reporting either one alone is a
    lie in a different direction."""
    messages = [
        *_exchange(start=0, end=10),
        *_exchange(start=0, end=10, thread="sub-a"),
        *_exchange(start=0, end=10, thread="sub-b"),
    ]

    duration = duration_of(messages)

    assert duration.active_ms == 10 * 60_000
    assert duration.execution_ms == 30 * 60_000
    assert duration.confidence == "derived"


def test_the_wall_clock_never_exceeds_the_span_that_contains_it() -> None:
    """The invariant that caught the original bug: a session cannot have worked
    longer than it existed. If this fails, the union has become a sum."""
    messages = [
        *_exchange(start=0, end=5),
        *_exchange(start=0, end=5, thread="sub-a"),
        *_exchange(start=20, end=30),
    ]

    duration = duration_of(messages)

    assert duration.elapsed_span_ms == 30 * 60_000
    assert duration.active_ms is not None
    assert duration.active_ms <= duration.elapsed_span_ms
    assert duration.execution_ms is not None
    assert duration.active_ms <= duration.execution_ms


def test_the_wait_for_a_human_is_not_work() -> None:
    """A reply followed twenty minutes later by the next prompt is thinking
    time, and counting it would make an overnight session look like labour."""
    messages = [
        *_exchange(start=0, end=1),
        *_exchange(start=21, end=22),
    ]

    duration = duration_of(messages)

    assert duration.active_ms == 2 * 60_000  # the two generations, not the gap
    assert duration.elapsed_span_ms == 22 * 60_000


def _tool(*, start: float, end: float, call: str) -> list[AgentMessage]:
    """A tool call and the result it returned — one tool interval."""
    return [
        AgentMessage(
            role="assistant",
            content=(ContentBlock(type="tool_use", tool_name="Bash", tool_use_id=call),),
            timestamp=_at(start),
        ),
        AgentMessage(
            role="tool",
            content=(ContentBlock(type="tool_result", tool_use_id=call),),
            timestamp=_at(end),
        ),
    ]


def test_the_compute_total_says_which_half_was_the_model_and_which_the_tools() -> None:
    """``execution_ms`` alone cannot tell a slow model from a slow test suite.

    Both are the same number there and they take opposite remedies, so the two
    halves ride beside it — and they ADD UP to it, which is what makes them a
    partition of one measurement rather than a second one.
    """
    messages = [
        *_exchange(start=0, end=2),
        *_tool(start=2, end=12, call="build"),
        *_exchange(start=12, end=15),
    ]

    duration = duration_of(messages)

    assert duration.generation_ms == 5 * 60_000
    assert duration.tool_ms == 10 * 60_000
    assert duration.generation_ms + duration.tool_ms == duration.execution_ms == 15 * 60_000


def test_a_session_that_ran_no_tools_reports_absent_tool_time_not_zero() -> None:
    """The nullability rule the whole view is built on, applied per half.

    A conversation with no tool call did not spend zero time in tools — no
    tool time was measured at all, and a rendered `0s` would claim otherwise.
    """
    duration = duration_of(_exchange(start=0, end=4))

    assert duration.tool_ms is None
    assert duration.generation_ms == duration.execution_ms == 4 * 60_000


def test_an_empty_spine_is_timed_and_measures_nothing() -> None:
    """A remote adapter answers `()` by contract, so "nothing to measure" must
    be an all-null view with `unknown` confidence — the caller's own `None`
    keeps meaning "not measured yet", and the two must not collapse."""
    duration = duration_of([])

    assert (
        duration.active_ms,
        duration.execution_ms,
        duration.generation_ms,
        duration.tool_ms,
        duration.elapsed_span_ms,
    ) == (None, None, None, None, None)
    assert duration.confidence == "unknown"


def test_a_spine_with_no_timestamps_reports_no_span() -> None:
    """Timestamps are the only evidence there is; a record without one
    contributes nothing rather than a zero-length guess."""
    duration = duration_of([AgentMessage(role="user"), AgentMessage(role="assistant")])

    assert duration.elapsed_span_ms is None
    assert duration.active_ms is None
    assert duration.confidence == "unknown"
