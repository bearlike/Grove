"""Unit coverage for `grove.core.trace` (#175) — deterministic ids, the
agent→generation→tool→sub-agent span tree, usage/cost/TTFT attributes, and
the disabled/misconfigured no-op contract.

Exercises the REAL opentelemetry-sdk (an in-memory exporter, never a network
call) via `sink_from_processor` so the span-construction path under test is
the identical one production traffic takes — not a hand-rolled stand-in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from loguru import logger
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.model import AgentMessage, ContentBlock, TokenUsage
from grove.core.config import ModelPriceConfig, TelemetryConfig, UsagePricingConfig
from grove.core.telemetry.semconv import ChatMessage
from grove.core.trace import (
    SpanRecord,
    TraceInstrumentor,
    _trace_transport,
    build_span_sink,
    derive_span_id,
    derive_trace_id,
    price_book_estimator,
    sink_from_processor,
)
from grove.core.usage._pricing import PriceBook


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


# ─── price_book_estimator (#454 scope item 3) ────────────────────────────────


def _book(**models: ModelPriceConfig) -> PriceBook:
    return PriceBook(UsagePricingConfig(models=models))


def test_price_book_estimator_prices_each_class_separately() -> None:
    estimate = price_book_estimator(
        _book(
            **{
                "claude-x": ModelPriceConfig(
                    input=3.0, output=15.0, cache_read=0.3, cache_write=3.75
                )
            }
        )
    )
    usage = TokenUsage(
        input=1_000_000, output=1_000_000, cache_creation=2_000_000, cache_read=1_000_000
    )
    assert estimate(usage, "claude-x") == {
        "input": Decimal("3.000000"),
        "output": Decimal("15.000000"),
        "cache_read": Decimal("0.300000"),
        "cache_creation": Decimal("7.500000"),
    }


def test_price_book_estimator_omits_a_class_it_has_no_evidence_for() -> None:
    # No cache rates configured and no cache counts reported: those classes are
    # ABSENT keys, never zeros — a zero would claim the caching was free rather
    # than say Grove has nothing to report about it.
    estimate = price_book_estimator(_book(**{"claude-x": ModelPriceConfig(input=3.0, output=15.0)}))
    usage = TokenUsage(input=1_000_000, output=1_000_000, cache_creation=None, cache_read=None)
    assert estimate(usage, "claude-x") == {
        "input": Decimal("3.000000"),
        "output": Decimal("15.000000"),
    }


def test_an_empty_price_catalog_prices_nothing() -> None:
    # Grove ships NO built-in rates: `UsagePricingConfig.models` defaults empty,
    # so an operator who configured no prices gets no cost figure anywhere —
    # never a fallback table, never a "typical" rate for an unknown model.
    assert UsagePricingConfig().models == {}
    estimate = price_book_estimator(PriceBook(UsagePricingConfig()))
    usage = TokenUsage(input=1_000_000, output=1_000_000, cache_creation=None, cache_read=None)
    assert estimate(usage, "claude-x") is None


def test_price_book_estimator_never_fabricates_a_zero_for_an_unknown_model() -> None:
    estimate = price_book_estimator(_book(**{"claude-x": ModelPriceConfig(input=3.0)}))
    usage = TokenUsage(input=100, output=None, cache_creation=None, cache_read=None)
    assert estimate(usage, "some-other-model") is None
    assert estimate(usage, None) is None


def test_price_book_estimator_refuses_to_price_an_unmeasured_class() -> None:
    # `output` is priced but unreported: the audit's own rule is that such a
    # generation has no knowable cost, and a breakdown must not report the
    # classes it CAN see as if they were the whole bill.
    estimate = price_book_estimator(_book(**{"claude-x": ModelPriceConfig(input=3.0, output=15.0)}))
    usage = TokenUsage(input=1000, output=None, cache_creation=None, cache_read=None)
    assert estimate(usage, "claude-x") is None


def test_price_book_estimator_drops_reasoning_never_prices_it_as_a_fifth_class() -> None:
    # A generation whose only evidence is `reasoning` must stay unpriced: the
    # book has no token-count evidence to charge, and reasoning already bills
    # inside `output` wherever a provider reports it (never counted twice).
    estimate = price_book_estimator(_book(**{"claude-x": ModelPriceConfig(output=15.0)}))
    usage = TokenUsage(input=None, output=None, cache_creation=None, cache_read=None, reasoning=500)
    assert estimate(usage, "claude-x") is None


def test_reasoning_tokens_are_reported_but_never_a_cost_class() -> None:
    estimate = price_book_estimator(_book(**{"claude-x": ModelPriceConfig(output=15.0)}))
    usage = TokenUsage(input=None, output=1_000_000, cache_creation=None, cache_read=None)
    thought = TokenUsage(
        input=None, output=1_000_000, cache_creation=None, cache_read=None, reasoning=400_000
    )
    # Reasoning bills inside `output`, so thinking must not move the bill.
    assert estimate(thought, "claude-x") == estimate(usage, "claude-x")
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
        model="claude-x",
        usage=thought,
        cost=estimate(thought, "claude-x"),
    )
    reported = json.loads(record.attributes["langfuse.observation.usage_details"])
    assert reported["reasoning"] == 400_000
    assert "reasoning" not in json.loads(record.attributes["langfuse.observation.cost_details"])


# ─── SpanRecord attribute vocabulary ─────────────────────────────────────────


def test_generation_record_carries_usage_cost_and_ttft_when_known() -> None:
    usage = TokenUsage(input=10, output=5, cache_creation=2, cache_read=1, reasoning=None)
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
        model="claude-x",
        usage=usage,
        cost={"input": Decimal("0.003"), "output": Decimal("0.0012")},
        completion_start_time=_ts("2026-07-08T09:59:59"),
    )
    assert record.name == "chat claude-x"
    assert record.attributes["langfuse.observation.type"] == "generation"
    assert record.attributes["langfuse.observation.model.name"] == "claude-x"
    assert record.attributes["gen_ai.request.model"] == "claude-x"
    assert record.attributes["gen_ai.response.model"] == "claude-x"
    details = json.loads(record.attributes["langfuse.observation.usage_details"])
    assert details == {"input": 10, "output": 5, "cache_creation": 2, "cache_read": 1}
    # Usage is dual-written: the JSON blob LangFuse reads, and the portable
    # scalar attributes beside it.
    assert record.attributes["gen_ai.usage.input_tokens"] == 10
    assert record.attributes["gen_ai.usage.output_tokens"] == 5
    assert record.attributes["gen_ai.usage.cache_creation.input_tokens"] == 2
    assert record.attributes["gen_ai.usage.cache_read.input_tokens"] == 1
    cost = json.loads(record.attributes["langfuse.observation.cost_details"])
    assert cost == pytest.approx({"input": 0.003, "output": 0.0012, "total": 0.0042})
    assert (
        record.attributes["langfuse.observation.completion_start_time"]
        == _ts("2026-07-08T09:59:59").isoformat()
    )


def test_generation_record_omits_unknown_fields_never_fabricates() -> None:
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
    )
    # No model at all degrades the span name to the bare operation, never a
    # fabricated "chat None".
    assert record.name == "chat"
    assert "langfuse.observation.model.name" not in record.attributes
    assert "gen_ai.request.model" not in record.attributes
    assert "gen_ai.response.model" not in record.attributes
    assert "langfuse.observation.usage_details" not in record.attributes
    assert "langfuse.observation.cost_details" not in record.attributes
    assert "langfuse.observation.completion_start_time" not in record.attributes
    # An absent value is an OMITTED attribute, never an empty string.
    assert "langfuse.observation.input" not in record.attributes
    assert "langfuse.observation.output" not in record.attributes
    assert "gen_ai.input.messages" not in record.attributes
    assert "gen_ai.output.messages" not in record.attributes
    assert "gen_ai.prompt" not in record.attributes
    assert "gen_ai.completion" not in record.attributes


def test_generation_record_caps_input_and_output_text() -> None:
    # The flat text is DERIVED from structured messages, never passed
    # alongside them — capping still has to hold on the derived value.
    record = SpanRecord.generation(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
        input_messages=(ChatMessage.of_text("user", "i" * 9000),),
        output_messages=(ChatMessage.of_text("assistant", "o" * 9000),),
    )
    # Both the vendor's flat key and the convention's superseded flat key cap
    # identically — they are the same derived string written twice.
    assert len(record.attributes["langfuse.observation.input"]) == 4000
    assert len(record.attributes["langfuse.observation.output"]) == 4000
    assert len(record.attributes["gen_ai.prompt"]) == 4000
    assert len(record.attributes["gen_ai.completion"]) == 4000
    # The structured document itself is capped too — an unbounded conversation
    # is exactly the oversized-attribute failure this ceiling exists to avoid.
    assert len(record.attributes["gen_ai.input.messages"]) <= 4000
    assert len(record.attributes["gen_ai.output.messages"]) <= 4000


def test_tool_record_omits_output_while_the_call_is_still_in_flight() -> None:
    record = SpanRecord.tool(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        name="Read",
        start_time=_ts("2026-07-08T10:00:00"),
        end_time=_ts("2026-07-08T10:00:00"),
        tool_input={"file_path": "x.py"},
    )
    assert "langfuse.observation.output" not in record.attributes


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
            tool_call_id="tu1",
            tool_input={"file_path": "x.py"},
        )
    )
    sink.flush()

    spans = exporter.get_finished_spans()
    by_name = _by_name(list(spans))
    # Span names are now composed per the convention: "invoke_agent {agent}",
    # "execute_tool {tool}" — never the bare subject passed in.
    root = by_name["invoke_agent session:s1"]
    tool = by_name["execute_tool Read"]
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
    assert tool.attributes["gen_ai.tool.call.id"] == "tu1"
    assert json.loads(tool.attributes["gen_ai.tool.call.arguments"]) == {"file_path": "x.py"}
    # Explicit historical timing survives the round trip (ns precision).
    assert root.start_time == int(_ts("2026-07-08T10:00:00").timestamp() * 1_000_000_000)
    assert root.end_time == int(_ts("2026-07-08T10:00:05").timestamp() * 1_000_000_000)


def test_sink_flush_refuses_an_unaccepted_processor_result(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    exporter, _processor, resource = in_memory

    class _RefusingProcessor(SimpleSpanProcessor):
        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            del timeout_millis
            return False

    sink = sink_from_processor(_RefusingProcessor(exporter), resource)

    with pytest.raises(RuntimeError, match="did not flush"):
        sink.flush()


# ─── disabled / misconfigured → no-op ────────────────────────────────────────


def test_build_span_sink_noop_when_disabled() -> None:
    assert build_span_sink(TelemetryConfig(enabled=False)) is None


def test_trace_transport_prefers_the_signal_specific_collector() -> None:
    assert _trace_transport(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://langfuse.example/api/public/otel",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=generic",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:4318/v1/traces",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=collector",
        }
    ) == ("http://127.0.0.1:4318/v1/traces", "Authorization=collector")


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


def test_build_span_sink_noop_when_credentials_unresolved(warnings_logged: list[str]) -> None:
    """Enabled-but-unresolved is the one direction nobody looks in, so it is
    LOUD. `derive_env` warns naming the variables that failed; this tier adds
    the consequence it alone knows — Grove's own exporter is dead."""
    cfg = TelemetryConfig(enabled=True)
    assert build_span_sink(cfg, env={}) is None
    assert any("trace export stays a no-op" in message for message in warnings_logged)


