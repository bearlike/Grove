"""Claude Code's native OTLP vocabulary, re-spelled in Grove's canonical one.

Everything this module knows about the source side was measured against a live
``claude`` **2.1.227** export on **2026-08-11**, captured by pointing
``OTEL_EXPORTER_OTLP_ENDPOINT`` at a throwaway sink; the captured batches are
checked in under ``tests/core/data/otlp_claude_code/`` and are what the tests
run against. Keys the capture did NOT exercise are marked ``UNVERIFIED`` at
their definition — an inferred key and a measured one are indistinguishable to
the next reader unless the difference is written down.

Three things arrive on the wire and are thrown away by a consumer that reads
only attributes, and recovering them is this module's whole job:

* **The prompt** rides ``user_prompt`` on ``claude_code.interaction``. No
  consumer maps that name, so a trace whose prompt is right there renders with
  ``input: null``.
* **The tool bodies** ride a ``tool.output`` span EVENT. LangFuse's OTLP ingest
  reads span attributes and ignores events, so the single most-wanted payload
  in the trace is delivered and discarded. Hoisting event → attribute is the
  fix, and it is available only to something standing in the data path.
* **The turn's shape** is correct on the wire and destroyed by batching — see
  :mod:`grove.core.telemetry._grouping`.

**The attribute vocabulary is never spelled here.** Every observation is built
through a :class:`~grove.core.trace.SpanRecord` constructor and only its
``name`` and ``attributes`` are lifted onto the protobuf span. That costs a
record object per span for two fields, and buys the guarantee that a span this
gateway emits and a span Grove's own transcript replay emits carry byte-identical
keys — the two tiers describe the same work and a consumer must not have to
know which tier produced a given trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from opentelemetry.proto.common.v1.common_pb2 import InstrumentationScope
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from grove.core.agents.model import TokenUsage
from grove.core.config import TelemetryContent
from grove.core.telemetry._grouping import ReleasedTrace
from grove.core.telemetry._otlp import (
    AttributeValue,
    OtlpAttributes,
    SpanEnvelope,
    ns_to_datetime,
)
from grove.core.telemetry.semconv import (
    ATTR_TEXT_CAP,
    ChatMessage,
    GenAiAttr,
    LangfuseAttr,
)
from grove.core.trace import SpanRecord


class ClaudeCodeAttr:
    """The source-side names, as measured on 2.1.227.

    A namespace rather than loose constants for the same reason
    :class:`~grove.core.telemetry.semconv.GenAiAttr` is one: three vocabularies
    meet in this module and ``MODEL`` alone does not say whose.
    """

    SPAN_TYPE: Final = "span.type"
    SESSION_ID: Final = "session.id"

    USER_PROMPT: Final = "user_prompt"
    INTERACTION_SEQUENCE: Final = "interaction.sequence"

    MODEL: Final = "model"
    TTFT_MS: Final = "ttft_ms"
    INPUT_TOKENS: Final = "input_tokens"
    OUTPUT_TOKENS: Final = "output_tokens"
    CACHE_READ_TOKENS: Final = "cache_read_tokens"
    CACHE_CREATION_TOKENS: Final = "cache_creation_tokens"

    TOOL_NAME: Final = "tool_name"
    TOOL_USE_ID: Final = "tool_use_id"
    SUBAGENT_TYPE: Final = "subagent_type"
    AGENT_ID: Final = "agent_id"
    SUCCESS: Final = "success"

    OUTPUT_EVENT_VALUE: Final = "output"
    """The event attribute carrying a tool's RESULT. Every other attribute on a
    ``tool.output`` event is part of the call's input — measured with
    ``bash_command`` for Bash, and treated as an open set on purpose so a tool
    Grove has never seen contributes its arguments without a code change."""


class ClaudeCodeSpan:
    """Span names Claude Code emits, and the ones Grove re-spells."""

    INTERACTION: Final = "claude_code.interaction"
    LLM_REQUEST: Final = "claude_code.llm_request"
    TOOL: Final = "claude_code.tool"
    TOOL_EXECUTION: Final = "claude_code.tool.execution"

    TOOL_OUTPUT_EVENT: Final = "tool.output"


AGENT_TOOL_NAME: Final = "Agent"
"""``tool_name`` of the call that spawns a sub-agent. The spawn edge in the
tree, and the only place ``subagent_type`` appears."""

_STRUCTURAL_ATTRS: Final = frozenset(
    {
        ClaudeCodeAttr.SPAN_TYPE,
        ClaudeCodeAttr.SESSION_ID,
        ClaudeCodeAttr.TOOL_NAME,
        ClaudeCodeAttr.TOOL_USE_ID,
        ClaudeCodeAttr.SUBAGENT_TYPE,
        ClaudeCodeAttr.AGENT_ID,
        ClaudeCodeAttr.SUCCESS,
        "user.id",
        "terminal.type",
        "duration_ms",
    }
)
"""Attributes on a ``claude_code.tool`` span that describe the CALL rather than
its arguments.

