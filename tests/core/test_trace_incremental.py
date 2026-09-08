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