def test_build_span_sink_says_nothing_when_telemetry_is_disabled(
    warnings_logged: list[str],
) -> None:
    """Opting out is not a fault — a disabled config must not nag."""
    assert build_span_sink(TelemetryConfig(enabled=False), env={}) is None
    assert warnings_logged == []


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


def test_plan_disambiguates_identical_normalized_messages() -> None:
    timestamp = _ts("2026-07-08T10:00:01")
    duplicate = AgentMessage(
        role="assistant",
        model="codex-x",
        timestamp=timestamp,
        content=(ContentBlock(type="thinking"),),
    )
    messages = (
        AgentMessage(
            role="user",
            timestamp=_ts("2026-07-08T10:00:00"),
            content=(ContentBlock(type="text", text="work"),),
        ),
        duplicate,
        duplicate,
        AgentMessage(
            role="assistant",
            model="codex-x",
            timestamp=_ts("2026-07-08T10:00:02"),
            content=(ContentBlock(type="text", text="done"),),
        ),
    )

    (manifest,) = TraceInstrumentor(TelemetryConfig(enabled=True), sink=lambda record: None).plan(
        Path("/work"), "session-identical", _FakeAdapter(messages)
    )

    assert len(manifest.observation_ids) == len(manifest.spans)


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
    # share the name "chat claude-x" here, so a name-keyed lookup would
    # silently collide on the wrong span.
    ids = {s.context.span_id: s for s in spans if s.context is not None}
    assert {root_id, gen1_id, gen2_id, tool_id} == set(ids)

    root = ids[root_id]
    assert root.context is not None and root.context.trace_id == trace_id
    assert root.parent is None
    assert root.name == f"invoke_agent session:{session_id}"
    assert root.attributes is not None
    # The turn root carries the session's own identity and sits at the top
    # of the spawn tree.
    assert root.attributes["gen_ai.agent.id"] == session_id
    assert root.attributes["grove.agent.depth"] == 0
    # Root span spans the WHOLE session (first to last timestamp).
    assert root.start_time == int(_ts("2026-07-08T10:00:00").timestamp() * 1_000_000_000)
    assert root.end_time == int(_ts("2026-07-08T10:00:03").timestamp() * 1_000_000_000)

    gen1 = ids[gen1_id]
    assert gen1.parent is not None and gen1.parent.span_id == root_id
    assert gen1.attributes is not None
    assert gen1.attributes["langfuse.observation.type"] == "generation"
    assert gen1.name == "chat claude-x"

    # The human's prompt is the first generation's input. A turn that produced
    # only a tool call has no PROSE, but its call is still part of the
    # completion — the exact gap the structured message document exists to
    # close, so this now carries the call as compact JSON rather than being
    # dropped.
    assert gen1.attributes["langfuse.observation.input"] == "do it"
    assert json.loads(gen1.attributes["langfuse.observation.output"]) == {
        "tool_call": "Read",
        "arguments": {"a": 1},
    }
    # The portable convention keys carry the same derived text.
    assert gen1.attributes["gen_ai.prompt"] == "do it"
    assert gen1.attributes["gen_ai.completion"] == gen1.attributes["langfuse.observation.output"]

    gen2 = ids[gen2_id]
    assert gen2.parent is not None and gen2.parent.span_id == root_id
    assert gen2.attributes is not None
    # The tool output that re-entered the loop is the next generation's input.
    assert gen2.attributes["langfuse.observation.input"] == "ok"
    assert gen2.attributes["langfuse.observation.output"] == "done"

    tool = ids[tool_id]
    assert tool.parent is not None and tool.parent.span_id == gen1_id
    assert tool.attributes is not None
    assert tool.name == "execute_tool Read"
    assert json.loads(tool.attributes["langfuse.observation.input"]) == {"a": 1}
    assert tool.attributes["langfuse.observation.output"] == "ok"
    assert json.loads(tool.attributes["gen_ai.tool.call.arguments"]) == {"a": 1}
    assert tool.attributes["gen_ai.tool.call.result"] == "ok"
    # tool_result's timestamp closes the tool span, not the call time.
    assert tool.start_time == int(_ts("2026-07-08T10:00:01").timestamp() * 1_000_000_000)
    assert tool.end_time == int(_ts("2026-07-08T10:00:02").timestamp() * 1_000_000_000)