A denylist, not an allowlist of ``full_command``/``file_path``/``skill_name``,
because the argument set is per-tool and grows with every tool Anthropic ships:
an allowlist silently stops carrying a new tool's arguments and nothing fails
when it does. The structural vocabulary, by contrast, is small and changes only
when the span shape itself does — which is a change loud enough to notice.
"""


@dataclass(slots=True, frozen=True)
class _ToolBody:
    """A tool call's arguments and result, gathered from span AND event."""

    arguments: Mapping[str, AttributeValue]
    result: str | None


@dataclass(slots=True, frozen=True)
class _Frame:
    """The identity and timing every observation inherits from its source span.

    Extracted once per span and threaded whole because the five fields are one
    fact — *which span, in which trace, under which parent, over which
    interval* — and a constructor call that spells them out five times is five
    chances to hand ``SpanRecord`` a parent from the previous iteration.
    """

    trace_id: int
    span_id: int
    parent_span_id: int | None
    start_time: datetime
    end_time: datetime

    @classmethod
    def of(cls, span: Span) -> _Frame:
        return cls(
            trace_id=int.from_bytes(span.trace_id, "big"),
            span_id=int.from_bytes(span.span_id, "big"),
            parent_span_id=(
                int.from_bytes(span.parent_span_id, "big") if span.parent_span_id else None
            ),
            start_time=ns_to_datetime(span.start_time_unix_nano),
            end_time=ns_to_datetime(span.end_time_unix_nano),
        )


