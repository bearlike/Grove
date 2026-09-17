"""Incremental live facts over one normalized agent-message spine.

``SpineFactsCache`` belongs to ONE session. Its owner keys it by the full
``(adapter kind, cwd, profile scope, session id)`` coordinate and drops that entry
when the session/workspace leaves the activity surface. This keeps the retained
message tuple, correlation clocks and interval union bounded by the live session
population rather than every transcript Grove has ever read.

A normal adapter read preserves the same frozen ``AgentMessage`` objects for its
unchanged prefix. The cache validates that prefix by object identity (not
``==``), then folds only the appended suffix. Equal-length replacements and
inserts are rebuilt from the complete current spine: they are unusual, but an
incorrect incremental answer is worse than the bounded replay they cost.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from grove.core.agents.model import AgentMessage
from grove.core.contracts.usage import DurationView, GenerationLatencyView, TokenClassesView
from grove.core.usage.projector import _generation_duration_ms


@dataclass(frozen=True, slots=True)
class SpineFacts:
    """The live reductions one message spine supports.

    This mirrors activity's established duration, token-class and model-latency
    payloads. It is internal state rather than a new wire contract.
    """

    duration: DurationView
    token_classes: TokenClassesView
    generation_latency: GenerationLatencyView


@dataclass(slots=True)
class _IntervalUnion:
    """A sorted, merged interval set whose covered time updates on insertion."""

    ranges: list[tuple[int, int]] = field(default_factory=list)
    total_ms: int = 0

    def add(self, start: int, end: int) -> None:
        """Add one clock interval, retaining the union reducer's overlap rule."""
        end = max(start, end)
        position = bisect_left(self.ranges, (start, end))
        if position and self.ranges[position - 1][1] >= start:
            position -= 1
            previous_start, previous_end = self.ranges.pop(position)
            self.total_ms -= previous_end - previous_start
            start = min(start, previous_start)
            end = max(end, previous_end)
        while position < len(self.ranges) and self.ranges[position][0] <= end:
            next_start, next_end = self.ranges.pop(position)
            self.total_ms -= next_end - next_start
            start = min(start, next_start)
            end = max(end, next_end)
        self.ranges.insert(position, (start, end))
        self.total_ms += end - start

    @property
    def measured(self) -> int | None:
        """Covered milliseconds, or ``None`` when no interval was measurable."""
        return self.total_ms if self.ranges else None


@dataclass(slots=True)
class _TokenTotals:
    """Nullable class totals without revisiting retained message payloads."""

    fresh_input: int = 0
    fresh_input_seen: bool = False
    cache_read: int = 0
    cache_read_seen: bool = False
    cache_creation: int = 0
    cache_creation_seen: bool = False
    reasoning: int = 0
    reasoning_seen: bool = False
    output: int = 0
    output_seen: bool = False

    def add(self, message: AgentMessage) -> None:
        """Accumulate exactly the per-message accounting the live view exposes."""
        usage = message.usage
        if usage is None:
            return
        if usage.input is not None:
            self.fresh_input += usage.input
            self.fresh_input_seen = True
        if usage.cache_read is not None:
            self.cache_read += usage.cache_read
            self.cache_read_seen = True
        if usage.cache_creation is not None:
            self.cache_creation += usage.cache_creation
            self.cache_creation_seen = True
        if usage.reasoning is not None:
            self.reasoning += usage.reasoning
            self.reasoning_seen = True
        if usage.output is not None:
            self.output += usage.output
            self.output_seen = True

    def view(self) -> TokenClassesView:
        """Render absent classes as absent rather than a fabricated zero."""
        return TokenClassesView(
            fresh_input=self.fresh_input if self.fresh_input_seen else None,
            cache_read=self.cache_read if self.cache_read_seen else None,
            cache_creation=self.cache_creation if self.cache_creation_seen else None,
            reasoning=self.reasoning if self.reasoning_seen else None,
            output=self.output if self.output_seen else None,
        )


