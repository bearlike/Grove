"""The OTLP gateway, driven from CAPTURED Claude Code payloads.

``tests/core/data/otlp_claude_code/batch-0*.binpb`` are the five real export
batches a live ``claude`` 2.1.227 produced on 2026-08-11 against a throwaway
OTLP sink (``OTEL_TRACES_EXPORT_INTERVAL=2000``, content logging on, one Bash
call and one sub-agent). Only ``user.id`` and ``session.id`` were overwritten
with fixed values; every span name, event name, attribute key, parent link and
BATCH BOUNDARY is as it arrived.

The batch boundaries are the point. A hand-authored payload would have put the
root in the first batch, because that is what a person writing a fixture
assumes — and the whole reason this tier exists is that the root arrives LAST.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, Span

from grove.core.telemetry._grouping import TraceBuffer
from grove.core.telemetry._otlp import OtlpAttributes, SpanEnvelope
from grove.core.telemetry.claude_code import ClaudeCodeSpan
from grove.core.telemetry.receiver import OtlpIngest, TraceGateway, build_receiver_app
from grove.core.telemetry.semconv import GenAiAttr, GroveLiveAttr, LangfuseAttr

DATA = Path(__file__).parent / "data" / "otlp_claude_code"
T0 = datetime(2026, 8, 11, 5, 34, tzinfo=UTC)


def captured_batches() -> list[list[ResourceSpans]]:
    """The five real export batches, in the order the agent sent them."""
    batches = []
    for path in sorted(DATA.glob("batch-*.binpb")):
        request = ExportTraceServiceRequest.FromString(path.read_bytes())
        batches.append(list(request.resource_spans))
    return batches


def spans_of(groups: Sequence[ResourceSpans]) -> list[Span]:
    return [span for rs in groups for ss in rs.scope_spans for span in ss.spans]


def attrs_of(span: Span) -> dict[str, object]:
    return dict(OtlpAttributes.scalars(span.attributes))


def feed_all(gateway: TraceGateway) -> list[Span]:
    """Push every captured batch through, one export interval apart."""
    emitted: list[Span] = []
    for index, batch in enumerate(captured_batches()):
        outcome = gateway.accept(batch, now=T0 + timedelta(seconds=2 * index))
        emitted.extend(spans_of(outcome.resource_spans))
    return emitted


@pytest.fixture
def gateway() -> TraceGateway:
    return TraceGateway()


# ─── the capture itself, so a regenerated fixture cannot silently change ─────


def test_the_captured_payload_still_has_the_shape_the_transform_assumes() -> None:
    """Pins the SOURCE facts every mapping below depends on.

    Without this, regenerating the fixture against a future Claude Code release
    that renamed ``user_prompt`` would leave the transform tests green while the
    transform silently stopped recovering anything — each one asserts on Grove's
    output, and an output built from an absent input is merely sparse.
    """
    batches = captured_batches()
    assert len(batches) == 5, "the batch boundaries are the evidence; do not merge them"
    everything = [span for batch in batches for span in spans_of(batch)]
    by_name = {span.name for span in everything}
    assert ClaudeCodeSpan.INTERACTION in by_name
    assert ClaudeCodeSpan.LLM_REQUEST in by_name
    assert ClaudeCodeSpan.TOOL in by_name

    root = next(s for s in everything if s.name == ClaudeCodeSpan.INTERACTION)
    assert not root.parent_span_id, "the interaction span is the turn root"
    assert "user_prompt" in attrs_of(root), "the prompt rides an attribute, unmapped"

    tool = next(
        s
        for s in everything
        if s.name == ClaudeCodeSpan.TOOL and attrs_of(s).get("tool_name") == "Bash"
    )
    event = next(e for e in tool.events if e.name == ClaudeCodeSpan.TOOL_OUTPUT_EVENT)
    assert "output" in OtlpAttributes.scalars(event.attributes), (
        "the tool body rides a span EVENT, which attribute-only consumers discard"
    )


def test_the_root_arrives_last_which_is_the_defect_the_buffer_exists_for() -> None:
    """The measured batch race: children ship batches before their parent."""
    batches = captured_batches()
    seen: set[bytes] = set()
    orphaned_at_ingest = 0
    root_batch = None
    for index, batch in enumerate(batches):
        ids = {span.span_id for span in spans_of(batch)}
        for span in spans_of(batch):
            if not span.parent_span_id:
                root_batch = index
            elif span.parent_span_id not in seen and span.parent_span_id not in ids:
                orphaned_at_ingest += 1
        seen |= ids
    assert root_batch == len(batches) - 1, "the turn root closes last, by construction"
    assert orphaned_at_ingest >= 5, (
        "a per-batch consumer sees these spans with no parent and promotes one to root"
    )


# ─── the pure core ───────────────────────────────────────────────────────────


def test_an_inverted_hierarchy_comes_out_correctly_rooted(gateway: TraceGateway) -> None:
    """Every emitted span reaches the turn root by parent links alone."""
    emitted = feed_all(gateway)
    assert len(emitted) == 19

    parents = {span.span_id: span.parent_span_id for span in emitted}
    roots = [span_id for span_id, parent in parents.items() if not parent]
    assert len(roots) == 1, "exactly one parentless span, or a backend picks a leaf"

    for span_id in parents:
        walked = span_id
        for _ in range(len(parents) + 1):
            if not parents[walked]:
                break
            walked = parents[walked]
        assert not parents[walked], "every span must terminate at the one root"
        assert walked == roots[0]


def test_the_turn_root_carries_the_prompt_that_was_riding_an_unmapped_key(
    gateway: TraceGateway,
) -> None:
    emitted = feed_all(gateway)
    root = next(span for span in emitted if not span.parent_span_id)
    attributes = attrs_of(root)

    assert attributes[GenAiAttr.OPERATION_NAME] == "invoke_agent"
    assert attributes[LangfuseAttr.OBSERVATION_TYPE] == "agent"
    assert attributes[GroveLiveAttr.AGENT_DEPTH] == 0
    # The three keys a consumer actually reads input from, all absent before.
    assert "grove-otlp-probe" in str(attributes[LangfuseAttr.OBSERVATION_INPUT])
    assert "grove-otlp-probe" in str(attributes[GenAiAttr.PROMPT])
    document = json.loads(str(attributes[GenAiAttr.INPUT_MESSAGES]))
    assert document[0]["role"] == "user"


def test_a_tool_body_event_becomes_a_tool_body_attribute(gateway: TraceGateway) -> None:
    emitted = feed_all(gateway)
    bash = next(
        span
        for span in emitted
        if attrs_of(span).get(GenAiAttr.TOOL_NAME) == "Bash"
        and attrs_of(span).get(GenAiAttr.TOOL_CALL_RESULT)
    )
    attributes = attrs_of(bash)

    assert attributes[GenAiAttr.OPERATION_NAME] == "execute_tool"
    assert attributes[GenAiAttr.TOOL_CALL_RESULT] == "grove-otlp-probe"
    assert attributes[LangfuseAttr.OBSERVATION_OUTPUT] == "grove-otlp-probe"
    # Arguments come from BOTH the span (`full_command`) and the event
    # (`bash_command`) — neither alone is the whole call.
    arguments = json.loads(str(attributes[GenAiAttr.TOOL_CALL_ARGUMENTS]))
    assert arguments["full_command"] == "echo grove-otlp-probe"
    assert arguments["bash_command"] == "echo grove-otlp-probe"
    assert attributes[GenAiAttr.TOOL_CALL_ID].startswith("call_")


def test_a_tool_call_with_no_result_yet_emits_no_output_key(gateway: TraceGateway) -> None:
    """Absent is not empty — the rule ``SpanRecord.tool`` already encodes."""
    emitted = feed_all(gateway)
    agent_calls = [span for span in emitted if attrs_of(span).get(GenAiAttr.TOOL_NAME) == "Agent"]
    assert agent_calls, "the capture contains two Agent tool calls"
    for span in agent_calls:
        attributes = attrs_of(span)
        assert GenAiAttr.TOOL_CALL_RESULT not in attributes
        assert LangfuseAttr.OBSERVATION_OUTPUT not in attributes


def test_a_failed_tool_is_levelled_from_its_execution_child(gateway: TraceGateway) -> None:
    """``success=False`` lands on a DIFFERENT span from the tool's identity."""
    emitted = feed_all(gateway)
    errored = [
        span for span in emitted if attrs_of(span).get(LangfuseAttr.OBSERVATION_LEVEL) == "ERROR"
    ]
    assert len(errored) == 1
    assert attrs_of(errored[0])[GenAiAttr.TOOL_NAME] == "Agent"


