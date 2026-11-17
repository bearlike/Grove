"""Incremental replay coverage for the trace manifest construction seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents.model import AgentMessage, ContentBlock
from grove.core.config import TelemetryConfig
from grove.core.trace import SpanRecord, TraceInstrumentor


def _at(second: int) -> datetime:
    return datetime(2026, 9, 15, tzinfo=UTC) + timedelta(seconds=second)


def _turn(index: int) -> tuple[AgentMessage, AgentMessage]:
    return (
        AgentMessage(
            role="user",
            message_id=f"prompt-{index}",
            timestamp=_at(index * 10),
            content=(ContentBlock(type="text", text=f"prompt {index}"),),
        ),
        AgentMessage(
            role="assistant",
            message_id=f"reply-{index}",
            timestamp=_at(index * 10 + 1),
            content=(ContentBlock(type="text", text=f"reply {index}"),),
        ),
    )


@dataclass(slots=True)
class _GrowingAdapter:
    messages: tuple[AgentMessage, ...] = ()
    reads: int = 0

    kind = "generic"

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        del cwd, session_id
        self.reads += 1
        return self.messages


@dataclass(slots=True)
class _RecordingSink:
    records: list[SpanRecord] = field(default_factory=list)
    flushes: int = 0
    acknowledge: bool = True

    def __call__(self, record: SpanRecord) -> None:
        self.records.append(record)

    def flush(self) -> bool:
        self.flushes += 1
        return self.acknowledge


def test_replay_skips_manifest_construction_for_an_acknowledged_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Growing a session builds only its new turn, never its historical prefix."""
    sink = _RecordingSink()
    adapter = _GrowingAdapter(messages=_turn(1))
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    built: list[str] = []
    real = TraceInstrumentor._thread_spans

    def count(
        self: TraceInstrumentor,
        session_id: str,
        messages: tuple[AgentMessage, ...],
        **kwargs: object,
    ) -> list[SpanRecord]:
        built.append(messages[0].message_id or "")
        return real(self, session_id, messages, **kwargs)

    monkeypatch.setattr(TraceInstrumentor, "_thread_spans", count)

    instrumentor.replay(Path("/workspace"), "session", adapter)
    assert built == ["prompt-1"]
    assert adapter.reads == 1

    adapter.messages += _turn(2)
    instrumentor.replay(Path("/workspace"), "session", adapter)

    assert built == ["prompt-1", "prompt-2"]
    assert adapter.reads == 2
    assert sink.flushes == 2


def test_replay_retries_when_flush_does_not_acknowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative acknowledgement is not a watermark advancement."""
    sink = _RecordingSink(acknowledge=False)
    adapter = _GrowingAdapter(messages=_turn(1))
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    built: list[str] = []
    real = TraceInstrumentor._thread_spans

    def count(
        self: TraceInstrumentor,
        session_id: str,
        messages: tuple[AgentMessage, ...],
        **kwargs: object,
    ) -> list[SpanRecord]:
        built.append(messages[0].message_id or "")
        return real(self, session_id, messages, **kwargs)

    monkeypatch.setattr(TraceInstrumentor, "_thread_spans", count)

    instrumentor.replay(Path("/workspace"), "session", adapter)
    sink.acknowledge = True
    instrumentor.replay(Path("/workspace"), "session", adapter)

    assert built == ["prompt-1", "prompt-1"]
    assert sink.flushes == 2


@dataclass(slots=True)
class _Ledger:
    """An in-memory stand-in for the durable store, shared across instrumentors."""

    ids: set[int] = field(default_factory=set)
    reads: int = 0

    def accepted(self, trace_ids: frozenset[int]) -> frozenset[int]:
        self.reads += 1
        return trace_ids & frozenset(self.ids)

    def accept(self, manifest: object) -> None:
        self.ids.add(manifest.trace_id)  # type: ignore[attr-defined]


def test_a_restarted_replay_sends_only_turns_nobody_acknowledged() -> None:
    """The restart case from #905: a fresh instrumentor re-sends nothing accepted."""
    ledger = _Ledger()
    adapter = _GrowingAdapter(messages=_turn(1) + _turn(2))
    first = _RecordingSink()
    TraceInstrumentor(TelemetryConfig(enabled=True), sink=first, acknowledged=ledger).replay(
        Path("/workspace"), "session", adapter
    )
    assert len(ledger.ids) == 2

    adapter.messages += _turn(3)
    restarted = _RecordingSink()
    TraceInstrumentor(TelemetryConfig(enabled=True), sink=restarted, acknowledged=ledger).replay(
        Path("/workspace"), "session", adapter
    )

    sent_before = {record.trace_id for record in first.records}
    sent_after = {record.trace_id for record in restarted.records}
    assert len(sent_after) == 1
    assert sent_after.isdisjoint(sent_before)
    assert restarted.flushes == 1