class SpineFactsCache:
    """Fold one immutable message spine incrementally, with exact rebuild fallback.

    ``facts`` is pure with respect to its input messages: the only mutation is
    this cache's derived state. ``processed_messages`` counts messages whose
    payloads were folded, so callers/tests can distinguish an O(new messages)
    append from the intentionally O(all messages) rebuild fallback.
    """

    def __init__(self) -> None:
        self._messages: tuple[AgentMessage, ...] = ()
        self._facts: SpineFacts | None = None
        self._previous_by_thread: dict[str | None, tuple[str, datetime]] = {}
        self._calls: dict[str, datetime] = {}
        self._intervals = _IntervalUnion()
        self._generation_ms = 0
        self._generation_seen = False
        self._tool_ms = 0
        self._tool_seen = False
        self._first_timestamp: datetime | None = None
        self._last_timestamp: datetime | None = None
        self._latency_total_ms = 0
        self._latency_calls = 0
        self._tokens = _TokenTotals()
        self.processed_messages = 0
        self.rebuilds = 0
        self.incremental_updates = 0

    @property
    def folded_messages(self) -> int:
        """Compatibility-friendly name for the payload-work counter."""
        return self.processed_messages

    def clear(self) -> None:
        """Release the retained generation when the owning session is evicted."""
        self._messages = ()
        self._facts = None
        self._previous_by_thread.clear()
        self._calls.clear()
        self._intervals = _IntervalUnion()
        self._generation_ms = 0
        self._generation_seen = False
        self._tool_ms = 0
        self._tool_seen = False
        self._first_timestamp = None
        self._last_timestamp = None
        self._latency_total_ms = 0
        self._latency_calls = 0
        self._tokens = _TokenTotals()
        self.processed_messages = 0
        self.rebuilds = 0
        self.incremental_updates = 0

    def facts(self, messages: Sequence[AgentMessage]) -> SpineFacts:
        """Return current facts, folding only an identity-retained appended suffix.

        Prefix validation is O(N) identity comparisons. It deliberately does not
        traverse message fields or content blocks; retained payloads are never
        re-reduced. Any changed prefix, tail replacement, removal or insertion
        instead rebuilds from the current complete spine, preserving exactness.
        """
        current = tuple(messages)
        if self._facts is None:
            self._rebuild(current)
            return self._current_facts()

        prefix = self._identity_prefix(current)
        if prefix == len(self._messages) and len(current) >= prefix:
            if len(current) == prefix:
                return self._facts
            self.incremental_updates += 1
            self._fold(current[prefix:])
            self._messages = current
            return self._current_facts()

        self._rebuild(current)
        return self._current_facts()

    def _identity_prefix(self, current: tuple[AgentMessage, ...]) -> int:
        """Find the retained prefix without invoking value equality on messages."""
        limit = min(len(self._messages), len(current))
        prefix = 0
        while prefix < limit and current[prefix] is self._messages[prefix]:
            prefix += 1
        return prefix

    def _rebuild(self, messages: tuple[AgentMessage, ...]) -> None:
        """Discard derived state and fold the current spine exactly once."""
        self._previous_by_thread.clear()
        self._calls.clear()
        self._intervals = _IntervalUnion()
        self._generation_ms = 0
        self._generation_seen = False
        self._tool_ms = 0
        self._tool_seen = False
        self._first_timestamp = None
        self._last_timestamp = None
        self._latency_total_ms = 0
        self._latency_calls = 0
        self._tokens = _TokenTotals()
        self._facts = None
        self._fold(messages)
        self._messages = messages
        self.rebuilds += 1

    def _fold(self, messages: Sequence[AgentMessage]) -> None:
        """Advance all reductions through one new suffix of normalized messages."""
        for message in messages:
            self.processed_messages += 1
            self._tokens.add(message)
            self._fold_generation(message)
            self._fold_tools(message)
            if message.timestamp is not None:
                self._fold_timestamp(message.timestamp)
                self._previous_by_thread[message.thread_id] = (message.role, message.timestamp)

    def _fold_generation(self, message: AgentMessage) -> None:
        """Reuse the audit's generation predicate before recording its interval."""
        if message.role != "assistant":
            return
        latency = _generation_duration_ms(message, self._previous_by_thread)
        if latency is None:
            return
        previous = self._previous_by_thread[message.thread_id]
        timestamp = message.timestamp
        if timestamp is not None:
            self._add_generation(previous[1], timestamp, latency)

    def _fold_tools(self, message: AgentMessage) -> None:
        """Pair calls exactly as the established full interval reducer does."""
        if message.timestamp is None:
            return
        for block in message.content:
            if block.type == "tool_use" and block.tool_use_id:
                self._calls[block.tool_use_id] = message.timestamp
            elif block.type == "tool_result" and block.tool_use_id:
                started = self._calls.get(block.tool_use_id)
                if started is not None:
                    self._add_tool(started, message.timestamp)

    def _add_generation(self, start: datetime, end: datetime, latency_ms: int) -> None:
        """Add one model interval to the common union and its labelled total."""
        self._intervals.add(self._epoch_ms(start), self._epoch_ms(end))
        self._generation_ms += latency_ms
        self._generation_seen = True
        self._latency_total_ms += latency_ms
        self._latency_calls += 1

    def _add_tool(self, start: datetime, end: datetime) -> None:
        """Add one tool interval using the same inverted-clock clamp as the audit."""
        start_ms = self._epoch_ms(start)
        end_ms = self._epoch_ms(end)
        duration_ms = max(0, end_ms - start_ms)
        self._intervals.add(start_ms, end_ms)
        self._tool_ms += duration_ms
        self._tool_seen = True

    def _fold_timestamp(self, timestamp: datetime) -> None:
        """Maintain the elapsed-span endpoints without revisiting old messages."""
        if self._first_timestamp is None or timestamp < self._first_timestamp:
            self._first_timestamp = timestamp
        if self._last_timestamp is None or timestamp > self._last_timestamp:
            self._last_timestamp = timestamp

    def _current_facts(self) -> SpineFacts:
        """Materialize the three stable public value objects from folded state."""
        active_ms = self._intervals.measured
        elapsed_span_ms = (
            int((self._last_timestamp - self._first_timestamp).total_seconds() * 1000)
            if self._first_timestamp is not None and self._last_timestamp is not None
            else None
        )
        duration = DurationView(
            active_ms=active_ms,
            execution_ms=(self._generation_ms if self._generation_seen else 0)
            + (self._tool_ms if self._tool_seen else 0)
            if self._generation_seen or self._tool_seen
            else None,
            generation_ms=self._generation_ms if self._generation_seen else None,
            tool_ms=self._tool_ms if self._tool_seen else None,
            elapsed_span_ms=elapsed_span_ms,
            confidence=(
                "derived" if active_ms is not None or elapsed_span_ms is not None else "unknown"
            ),
        )
        latency = (
            GenerationLatencyView(
                avg_ms=round(self._latency_total_ms / self._latency_calls),
                calls=self._latency_calls,
            )
            if self._latency_calls
            else GenerationLatencyView()
        )
        self._facts = SpineFacts(
            duration=duration,
            token_classes=self._tokens.view(),
            generation_latency=latency,
        )
        return self._facts

    @staticmethod
    def _epoch_ms(timestamp: datetime) -> int:
        """The audit's epoch-millisecond clock representation."""
        return int(timestamp.timestamp() * 1000)


__all__ = ["SpineFacts", "SpineFactsCache"]