def test_a_sub_agent_becomes_an_invoke_agent_observation_at_depth_one(
    gateway: TraceGateway,
) -> None:
    emitted = feed_all(gateway)
    sub_agents = [
        span
        for span in emitted
        if attrs_of(span).get(LangfuseAttr.OBSERVATION_TYPE) == "agent"
        and attrs_of(span).get(GroveLiveAttr.AGENT_DEPTH) == 1
    ]
    assert len(sub_agents) == 2, "one invoke_agent per Agent tool call"
    for span in sub_agents:
        attributes = attrs_of(span)
        assert attributes[GenAiAttr.AGENT_NAME] == "general-purpose"
        assert span.name == "invoke_agent general-purpose"

    # The spawn edge: the sub-agent's own work hangs beneath the invoke_agent.
    by_id = {span.span_id: span for span in emitted}
    spawned = [span for span in emitted if span.parent_span_id in {s.span_id for s in sub_agents}]
    assert spawned, "the sub-agent's generations parent to the spawn, not to the turn"
    assert all(by_id[span.parent_span_id] in sub_agents for span in spawned)


def test_generation_usage_and_ttft_survive_the_transform(gateway: TraceGateway) -> None:
    emitted = feed_all(gateway)
    generations = [
        span
        for span in emitted
        if attrs_of(span).get(LangfuseAttr.OBSERVATION_TYPE) == "generation"
    ]
    assert len(generations) == 6

    first = attrs_of(generations[0])
    # The 1-vs-78395 defect: the counts are right on the wire and were read
    # from the wrong key downstream. Assert against the SOURCE value, not a
    # literal, so a re-capture cannot make this pass by agreeing with itself.
    source = next(
        s
        for s in spans_of([rs for batch in captured_batches() for rs in batch])
        if s.span_id == generations[0].span_id
    )
    assert first[GenAiAttr.USAGE_INPUT_TOKENS] == attrs_of(source)["input_tokens"]
    assert first[GenAiAttr.USAGE_INPUT_TOKENS] > 1000
    assert first[GenAiAttr.USAGE_OUTPUT_TOKENS] == attrs_of(source)["output_tokens"]
    assert json.loads(str(first[LangfuseAttr.OBSERVATION_USAGE_DETAILS]))["input"] > 1000
    assert first[LangfuseAttr.OBSERVATION_COMPLETION_START_TIME]
    assert first[GenAiAttr.RESPONSE_MODEL] == attrs_of(source)["model"]