def test_replay_is_idempotent_on_reingestion(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    """A COLD instrumentor (a restarted daemon, a re-run backfill) replays the
    whole session and yields the SAME ids as the first run — LangFuse's OTLP
    ingest de-dupes on that, so it never double-records. The watermark is a
    cost control layered on top and must not be what makes replay safe."""
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    messages = (
        AgentMessage(
            role="assistant",
            message_id="m1",
            timestamp=_ts("2026-07-08T10:00:00"),
            content=(ContentBlock(type="text", text="hi"),),
        ),
    )
    adapter = _FakeAdapter(messages)
    TraceInstrumentor(cfg, sink=sink).replay(Path("/work"), "sess-repeat", adapter)
    TraceInstrumentor(cfg, sink=sink).replay(Path("/work"), "sess-repeat", adapter)

    spans = list(exporter.get_finished_spans())
    ids = [(s.context.trace_id, s.context.span_id) for s in spans if s.context is not None]
    assert len(ids) == 4  # 2 replays x (root + 1 generation)
    assert len(set(ids)) == 2  # but only 2 DISTINCT (trace_id, span_id) pairs


# ─── the per-session watermark ───────────────────────────────────────────────


def test_replay_re_emits_nothing_when_the_session_has_not_moved(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    """The activity tick calls this per session per poll; an idle session must
    cost zero exports, not a full re-send of its whole history."""
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    adapter = _FakeAdapter(
        (
            AgentMessage(
                role="assistant",
                message_id="m1",
                timestamp=_ts("2026-07-08T10:00:00"),
                content=(ContentBlock(type="text", text="hi"),),
            ),
        )
    )
    instrumentor.replay(Path("/work"), "sess-quiet", adapter)
    first = len(exporter.get_finished_spans())
    instrumentor.replay(Path("/work"), "sess-quiet", adapter)
    instrumentor.replay(Path("/work"), "sess-quiet", adapter)
    assert len(exporter.get_finished_spans()) == first


def test_replay_emits_only_the_new_immutable_turn_on_a_repeat_call(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    session_id = "sess-grow"
    first_prompt = AgentMessage(
        role="user",
        timestamp=_ts("2026-07-08T09:59:59"),
        content=(ContentBlock(type="text", text="first"),),
    )
    first_turn = AgentMessage(
        role="assistant",
        message_id="m1",
        timestamp=_ts("2026-07-08T10:00:00"),
        content=(ContentBlock(type="text", text="hi"),),
    )
    second_prompt = AgentMessage(
        role="user",
        timestamp=_ts("2026-07-08T10:00:04"),
        content=(ContentBlock(type="text", text="second"),),
    )
    second_turn = AgentMessage(
        role="assistant",
        message_id="m2",
        timestamp=_ts("2026-07-08T10:00:05"),
        content=(ContentBlock(type="text", text="more"),),
    )
    instrumentor.replay(Path("/work"), session_id, _FakeAdapter((first_prompt, first_turn)))
    exporter.clear()
    all_messages = (first_prompt, first_turn, second_prompt, second_turn)
    adapter = _FakeAdapter(all_messages)
    expected = instrumentor.plan(Path("/work"), session_id, adapter)[1]
    instrumentor.replay(Path("/work"), session_id, adapter)

    emitted = {s.context.span_id for s in exporter.get_finished_spans() if s.context is not None}
    assert emitted == {span.span_id for span in expected.spans}


def test_replay_withholds_an_open_tool_turn_then_emits_it_once_complete(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    """An open tool span is never exported and therefore never mutated later."""
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=sink)
    session_id = "sess-late"
    call = AgentMessage(
        role="assistant",
        message_id="m1",
        timestamp=_ts("2026-07-08T10:00:00"),
        content=(ContentBlock(type="tool_use", tool_name="Read", tool_use_id="tu1"),),
    )
    result = AgentMessage(
        role="tool",
        timestamp=_ts("2026-07-08T10:00:09"),
        content=(ContentBlock(type="tool_result", tool_use_id="tu1", text="file body"),),
    )
    instrumentor.replay(Path("/work"), session_id, _FakeAdapter((call,)))
    instrumentor.replay(Path("/work"), session_id, _FakeAdapter((call, result)))
    assert exporter.get_finished_spans() == ()
    final = AgentMessage(
        role="assistant",
        message_id="m2",
        timestamp=_ts("2026-07-08T10:00:10"),
        content=(ContentBlock(type="text", text="done"),),
    )
    exporter.clear()
    instrumentor.replay(Path("/work"), session_id, _FakeAdapter((call, result, final)))

    tool_span_id = derive_span_id(session_id, "tool", "tu1")
    ids = {s.context.span_id: s for s in exporter.get_finished_spans() if s.context is not None}
    assert tool_span_id in ids
    tool = ids[tool_span_id]
    assert tool.attributes is not None
    assert tool.attributes["langfuse.observation.output"] == "file body"
    assert tool.end_time == int(_ts("2026-07-08T10:00:09").timestamp() * 1_000_000_000)


def test_replay_advances_the_watermark_only_on_a_successful_export(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
) -> None:
    """A sink that blew up mid-replay leaves nothing recorded upstream, so the
    next call must treat the whole session as unsent."""
    _, processor, resource = in_memory
    real = sink_from_processor(processor, resource)
    failures = {"left": 1}

    class _FlakySink:
        def __call__(self, record: SpanRecord) -> None:
            if failures["left"]:
                failures["left"] -= 1
                raise RuntimeError("export blew up")
            real(record)

        def flush(self) -> None:
            real.flush()

    exporter = in_memory[0]
    instrumentor = TraceInstrumentor(TelemetryConfig(enabled=True), sink=_FlakySink())
    adapter = _FakeAdapter(
        (
            AgentMessage(
                role="assistant",
                message_id="m1",
                timestamp=_ts("2026-07-08T10:00:00"),
                content=(ContentBlock(type="text", text="hi"),),
            ),
        )
    )
    instrumentor.replay(Path("/work"), "sess-flaky", adapter)  # never raises
    assert exporter.get_finished_spans() == ()
    instrumentor.replay(Path("/work"), "sess-flaky", adapter)
    assert len(exporter.get_finished_spans()) == 2  # root + generation, retried whole


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
    cwd = Path("/home/dev/work/fleet-trace")
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
            '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:04.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"tu1","content":"Auth uses cookies."}]}}',
            '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:05.000Z","message":{"id":"m2","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}',
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
    assert subagent_span.name == "invoke_agent Explore"
    assert subagent_span.context is not None and subagent_span.context.trace_id == trace_id
    # The nested agent's parent is the exact TOOL span the main thread's
    # "Agent" tool_use produced — not a message-id-derived link.
    assert subagent_span.parent is not None
    assert subagent_span.parent.span_id == tool_span_id
    assert subagent_span.attributes is not None
    # One level below the turn root, and identified by its own thread id.
    assert subagent_span.attributes["grove.agent.depth"] == 1
    assert subagent_span.attributes["gen_ai.agent.id"] == "agent01"


def test_replay_nests_a_sub_agents_own_sub_agent_two_levels_deep(
    in_memory: tuple[InMemorySpanExporter, SimpleSpanProcessor, Resource],
    adapter: ClaudeCodeAdapter,
    claude_home: Path,
) -> None:
    """A sub-agent that itself spawns a sub-agent must not lose the whole
    subtree — the exact regression `_attachments`' unbounded walk exists to
    fix. Three `invoke_agent` spans at depth 0/1/2, each nested under the
    `execute_tool` span for the call that spawned it, at ANY depth."""
    exporter, processor, resource = in_memory
    sink = sink_from_processor(processor, resource)
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=sink)

    sid = "24242424-2424-4242-8242-242424242424"
    cwd = Path("/home/dev/work/deep-fleet-trace")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-02T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-02T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"Explore the auth flow","subagent_type":"Explore"}}]}}',
            '{"type":"user","uuid":"u2","timestamp":"2026-06-02T10:00:07.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"tu1","content":"Auth uses cookies."}]}}',
            '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
            '"timestamp":"2026-06-02T10:00:08.000Z","message":{"id":"m2","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}',
        ],
    )
    # agent01 is the direct child (depth 1) — and itself spawns tu2, whose
    # thread (agent02) must attach at depth 2 without any extra scaffolding
    # beyond a second `_write_subagent` call sharing the same top session id.
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent01",
        [
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-02T10:00:02.000Z","message":{"id":"sm1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu2","name":"Agent",'
            '"input":{"description":"Trace the session table","subagent_type":"Explore"}}]}}',
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-02T10:00:05.000Z","message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"tu2",'
            '"content":"Session table uses Postgres."}]}}',
            '{"type":"assistant","uuid":"sa2","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-02T10:00:06.000Z","message":{"id":"sm2","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text",'
            '"text":"Auth uses session cookies. Session table uses Postgres."}]}}',
        ],
        meta={"agentType": "Explore", "description": "Explore the auth flow", "toolUseId": "tu1"},
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent02",
        [
            '{"type":"assistant","uuid":"ssa1","isSidechain":true,"agentId":"agent02",'
            '"timestamp":"2026-06-02T10:00:04.000Z","message":{"id":"dm1","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
            '"usage":{"input_tokens":10,"output_tokens":8},'
            '"content":[{"type":"text","text":"Session table uses Postgres."}]}}',
        ],
        meta={"agentType": "Explore", "description": "Trace the session table", "toolUseId": "tu2"},
    )

    instrumentor.replay(cwd, sid, adapter)

    spans = list(exporter.get_finished_spans())
    trace_id = derive_trace_id(sid)
    root_span_id = derive_span_id(sid, "agent", "root")
    tool_tu1_id = derive_span_id(sid, "tool", "tu1")
    tool_tu2_id = derive_span_id(sid, "tool", "tu2")
    agent01_span_id = derive_span_id(sid, "agent", "agent01")
    agent02_span_id = derive_span_id(sid, "agent", "agent02")

    ids = {s.context.span_id: s for s in spans if s.context is not None}
    for expected in (root_span_id, tool_tu1_id, agent01_span_id, tool_tu2_id, agent02_span_id):
        assert expected in ids

    root = ids[root_span_id]
    agent01 = ids[agent01_span_id]
    agent02 = ids[agent02_span_id]
    assert root.context is not None and root.context.trace_id == trace_id
    assert agent01.context is not None and agent01.context.trace_id == trace_id
    assert agent02.context is not None and agent02.context.trace_id == trace_id

    assert root.attributes is not None and root.attributes["grove.agent.depth"] == 0
    assert agent01.attributes is not None and agent01.attributes["grove.agent.depth"] == 1
    assert agent02.attributes is not None and agent02.attributes["grove.agent.depth"] == 2
    assert agent02.attributes["gen_ai.agent.id"] == "agent02"

    # Each level nests under the TOOL span of the call that spawned it, not
    # under its grandparent agent span directly.
    assert agent01.parent is not None and agent01.parent.span_id == tool_tu1_id
    assert agent02.parent is not None and agent02.parent.span_id == tool_tu2_id
    assert root.parent is None