class ClaudeCodeTransform:
    """Rewrite one Claude Code trace into Grove's canonical vocabulary.

    Stateless and pure: :meth:`apply` reads a released trace and returns the
    same spans with their names and attributes rewritten. It mutates the
    protobuf spans in place because they were unpacked from a request this
    gateway already owns — copying a whole trace to avoid touching messages
    nobody else can observe would double the transform's memory for nothing.

    ``content`` gates whether prompts and tool bodies are carried at all. It
    defaults to ``"all"`` because the AGENT-side switches
    (``OTEL_LOG_USER_PROMPTS``, ``OTEL_LOG_TOOL_CONTENT``) already default off:
    if a body reached this transform, an operator turned it on deliberately, and
    a second default-off gate here would render that decision inert with nothing
    said. Grove's own policy layer is expected to pass ``"none"`` when it must
    strip content that the agent was configured to send.
    """

    def __init__(self, *, content: TelemetryContent = "all") -> None:
        self._content = content

    @staticmethod
    def claims(resource: Resource, scope: InstrumentationScope) -> bool:
        """Whether these spans are Claude Code's.

        Matched on the SCOPE first: an operator is free to override
        ``service.name`` through ``OTEL_RESOURCE_ATTRIBUTES`` (Grove's own tier-1
        enrichment writes that variable), while the instrumentation scope is the
        library's own name and nothing outside Anthropic sets it.
        """
        if scope.name.startswith("com.anthropic.claude_code"):
            return True
        return any(
            attribute.key == "service.name" and attribute.value.string_value == "claude-code"
            for attribute in resource.attributes
        )

    def apply(self, released: ReleasedTrace) -> tuple[SpanEnvelope, ...]:
        """Re-spell every span Grove understands; pass the rest through."""
        envelopes = tuple(
            envelope
            for envelope in released.envelopes
            if self.claims(envelope.resource, envelope.scope)
        )
        if not envelopes:
            return released.envelopes

        index = {envelope.span.span_id: envelope.span for envelope in envelopes}
        root_id = self._resolve_root(index, released.root_span_id)
        self._reparent(index, root_id)
        depths = _SpawnTree(index).depths()
        errored = self._errored_calls(index)

        for envelope in envelopes:
            span = envelope.span
            record = self._observation(
                span,
                index=index,
                depth=depths.get(span.span_id),
                is_root=span.span_id == root_id,
                is_error=span.span_id in errored,
            )
            if record is not None:
                span.name = record.name
                OtlpAttributes.apply(span, record.attributes)
        return released.envelopes

    # ─── hierarchy ───────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_root(index: Mapping[bytes, Span], buffered_root: bytes | None) -> bytes | None:
        """The turn root, preferring the buffer's answer over a guess.

        Falls back to a parentless ``claude_code.interaction`` in this batch, and
        to nothing at all otherwise. It deliberately does NOT fall back to "any
        parentless span": that heuristic is what a backend already applies, and
        applying it to a partial trace is how a sub-agent's tool span became the
        root of a 270-observation trace.
        """
        if buffered_root is not None and buffered_root in index:
            return buffered_root
        return next(
            (
                span_id
                for span_id, span in index.items()
                if not span.parent_span_id and span.name == ClaudeCodeSpan.INTERACTION
            ),
            None,
        )

    @staticmethod
    def _reparent(index: Mapping[bytes, Span], root_id: bytes | None) -> None:
        """Attach every span whose parent is unreachable onto the turn root.

        One rule covers three symptoms that look unrelated on a dashboard: a
        child that outran its parent's export batch, an ``llm_request.context:
        standalone`` request that never had a parent, and a sub-agent subtree
        whose spawning tool span was released in an earlier window. All three
        are the same sentence — *this span names a parent the trace does not
        contain* — and all three end with the backend promoting a leaf to root.
        Handling them generically is also what keeps the ``standalone`` case
        honest: that value is UNVERIFIED here (the probe capture produced only
        ``interaction`` and ``tool``), so nothing depends on Grove having its
        spelling right.

        With no root resolved the hierarchy is left exactly as it arrived. A
        wrong parent is worse than an absent one, because only the absent one is
        visibly incomplete.
        """
        if root_id is None:
            return
        for span_id, span in index.items():
            if span_id == root_id:
                continue
            if span.parent_span_id and span.parent_span_id in index:
                continue
            span.parent_span_id = root_id

    @staticmethod
    def _errored_calls(index: Mapping[bytes, Span]) -> frozenset[bytes]:
        """Tool spans whose execution child reported failure.

        The ``success``/``error`` pair lands on ``claude_code.tool.execution``
        while the tool's identity and body land on its ``claude_code.tool``
        parent, so an error level can only be set by joining the two. Reading
        one span alone gives a tool observation that renders green next to the
        error text it produced.
        """
        failed: set[bytes] = set()
        for span in index.values():
            if span.name != ClaudeCodeSpan.TOOL_EXECUTION:
                continue
            attributes = OtlpAttributes.scalars(span.attributes)
            if attributes.get(ClaudeCodeAttr.SUCCESS) is False:
                failed.add(span.parent_span_id)
        return frozenset(failed)

    # ─── per-span mapping ────────────────────────────────────────────────────

    def _observation(
        self,
        span: Span,
        *,
        index: Mapping[bytes, Span],
        depth: int | None,
        is_root: bool,
        is_error: bool,
    ) -> SpanRecord | None:
        """The canonical record for one source span, or ``None`` to pass through.

        ``claude_code.tool.blocked_on_user`` is the deliberate pass-through: it
        is a permission wait with no Grove kind, and giving it one would put a
        tool-shaped observation on a dashboard for a call that had not started.
        """
        frame = _Frame.of(span)
        attributes = OtlpAttributes.scalars(span.attributes)
        if is_root:
            return self._turn(attributes, frame)
        if span.name == ClaudeCodeSpan.LLM_REQUEST:
            return self._generation(attributes, frame)
        if span.name == ClaudeCodeSpan.TOOL:
            return self._tool(span, attributes, frame, is_error=is_error)
        spawn = _SpawnTree.spawning_tool(span, index)
        if spawn is not None:
            return self._sub_agent(spawn, frame, depth=depth)
        return None

    def _turn(self, attributes: Mapping[str, AttributeValue], frame: _Frame) -> SpanRecord:
        """The ``claude_code.interaction`` root as an ``invoke_agent`` observation.

        Named after the session the way :class:`~grove.core.trace.TraceInstrumentor`
        names its own turn roots, so the gateway tier and the replay tier group
        under one label instead of two spellings of the same session.
        """
        session_id = str(attributes.get(ClaudeCodeAttr.SESSION_ID, ""))
        sequence = attributes.get(ClaudeCodeAttr.INTERACTION_SEQUENCE)
        name = f"session:{session_id}" if session_id else "session"
        if isinstance(sequence, int) and sequence > 1:
            name = f"{name}:turn:{sequence}"
        record = SpanRecord.agent(
            name=name,
            agent_id=session_id or None,
            depth=0,
            trace_id=frame.trace_id,
            span_id=frame.span_id,
            parent_span_id=frame.parent_span_id,
            start_time=frame.start_time,
            end_time=frame.end_time,
        )
        prompt = attributes.get(ClaudeCodeAttr.USER_PROMPT)
        if self._content == "none" or not isinstance(prompt, str) or not prompt:
            return record
        return _with_document(
            record,
            (ChatMessage.of_text("user", prompt),),
            structured_key=GenAiAttr.INPUT_MESSAGES,
            flat_key=GenAiAttr.PROMPT,
            vendor_key=LangfuseAttr.OBSERVATION_INPUT,
        )

    def _generation(self, attributes: Mapping[str, AttributeValue], frame: _Frame) -> SpanRecord:
        """One ``claude_code.llm_request`` as a ``chat`` observation.

        Usage is mapped field-by-field from the source's four counters, which is
        the whole fix for #499's "input reports 1 against a total of 78395":
        the numbers are correct on the wire and were being read from the wrong
        key downstream. An absent counter stays ``None`` rather than becoming a
        zero, per :class:`~grove.core.agents.model.TokenUsage`'s own rule.

        ``ttft_ms`` becomes a completion-start INSTANT rather than riding as a
        duration, because that is the only shape a consumer can place on a
        timeline beside the span it belongs to.
        """
        usage = TokenUsage(
            input=_as_int(attributes.get(ClaudeCodeAttr.INPUT_TOKENS)),
            output=_as_int(attributes.get(ClaudeCodeAttr.OUTPUT_TOKENS)),
            cache_read=_as_int(attributes.get(ClaudeCodeAttr.CACHE_READ_TOKENS)),
            cache_creation=_as_int(attributes.get(ClaudeCodeAttr.CACHE_CREATION_TOKENS)),
        )
        ttft = _as_int(attributes.get(ClaudeCodeAttr.TTFT_MS))
        model = attributes.get(ClaudeCodeAttr.MODEL) or attributes.get(GenAiAttr.REQUEST_MODEL)
        return SpanRecord.generation(
            model=str(model) if model else None,
            usage=usage,
            completion_start_time=(
                frame.start_time + timedelta(milliseconds=ttft) if ttft is not None else None
            ),
            trace_id=frame.trace_id,
            span_id=frame.span_id,
            parent_span_id=frame.parent_span_id,
            start_time=frame.start_time,
            end_time=frame.end_time,
        )

    def _tool(
        self,
        span: Span,
        attributes: Mapping[str, AttributeValue],
        frame: _Frame,
        *,
        is_error: bool,
    ) -> SpanRecord:
        """One ``claude_code.tool`` as an ``execute_tool`` observation, body included."""
        body = self._body(span, attributes)
        return SpanRecord.tool(
            name=str(attributes.get(ClaudeCodeAttr.TOOL_NAME) or "tool"),
            tool_call_id=str(attributes.get(ClaudeCodeAttr.TOOL_USE_ID) or "") or None,
            tool_input=dict(body.arguments) or None,
            tool_output=body.result,
            is_error=is_error,
            trace_id=frame.trace_id,
            span_id=frame.span_id,
            parent_span_id=frame.parent_span_id,
            start_time=frame.start_time,
            end_time=frame.end_time,
        )

    def _sub_agent(self, spawn: Span, frame: _Frame, *, depth: int | None) -> SpanRecord:
        """The ``tool.execution`` of an ``Agent`` call as an ``invoke_agent`` observation.

        The sub-agent's own generations and tools already parent to this span on
        the wire, so naming it as the agent observation makes the spawn edge a
        rename rather than a synthesized span — no invented id, no second node
        competing with the real one for children.
        """
        attributes = OtlpAttributes.scalars(spawn.attributes)
        return SpanRecord.agent(
            name=str(attributes.get(ClaudeCodeAttr.SUBAGENT_TYPE) or AGENT_TOOL_NAME),
            agent_id=str(attributes.get(ClaudeCodeAttr.TOOL_USE_ID) or "") or None,
            depth=depth,
            trace_id=frame.trace_id,
            span_id=frame.span_id,
            parent_span_id=frame.parent_span_id,
            start_time=frame.start_time,
            end_time=frame.end_time,
        )

    def _body(self, span: Span, attributes: Mapping[str, AttributeValue]) -> _ToolBody:
        """A tool call's arguments and result, gathered from span AND events.

        The result lives ONLY on a ``tool.output`` event, which is why a
        consumer reading attributes alone shows every tool call empty. Absent
        stays absent: a call still in flight has no ``output`` attribute and
        gets no output key, preserving the difference between "returned nothing"
        and "has not returned" that :meth:`SpanRecord.tool` already encodes.
        """
        if self._content == "none":
            return _ToolBody(arguments={}, result=None)
        arguments: dict[str, AttributeValue] = {
            key: value for key, value in attributes.items() if key not in _STRUCTURAL_ATTRS
        }
        result: str | None = None
        for event in span.events:
            if event.name != ClaudeCodeSpan.TOOL_OUTPUT_EVENT:
                continue
            for key, value in OtlpAttributes.scalars(event.attributes).items():
                if key == ClaudeCodeAttr.OUTPUT_EVENT_VALUE:
                    result = str(value)[:ATTR_TEXT_CAP]
                else:
                    arguments[key] = value
        return _ToolBody(arguments=arguments, result=result)