def test_a_boolean_source_attribute_never_becomes_a_token_count() -> None:
    """``success`` is a bool and ``bool`` is an ``int`` — the classic silent 1."""
    gateway = TraceGateway()
    emitted = feed_all(gateway)
    for span in emitted:
        counts = [value for key, value in attrs_of(span).items() if key.startswith("gen_ai.usage.")]
        assert all(not isinstance(value, bool) for value in counts)


def test_spans_from_an_unknown_source_pass_through_untouched() -> None:
    """A gateway in front of a mixed fleet must be a proxy, not a filter."""
    gateway = TraceGateway()
    foreign = ResourceSpans()
    scope_spans = foreign.scope_spans.add()
    scope_spans.scope.name = "some.other.tracer"
    span = scope_spans.spans.add()
    span.name = "not-anthropics"
    span.trace_id = b"\x11" * 16
    span.span_id = b"\x22" * 8
    original = span.SerializeToString()

    outcome = gateway.accept([foreign], now=T0)
    emitted = spans_of(outcome.resource_spans)
    assert len(emitted) == 1
    assert emitted[0].SerializeToString() == original


# ─── the buffer's bounds ─────────────────────────────────────────────────────


def test_an_orphaned_child_is_held_and_then_flushed_on_age() -> None:
    """A root that never comes must not strand its children in memory."""
    buffer = TraceBuffer(max_age=timedelta(seconds=30))
    gateway = TraceGateway(buffer=buffer)

    first = captured_batches()[0]
    outcome = gateway.accept(first, now=T0)
    assert outcome.resource_spans == (), "nothing leaves before the root lands"
    assert outcome.held == 4

    late = gateway.drain(now=T0 + timedelta(seconds=31))
    assert len(spans_of(late.resource_spans)) == 4
    assert late.held == 0