def test_an_unacknowledged_turn_is_not_recorded_as_accepted() -> None:
    ledger = _Ledger()
    adapter = _GrowingAdapter(messages=_turn(1))
    TraceInstrumentor(
        TelemetryConfig(enabled=True), sink=_RecordingSink(acknowledge=False), acknowledged=ledger
    ).replay(Path("/workspace"), "session", adapter)

    assert ledger.ids == set()


def test_a_quiet_session_consults_the_store_once_per_turn() -> None:
    """The store is the cold path; the in-memory set answers every later tick."""
    ledger = _Ledger()
    adapter = _GrowingAdapter(messages=_turn(1))
    instrumentor = TraceInstrumentor(
        TelemetryConfig(enabled=True), sink=_RecordingSink(), acknowledged=ledger
    )
    for _ in range(3):
        instrumentor.replay(Path("/workspace"), "session", adapter)

    assert ledger.reads == 1


def _long_turn(tools: int) -> tuple[AgentMessage, ...]:
    """A human turn whose single assistant message makes ``tools`` tool calls."""
    calls = tuple(
        ContentBlock(
            type="tool_use",
            tool_name="Bash",
            tool_use_id=f"toolu_{index}",
            tool_input={"command": f"echo {index}"},
        )
        for index in range(tools)
    )
    results = tuple(
        ContentBlock(type="tool_result", tool_use_id=f"toolu_{index}", text="ok")
        for index in range(tools)
    )
    return (
        AgentMessage(
            role="user",
            message_id="prompt-long",
            timestamp=_at(0),
            content=(ContentBlock(type="text", text="do a lot"),),
        ),
        AgentMessage(role="assistant", message_id="calls", timestamp=_at(1), content=calls),
        AgentMessage(role="user", message_id="results", timestamp=_at(2), content=results),
        AgentMessage(
            role="assistant",
            message_id="final",
            timestamp=_at(3),
            content=(ContentBlock(type="text", text="done"),),
        ),
    )


def test_a_turn_larger_than_admission_capacity_is_delivered_whole() -> None:
    """A turn over the processor's bounds was refused in part on every tick forever.

    Through the REAL bounded processor: the turn must be acknowledged once, with
    every span exported exactly once.
    """
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SpanExportResult  # noqa: PLC0415

    from grove.core.trace import _AcknowledgingSpanProcessor, sink_from_processor  # noqa: PLC0415

    exported: list[int] = []

    class _Exporter:
        def export(self, spans: object) -> SpanExportResult:
            exported.extend(span.context.span_id for span in spans)  # type: ignore[attr-defined]
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            pass

    processor = _AcknowledgingSpanProcessor(_Exporter(), max_pending_items=150, batch_size=64)
    sink = sink_from_processor(processor, Resource.create({}))  # type: ignore[arg-type]
    ledger = _Ledger()
    adapter = _GrowingAdapter(messages=_long_turn(300))
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink, acknowledged=ledger)

    planned = TraceInstrumentor(TelemetryConfig(enabled=True), sink=_RecordingSink()).plan(
        Path("/workspace"), "session", adapter
    )
    biggest = max(len(manifest.spans) for manifest in planned)
    assert biggest > 150  # the fixture really exceeds the processor's bound

    instrumentor.replay(Path("/workspace"), "session", adapter)
    instrumentor.replay(Path("/workspace"), "session", adapter)

    assert ledger.ids == {manifest.trace_id for manifest in planned}
    assert sorted(exported) == sorted(s.span_id for m in planned for s in m.spans)


def test_a_failed_chunk_resumes_where_it_stopped() -> None:
    """A retry sends only the chunks not yet acknowledged, never the turn whole."""

    @dataclass(slots=True)
    class _ScriptedSink:
        outcomes: list[bool]
        records: list[SpanRecord] = field(default_factory=list)

        def __call__(self, record: SpanRecord) -> None:
            self.records.append(record)

        def flush(self) -> bool:
            return self.outcomes.pop(0) if self.outcomes else True

    adapter = _GrowingAdapter(messages=_long_turn(300))
    planned = TraceInstrumentor(TelemetryConfig(enabled=True), sink=_RecordingSink()).plan(
        Path("/workspace"), "session", adapter
    )
    sizes = [len(manifest.spans) for manifest in planned]
    assert sizes[0] > 2 * TraceInstrumentor.REPLAY_CHUNK

    # The big turn's first chunk is acknowledged and its second refused.
    sink = _ScriptedSink(outcomes=[True, False])
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    instrumentor.replay(Path("/workspace"), "session", adapter)
    instrumentor.replay(Path("/workspace"), "session", adapter)

    sent = [record.span_id for record in sink.records]
    # Every planned span arrives, and only the refused chunk is sent twice.
    assert set(sent) == {s.span_id for m in planned for s in m.spans}
    assert len(sent) == len(set(sent)) + TraceInstrumentor.REPLAY_CHUNK