class _SpawnTree:
    """Who spawned whom, and how deep each sub-agent sits.

    Its own class because the two questions share one index and one memo: the
    depth walk is a parent chain, and computing it per span without memoizing
    turns a deep fleet into quadratic work on the transform pool.
    """

    def __init__(self, index: Mapping[bytes, Span]) -> None:
        self._index = index
        self._memo: dict[bytes, int] = {}

    @staticmethod
    def spawning_tool(span: Span, index: Mapping[bytes, Span]) -> Span | None:
        """The ``Agent`` tool span this span is the execution of, if any."""
        if span.name != ClaudeCodeSpan.TOOL_EXECUTION:
            return None
        parent = index.get(span.parent_span_id)
        if parent is None or parent.name != ClaudeCodeSpan.TOOL:
            return None
        attributes = OtlpAttributes.scalars(parent.attributes)
        is_agent = attributes.get(ClaudeCodeAttr.TOOL_NAME) == AGENT_TOOL_NAME
        return parent if is_agent else None

    def depths(self) -> dict[bytes, int]:
        """``span_id -> spawn depth`` for every sub-agent observation.

        **There is no ceiling and there must not be one.** How deep a fleet
        nests is a property of the work; a limit would truncate exactly the deep
        fleets that most need to be seen, and it would do so silently. The walk
        terminates on the index instead — a chain leaves the trace or reaches
        the root within its own length, and a cycle cannot be expressed by
        parent pointers a single exporter minted.
        """
        return {
            span_id: self._depth(span_id)
            for span_id, span in self._index.items()
            if self.spawning_tool(span, self._index) is not None
        }

    def _depth(self, span_id: bytes) -> int:
        cached = self._memo.get(span_id)
        if cached is not None:
            return cached
        # Provisional entry: a malformed parent chain that pointed back into
        # its own subtree would otherwise recurse until the interpreter stops
        # it, and a wrong depth is a far cheaper failure than a dead worker.
        self._memo[span_id] = 1
        depth = 1
        parent_id = self._index[span_id].parent_span_id
        while parent_id in self._index:
            parent = self._index[parent_id]
            if self.spawning_tool(parent, self._index) is not None:
                depth = self._depth(parent_id) + 1
                break
            parent_id = parent.parent_span_id
        self._memo[span_id] = depth
        return depth