def test_a_rootless_release_leaves_the_hierarchy_exactly_as_received() -> None:
    """With no root resolved, inventing one repeats the original defect."""
    buffer = TraceBuffer(max_age=timedelta(seconds=1))
    gateway = TraceGateway(buffer=buffer)
    first = captured_batches()[0]
    before = {s.span_id: s.parent_span_id for s in spans_of(first)}

    gateway.accept(first, now=T0)
    released = gateway.drain(now=T0 + timedelta(seconds=5))
    after = {s.span_id: s.parent_span_id for s in spans_of(released.resource_spans)}
    assert after == before


def test_a_trace_over_its_span_ceiling_releases_rather_than_dropping() -> None:
    buffer = TraceBuffer(max_spans_per_trace=2, max_age=timedelta(hours=1))
    gateway = TraceGateway(buffer=buffer)
    outcome = gateway.accept(captured_batches()[0], now=T0)
    assert len(spans_of(outcome.resource_spans)) == 4, "flush what you have, never shed"


def test_the_buffer_does_not_leak_an_entry_per_trace_ever_seen() -> None:
    buffer = TraceBuffer(max_age=timedelta(seconds=10))
    envelope = SpanEnvelope.unpack(captured_batches()[0])[:1]
    buffer.offer(envelope, now=T0)
    buffer.drain(now=T0 + timedelta(seconds=11))
    assert buffer.held_spans == 0
    # A second drain long after must be a no-op rather than re-releasing.
    assert buffer.drain(now=T0 + timedelta(hours=1)) == ()


def test_force_drain_releases_a_trace_that_has_not_aged_out() -> None:
    buffer = TraceBuffer(max_age=timedelta(hours=1))
    buffer.offer(SpanEnvelope.unpack(captured_batches()[0]), now=T0)
    released = buffer.drain(now=T0, force=True)
    assert sum(len(trace.envelopes) for trace in released) == 4


# ─── the ASGI shell ──────────────────────────────────────────────────────────


class RecordingSink:
    """Captures what the gateway forwarded. A list would not record CALLS."""

    def __init__(self) -> None:
        self.groups: list[Sequence[ResourceSpans]] = []

    def __call__(self, resource_spans: Sequence[ResourceSpans]) -> None:
        self.groups.append(resource_spans)

    @property
    def spans(self) -> list[Span]:
        return [span for group in self.groups for span in spans_of(group)]


@pytest.fixture
def client() -> Iterator[tuple[object, RecordingSink, OtlpIngest]]:
    sink = RecordingSink()
    ingest = OtlpIngest(sink=sink, idle_drain=timedelta(milliseconds=50))
    with TestClient(build_receiver_app(ingest=ingest)) as test_client:
        yield test_client, sink, ingest


def _post(test_client: object, batch: Sequence[ResourceSpans]) -> ExportTraceServiceResponse:
    request = ExportTraceServiceRequest(resource_spans=list(batch))
    response = test_client.post(  # type: ignore[attr-defined]
        "/v1/traces",
        content=request.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 200
    return ExportTraceServiceResponse.FromString(response.content)


def test_the_receiver_transforms_a_posted_capture_end_to_end(
    client: tuple[object, RecordingSink, OtlpIngest],
) -> None:
    test_client, sink, _ = client
    for batch in captured_batches():
        body = _post(test_client, batch)
        assert not body.partial_success.rejected_spans

    deadline = time.monotonic() + 5
    while len(sink.spans) < 19 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(sink.spans) == 19

    root = next(span for span in sink.spans if not span.parent_span_id)
    assert "grove-otlp-probe" in str(attrs_of(root)[LangfuseAttr.OBSERVATION_INPUT])


def test_a_full_queue_sheds_and_reports_partial_success_rather_than_growing() -> None:
    """Backpressure on telemetry must never become backpressure on an agent."""
    ingest = OtlpIngest(sink=RecordingSink(), queue_capacity=1)
    batch = captured_batches()[0]

    async def flood() -> list[int]:
        # No worker is started here, so nothing drains: the first put fills the
        # queue and every later one must be refused, not awaited.
        return [await ingest.submit(batch, spans=4) for _ in range(4)]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "start", _noop)
        rejected = asyncio.run(flood())

    assert rejected[0] == 0
    assert rejected[1:] == [4, 4, 4], "a full queue sheds; it does not grow"
    assert ingest.shed_spans == 12