def test_planned_manifest_never_orphans_a_sub_agent_parent(
    adapter: ClaudeCodeAdapter,
    claude_home: Path,
) -> None:
    """No span in a planned manifest may name a ``parent_span_id`` that is not
    itself the ``span_id`` of some span in that SAME manifest.

    This is the exact defect observed in production: an orphaned sub-agent
    span renders as its own trace root in a viewer that cannot resolve its
    parent, indistinguishable from a genuinely top-level trace. Pinned
    directly over the pure ``plan()`` seam — no OTel sink, no SDK parent
    plumbing — so nothing here can paper over a real dangling reference.
    """
    cfg = TelemetryConfig(enabled=True)
    instrumentor = TraceInstrumentor(cfg, sink=lambda record: None)

    sid = "34343434-3434-4343-8343-343434343434"
    cwd = Path("/home/dev/work/no-orphans")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-03T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-03T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"Explore the auth flow","subagent_type":"Explore"}}]}}',
            '{"type":"user","uuid":"u2","timestamp":"2026-06-03T10:00:04.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"tu1","content":"Auth uses cookies."}]}}',
            '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
            '"timestamp":"2026-06-03T10:00:05.000Z","message":{"id":"m2","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent01",
        [
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-03T10:00:03.000Z","message":{"id":"sm1","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
            '"content":[{"type":"text","text":"Auth uses session cookies."}]}}',
        ],
        meta={"agentType": "Explore", "description": "Explore the auth flow", "toolUseId": "tu1"},
    )

    (manifest,) = instrumentor.plan(cwd, sid, adapter)

    span_ids = {span.span_id for span in manifest.spans}
    # The fixture actually exercises a sub-agent, or the invariant below would
    # pass vacuously.
    assert any(span.kind == "agent" and span.parent_span_id is not None for span in manifest.spans)
    for span in manifest.spans:
        if span.parent_span_id is not None:
            assert span.parent_span_id in span_ids
