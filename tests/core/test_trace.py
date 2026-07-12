"""Unit coverage for `grove.core.trace` (#175) — deterministic ids, the
agent→generation→tool→sub-agent span tree, usage/cost/TTFT attributes, and
the disabled/misconfigured no-op contract.

Exercises the REAL opentelemetry-sdk (an in-memory exporter, never a network
call) via `sink_from_processor` so the span-construction path under test is
the identical one production traffic takes — not a hand-rolled stand-in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.model import AgentMessage, ContentBlock, TokenUsage
from grove.core.config import TelemetryConfig
from grove.core.trace import (
    SpanRecord,
    TraceInstrumentor,
    build_span_sink,
    derive_span_id,
    derive_trace_id,
    sink_from_processor,
)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def in_memory() -> tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource]:
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    resource = Resource.create({"service.name": "grove-test"})
    return exporter, processor, resource


def _by_name(spans: list[ReadableSpan]) -> dict[str, ReadableSpan]:
    return {s.name: s for s in spans}


# ─── deterministic ids ───────────────────────────────────────────────────────


def test_derive_trace_id_is_deterministic_and_nonzero() -> None:
    a = derive_trace_id("session-1")
    b = derive_trace_id("session-1")
    c = derive_trace_id("session-2")
    assert a == b
    assert a != c
    assert a != 0


def test_derive_span_id_is_deterministic_and_scoped() -> None:
    a = derive_span_id("session-1", "tool", "tu1")
    b = derive_span_id("session-1", "tool", "tu1")
    c = derive_span_id("session-1", "tool", "tu2")
    d = derive_span_id("session-2", "tool", "tu1")
    assert a == b
    assert len({a, c, d}) == 3
    assert a != 0


# ─── SpanRecord attribute vocabulary ─────────────────────────────────────────


def test_generation_record_carries_usage_cost_and_ttft_when_known() -> None:
    usage = TokenUsage(input=10, output=5, cache_creation=2, cache_read=1, reasoning=None)
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        name="generation:claude-x",
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
        model="claude-x",
        usage=usage,
        cost_usd=0.0042,
        completion_start_time=_ts("2026-07-08T09:59:59"),
    )
    assert record.attributes["langfuse.observation.type"] == "generation"
    assert record.attributes["langfuse.observation.model.name"] == "claude-x"
    details = json.loads(record.attributes["langfuse.observation.usage_details"])
    assert details == {"input": 10, "output": 5, "cache_creation": 2, "cache_read": 1}
    cost = json.loads(record.attributes["langfuse.observation.cost_details"])
    assert cost == {"total": 0.0042}
    assert (
        record.attributes["langfuse.observation.completion_start_time"]
        == _ts("2026-07-08T09:59:59").isoformat()
    )


def test_generation_record_omits_unknown_fields_never_fabricates() -> None:
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        name="generation",
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
    )
    assert "langfuse.observation.model.name" not in record.attributes
    assert "langfuse.observation.usage_details" not in record.attributes
    assert "langfuse.observation.cost_details" not in record.attributes
    assert "langfuse.observation.completion_start_time" not in record.attributes


# ─── sink_from_processor — the real OTel wiring, no network ─────────────────


def test_sink_from_processor_exports_deterministic_ids_and_parent_link(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    trace_id = derive_trace_id("s1")
    root_id = derive_span_id("s1", "agent", "root")
    child_id = derive_span_id("s1", "tool", "tu1")
    sink(
        SpanRecord.agent(
            trace_id=trace_id,
            span_id=root_id,
            parent_span_id=None,
            name="session:s1",
            start_time=_ts("2026-07-08T10:00:00"),
            end_time=_ts("2026-07-08T10:00:05"),
        )
    )
    sink(
        SpanRecord.tool(
            trace_id=trace_id,
            span_id=child_id,
            parent_span_id=root_id,
            name="Read",
            start_time=_ts("2026-07-08T10:00:01"),
            end_time=_ts("2026-07-08T10:00:02"),
            tool_input={"file_path": "x.py"},
        )
    )
    sink.flush()

    spans = exporter.get_finished_spans()
    by_name = _by_name(list(spans))
    root = by_name["session:s1"]
    tool = by_name["Read"]
    assert root.context is not None
    assert tool.context is not None
    assert root.context.trace_id == trace_id
    assert root.context.span_id == root_id
    assert tool.context.trace_id == trace_id
    assert tool.context.span_id == child_id
    assert tool.parent is not None
    assert tool.parent.span_id == root_id
    assert root.parent is None
    assert root.attributes is not None
    assert root.attributes["langfuse.observation.type"] == "agent"
    assert tool.attributes is not None
    assert json.loads(tool.attributes["langfuse.observation.input"]) == {"file_path": "x.py"}
    # Explicit historical timing survives the round trip (ns precision).
    assert root.start_time == int(_ts("2026-07-08T10:00:00").timestamp() * 1_000_000_000)
    assert root.end_time == int(_ts("2026-07-08T10:00:05").timestamp() * 1_000_000_000)


# ─── disabled / misconfigured → no-op ────────────────────────────────────────


def test_build_span_sink_noop_when_disabled() -> None:
    assert build_span_sink(TelemetryConfig(enabled=False)) is None


def test_build_span_sink_noop_when_credentials_unresolved() -> None:
    cfg = TelemetryConfig(enabled=True)
    assert build_span_sink(cfg, env={}) is None


def test_instrumentor_disabled_never_reads_or_calls_sink() -> None:
    class _ExplodingSink:
        def __call__(self, record: SpanRecord) -> None:
            raise AssertionError("must not be called when disabled")

        def flush(self) -> None:
            raise AssertionError("must not be called when disabled")

    class _ExplodingAdapter:
        kind = "claude_code"

        def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
            raise AssertionError("must not be called when disabled")

    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=False), sink=_ExplodingSink())
    assert instrumentor.enabled is False
    instrumentor.replay(Path("/tmp/x"), "sid", _ExplodingAdapter())  # never raises, never reads


# ─── the agent → generation → tool tree (fake adapter, no filesystem) ───────


class _FakeAdapter:
    """A minimal `SpineAdapter` — no `fleet_activity`, so `replay` exercises
    only the main-thread walk (the sub-agent tree is covered separately
    against the real `ClaudeCodeAdapter`, since the spawning-tool link reads
    a real sidecar file)."""

    kind = "generic"

    def __init__(self, messages: tuple[AgentMessage, ...]) -> None:
        self._messages = messages

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self._messages


def test_replay_builds_agent_generation_tool_tree(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=sink)

    messages = (
        AgentMessage(
            role="user",
            content=(ContentBlock(type="text", text="do it"),),
            timestamp=_ts("2026-07-08T10:00:00"),
        ),
        AgentMessage(
            role="assistant",
            message_id="m1",
            model="claude-x",
            usage=TokenUsage(input=10, output=5),
            timestamp=_ts("2026-07-08T10:00:01"),
            content=(
                ContentBlock(
                    type="tool_use", tool_name="Read", tool_use_id="tu1", tool_input={"a": 1}
                ),
            ),
        ),
        AgentMessage(
            role="tool",
            timestamp=_ts("2026-07-08T10:00:02"),
            content=(ContentBlock(type="tool_result", tool_use_id="tu1", text="ok"),),
        ),
        AgentMessage(
            role="assistant",
            message_id="m2",
            model="claude-x",
            usage=TokenUsage(input=20, output=8),
            timestamp=_ts("2026-07-08T10:00:03"),
            content=(ContentBlock(type="text", text="done"),),
        ),
    )
    adapter = _FakeAdapter(messages)
    session_id = "sess-tree"
    instrumentor.replay(Path("/work"), session_id, adapter)

    spans = list(exporter.get_finished_spans())
    assert len(spans) == 4  # root agent + 2 generations + 1 tool (nested under generation 1)

    trace_id = derive_trace_id(session_id)
    root_id = derive_span_id(session_id, "agent", "root")
    gen1_id = derive_span_id(session_id, "generation", "m1")
    gen2_id = derive_span_id(session_id, "generation", "m2")
    tool_id = derive_span_id(session_id, "tool", "tu1")

    # Look up by deterministic span id, never by name — two generations
    # share the name "generation:claude-x" here, so a name-keyed lookup
    # would silently collide on the wrong span.
    ids = {s.context.span_id: s for s in spans if s.context is not None}
    assert {root_id, gen1_id, gen2_id, tool_id} == set(ids)

    root = ids[root_id]
    assert root.context is not None and root.context.trace_id == trace_id
    assert root.parent is None
    # Root span spans the WHOLE session (first to last timestamp).
    assert root.start_time == int(_ts("2026-07-08T10:00:00").timestamp() * 1_000_000_000)
    assert root.end_time == int(_ts("2026-07-08T10:00:03").timestamp() * 1_000_000_000)

    gen1 = ids[gen1_id]
    assert gen1.parent is not None and gen1.parent.span_id == root_id
    assert gen1.attributes is not None
    assert gen1.attributes["langfuse.observation.type"] == "generation"

    gen2 = ids[gen2_id]
    assert gen2.parent is not None and gen2.parent.span_id == root_id

    tool = ids[tool_id]
    assert tool.parent is not None and tool.parent.span_id == gen1_id
    assert tool.attributes is not None
    assert json.loads(tool.attributes["langfuse.observation.input"]) == {"a": 1}
    # tool_result's timestamp closes the tool span, not the call time.
    assert tool.start_time == int(_ts("2026-07-08T10:00:01").timestamp() * 1_000_000_000)
    assert tool.end_time == int(_ts("2026-07-08T10:00:02").timestamp() * 1_000_000_000)


def test_replay_is_idempotent_on_reingestion(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    """Replaying the same session twice yields the SAME ids both times —
    LangFuse's OTLP ingest de-dupes on that, so a re-run backfill never
    double-records."""
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=sink)
    messages = (
        AgentMessage(
            role="assistant",
            message_id="m1",
            timestamp=_ts("2026-07-08T10:00:00"),
            content=(ContentBlock(type="text", text="hi"),),
        ),
    )
    adapter = _FakeAdapter(messages)
    instrumentor.replay(Path("/work"), "sess-repeat", adapter)
    instrumentor.replay(Path("/work"), "sess-repeat", adapter)

    spans = list(exporter.get_finished_spans())
    ids = [(s.context.trace_id, s.context.span_id) for s in spans if s.context is not None]
    assert len(ids) == 4  # 2 replays x (root + 1 generation)
    assert len(set(ids)) == 2  # but only 2 DISTINCT (trace_id, span_id) pairs


def test_replay_noop_when_adapter_read_fails(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=sink)

    class _BrokenAdapter:
        kind = "generic"

        def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
            raise RuntimeError("boom")

    instrumentor.replay(Path("/work"), "sess-broken", _BrokenAdapter())  # never raises
    assert exporter.get_finished_spans() == ()


# ─── sub-agent nesting, parented at the spawning tool span (real adapter) ───


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _write_lines(claude_home: Path, cwd: Path, sid: str, lines: list[str]) -> None:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_subagent(
    claude_home: Path,
    cwd: Path,
    sid: str,
    agent_id: str,
    lines: list[str],
    *,
    meta: dict[str, object] | None = None,
) -> None:
    sub_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"agent-{agent_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if meta is not None:
        (sub_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_replay_nests_subagent_agent_span_under_spawning_tool_span(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
    adapter: ClaudeCodeAdapter,
    claude_home: Path,
) -> None:
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=sink)

    sid = "14141414-1414-4141-8141-141414141414"
    cwd = Path("/home/kk/work/fleet-trace")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"Explore the auth flow","subagent_type":"Explore"}}]}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent01",
        [
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"sm1","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
            '"usage":{"input_tokens":20,"output_tokens":15},'
            '"content":[{"type":"text","text":"Auth uses session cookies."}]}}',
        ],
        meta={"agentType": "Explore", "description": "Explore the auth flow", "toolUseId": "tu1"},
    )

    instrumentor.replay(cwd, sid, adapter)

    spans = list(exporter.get_finished_spans())
    trace_id = derive_trace_id(sid)
    tool_span_id = derive_span_id(sid, "tool", "tu1")
    subagent_span_id = derive_span_id(sid, "agent", "agent01")

    ids = {s.context.span_id: s for s in spans if s.context is not None}
    assert tool_span_id in ids
    assert subagent_span_id in ids
    subagent_span = ids[subagent_span_id]
    assert subagent_span.name == "Explore"
    assert subagent_span.context is not None and subagent_span.context.trace_id == trace_id
    # The nested agent's parent is the exact TOOL span the main thread's
    # "Agent" tool_use produced — not a message-id-derived link.
    assert subagent_span.parent is not None
    assert subagent_span.parent.span_id == tool_span_id