def test_a_shed_batch_is_reported_in_the_otlp_response_body(
    client: tuple[object, RecordingSink, OtlpIngest],
) -> None:
    test_client, _, ingest = client
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "submit", _refuse_everything)
        body = _post(test_client, captured_batches()[0])
    assert body.partial_success.rejected_spans == 4
    assert "queue full" in body.partial_success.error_message


def test_an_undecodable_body_is_a_400_and_not_a_dead_receiver(
    client: tuple[object, RecordingSink, OtlpIngest],
) -> None:
    test_client, _, _ = client
    response = test_client.post(  # type: ignore[attr-defined]
        "/v1/traces",
        content=b"\xff\xff not protobuf",
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 400
    assert _post(test_client, captured_batches()[0]).partial_success.rejected_spans == 0


def test_the_logs_endpoint_accepts_rather_than_404ing(
    client: tuple[object, RecordingSink, OtlpIngest],
) -> None:
    """#500 owns the transform; a 404 here silently loses Codex's prompts."""
    test_client, _, _ = client
    response = test_client.post(  # type: ignore[attr-defined]
        "/v1/logs", content=b"anything", headers={"content-type": "application/x-protobuf"}
    )
    assert response.status_code == 200


def test_the_transform_never_runs_on_the_event_loop(
    client: tuple[object, RecordingSink, OtlpIngest],
) -> None:
    """Structural: the gateway is only ever entered from a pool thread.

    Asserted on the THREAD NAME rather than by timing, because a fast transform
    on the loop looks identical to a correct one under a stopwatch.
    """
    test_client, _sink, ingest = client
    threads: list[str] = []
    original = ingest.gateway.accept

    def spy(*args: object, **kwargs: object) -> object:
        threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest.gateway, "accept", spy)
        for batch in captured_batches():
            _post(test_client, batch)
        deadline = time.monotonic() + 5
        while len(threads) < 5 and time.monotonic() < deadline:
            time.sleep(0.02)

    assert threads, "the gateway was never entered"
    assert all(name.startswith("grove-otlp") for name in threads), threads
    assert threading.current_thread().name not in threads


async def _noop() -> None:
    """Stand-in for ``OtlpIngest.start`` so a flood test drains nothing."""


async def _refuse_everything(_: object, *, spans: int) -> int:
    """Stand-in for ``OtlpIngest.submit`` that sheds whatever it is handed."""
    return spans


def test_a_tag_list_crosses_the_protobuf_boundary_as_a_string_array() -> None:
    """`langfuse.trace.tags` is specified as `string[]`, so it is the one
    attribute Grove writes that is not a scalar.

    Before the array arm existed a tuple fell through to `string_value`, which
    protobuf rejects — and only the re-export tier could reach it, and only for
    a span Grove had stamped tags onto, which is narrow enough to have shipped
    unnoticed. The clear-before-fill half matters just as much: `apply`
    overwrites in place, so re-stamping a span whose key already held an array
    would append rather than replace, and a tag list that grows by one copy of
    itself per export is a bug that only appears under retry.
    """
    span = Span()
    OtlpAttributes.apply(span, {"langfuse.trace.tags": ("grove", "repo:proj")})
    OtlpAttributes.apply(span, {"langfuse.trace.tags": ("grove", "repo:proj")})

    attribute = next(a for a in span.attributes if a.key == "langfuse.trace.tags")
    assert [v.string_value for v in attribute.value.array_value.values] == ["grove", "repo:proj"]


def test_scalars_still_round_trip_beside_the_array_arm() -> None:
    span = Span()
    OtlpAttributes.apply(span, {"s": "text", "i": 7, "f": 1.5, "b": False})
    assert OtlpAttributes.scalars(span.attributes) == {"s": "text", "i": 7, "f": 1.5, "b": False}