def _with_document(
    record: SpanRecord,
    messages: Sequence[ChatMessage],
    *,
    structured_key: str,
    flat_key: str,
    vendor_key: str,
) -> SpanRecord:
    """Add a message document to a record that has no constructor slot for one.

    **This is a gap in :class:`~grove.core.trace.SpanRecord`, not a design.**
    ``SpanRecord.generation`` already owns exactly this dual-write — structured
    document, flattened prose, vendor key — but ``SpanRecord.agent`` takes no
    messages at all, and the turn root is precisely where a consumer reads a
    trace's input from. Promoting ``input_messages``/``output_messages`` onto
    ``SpanRecord.agent`` deletes this function; until then the keys are passed
    in from :mod:`~grove.core.telemetry.semconv` rather than spelled here, so
    the duplication is a shape and never a string.
    """
    encoded = ChatMessage.encode(messages)
    if encoded is None:
        return record
    attributes: dict[str, AttributeValue] = dict(record.attributes)
    attributes[structured_key] = encoded
    flattened = "\n".join(m.flatten() for m in messages if m.flatten())[:ATTR_TEXT_CAP]
    if flattened:
        attributes[flat_key] = flattened
        attributes[vendor_key] = flattened
    return SpanRecord(
        trace_id=record.trace_id,
        span_id=record.span_id,
        parent_span_id=record.parent_span_id,
        name=record.name,
        kind=record.kind,
        start_time=record.start_time,
        end_time=record.end_time,
        attributes=attributes,
    )


def _as_int(value: AttributeValue | None) -> int | None:
    """A reported count, or ``None`` when the source did not report one.

    ``bool`` is rejected explicitly: ``success`` is a boolean on these spans and
    a truthy read would fold it into a token count of 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


__all__ = [
    "AGENT_TOOL_NAME",
    "ClaudeCodeAttr",
    "ClaudeCodeSpan",
    "ClaudeCodeTransform",
]
