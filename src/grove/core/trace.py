"""Trace instrumentor — replay a session's spine into OTel spans for LangFuse.

The universal backfill tier of Grove's observability stack: any session
readable through :meth:`AgentMessage <grove.core.agents.model.AgentMessage>`'s
agentic-loop spine (``read_messages``) gets a full LangFuse trace with
zero provider-specific tracing code — in contrast to the live wire-level
proxy (richer, TTFT-accurate, but gateway-only). One parse (already paid by
the dashboard poll or a CLI read), one OTel export.

Design decisions worth re-deriving-avoiding:

* **Raw ``opentelemetry-sdk``, never the ``langfuse`` SDK.** LangFuse's own
  ``start_observation()`` has no ``start_time`` parameter, so it cannot
  backfill a session that already happened (research pinned in
  ``agents/CLAUDE.md`` §"Native instrumentation & control"). Building spans
  against the SDK directly is what makes historical replay possible at all.
* **Immutable turn traces with deterministic W3C ids.** LangFuse v4 accepts
  caller-supplied ids but does not promise deduplication. A trace is therefore
  one completed user turn, grouped under the native agent session with
  ``langfuse.session.id``. Once planned, none of its spans can grow. Durable
  replay safety belongs to ``telemetry_backfill``: it reconciles deterministic
  observation ids against LangFuse and checkpoints completed manifests.
* **A trivial ``Span`` subclass bypasses the SDK's "must be instantiated via
  a tracer" guard** (:func:`sink_from_processor`). The guard is an identity
  check (``cls is Span``), so a one-line subclass sidesteps it — the exact
  technique the SDK's own ``Tracer.start_span`` uses internally. This is
  required because a live ``Tracer`` always generates fresh ids and stamps
  "now" as the start time; replay needs to assign both explicitly from
  :class:`AgentMessage.timestamp <grove.core.agents.model.AgentMessage>`.
* **The emit sink is the seam the live proxy shares.** :func:`build_span_sink`
  (cfg/env-driven, best-effort) and the lower-level :func:`sink_from_processor`
  (any ``SpanProcessor``, incl. an in-memory one for tests) both return a
  :class:`SpanSink` — a live proxy with real TTFT builds its own
  :class:`SpanRecord` (setting ``completion_start_time``) and calls the same
  sink, so a replayed span and a live one land identically in LangFuse.
* **Best-effort like every other read in this codebase.** A disabled/
  misconfigured :class:`~grove.core.config.TelemetryConfig`, a missing
  ``opentelemetry`` install (the extra is optional — see ``pyproject.toml``'s
  ``telemetry`` extra), or an export failure all degrade to a silent no-op,
  never an exception into a caller's poll/render/backfill loop.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.model import AgentMessage, TokenUsage
from grove.core.config import TelemetryConfig, TelemetryContent
from grove.core.telemetry.semconv import (
    ATTR_TEXT_CAP,
    ChatMessage,
    ChatRole,
    GenAiAttr,
    GroveLiveAttr,
    LangfuseAttr,
    MessagePart,
    ObservationKind,
    ObservationShapes,
    TextPart,
    ToolCallPart,
    ToolCallResponsePart,
    TraceIdentity,
)
from grove.core.usage._pricing import _PER_MILLION, _QUANTUM, PriceBook, TokenCounts

if TYPE_CHECKING:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor


# ─── the narrow read surface this module needs ──────────────────────────────


class SpineAdapter(Protocol):
    """The read surface :meth:`TraceInstrumentor.replay` needs, and no more.

    ``read_messages`` now lives on the shared ``AgentAdapter`` Protocol, so
    every real adapter satisfies this structurally and no caller has to know
    this type exists. It stays narrow rather than widening the parameter to
    ``AgentAdapter`` because a signature is a statement about what the callee
    REQUIRES: replay reads one method, and demanding the whole adapter surface
    for it would oblige every future caller and every test double to supply
    fifteen members it never touches.

    That cost is invisible from the lint gate, which is the reason to be
    deliberate here — ``make lint`` type-checks ``src/`` only, so an
    over-wide annotation here is not enforced against the doubles in
    ``tests/`` and would silently make them lies rather than failures.
    """

    kind: str

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]: ...


# ─── deterministic W3C id derivation ────────────────────────────────────────

_MASK_128 = (1 << 128) - 1
_MASK_64 = (1 << 64) - 1


def derive_trace_id(session_id: str) -> int:
    """Deterministic 128-bit W3C trace id for a stable identity.

    Identity is necessary for remote reconciliation; it is not a claim that
    an OTLP backend deduplicates repeated records. Never returns ``0`` (OTel's
    invalid-trace sentinel).
    """
    digest = hashlib.sha256(f"grove/trace/{session_id}".encode()).digest()
    return (int.from_bytes(digest[:16], "big") & _MASK_128) or 1


def derive_turn_trace_id(session_id: str, turn_id: str, *, first: bool = False) -> int:
    """Trace id for one immutable turn, preserving the legacy first-turn id.

    Keeping the first turn on ``derive_trace_id(session_id)`` lets Grove
    reconcile deployments that briefly emitted a session-wide trace before
    immutable replay landed. Later turns use their own identity and can never
    extend that first trace.
    """
    return derive_trace_id(session_id if first else f"{session_id}/turn/{turn_id}")


def derive_span_id(*parts: str) -> int:
    """Deterministic 64-bit W3C span id from a stable key.

    Callers scope the key under the session id plus a role tag (``"agent"``/
    ``"generation"``/``"tool"``) plus the native id (message id, tool-call
    id, sub-agent thread id) — see the call sites in
    :class:`TraceInstrumentor` for the exact scoping, which is what keeps a
    generation span's id and its child tool span's id from ever colliding.
    Never ``0`` (OTel's invalid-span sentinel).
    """
    digest = hashlib.sha256("/".join(("grove/span", *parts)).encode()).digest()
    return (int.from_bytes(digest[:8], "big") & _MASK_64) or 1


# ─── the LangFuse observation shape ──────────────────────────────────────────

AttributeValue = str | int | float | bool | tuple[str, ...]
"""What one span attribute may hold.

The tuple arm exists for exactly one key — ``langfuse.trace.tags``, whose
mapping is specified as ``string[]`` rather than as a delimited string. OTel
attributes are natively sequence-capable, so this widens the type to what the
protocol already allowed rather than adding an encoding both ends would have to
agree on. It is a TUPLE, not a list, because :class:`SpanRecord` is frozen and a
mutable attribute value would make its hashability a lie.
"""

__all__ = ["ObservationKind"]  # re-exported: this module was its home before
# `telemetry.semconv` existed, and consumers import it from here.


def _usage_details(usage: TokenUsage) -> dict[str, int]:
    """Map :class:`TokenUsage` fields onto LangFuse's ``usage_details`` JSON
    blob. Field NAMES mirror ``TokenUsage`` verbatim rather than guessing
    LangFuse's own cache/reasoning vocabulary — a follow-up if LangFuse's cost
    UI wants different keys. An unreported (``None``) field is omitted, never
    fabricated as ``0``."""
    fields = {
        "input": usage.input,
        "output": usage.output,
        "cache_creation": usage.cache_creation,
        "cache_read": usage.cache_read,
        "reasoning": usage.reasoning,
    }
    return {name: value for name, value in fields.items() if value is not None}


_GEN_AI_USAGE_KEYS: Mapping[str, str] = {
    "input": GenAiAttr.USAGE_INPUT_TOKENS,
    "output": GenAiAttr.USAGE_OUTPUT_TOKENS,
    "cache_read": GenAiAttr.USAGE_CACHE_READ_INPUT_TOKENS,
    "cache_creation": GenAiAttr.USAGE_CACHE_CREATION_INPUT_TOKENS,
    "reasoning": GenAiAttr.USAGE_REASONING_OUTPUT_TOKENS,
}
"""``_usage_details``'s field names to their semconv attribute keys.

Keyed by that function's vocabulary rather than the convention's so the JSON
blob a vendor reads and the scalar attributes a portable consumer aggregates
are provably the same numbers — they are built from one dict, in one pass. A
field :func:`_usage_details` omits for want of evidence is omitted here too,
by construction rather than by a second rule that could drift from the first.
"""


CostParts = Mapping[str, Decimal]
"""Per-token-class cost for one generation, keyed by the ``_usage_details``
vocabulary so the two blobs line up key-for-key where they describe the same
thing. A class with no token evidence is an ABSENT key — never a zero, which
would read as "this class was free". ``Decimal`` because these are money and
the audit sums them across thousands of sessions; :func:`_cost_details` is the
one place that leaves that arithmetic."""

CostEstimator = Callable[[TokenUsage, str | None], CostParts | None]
"""``(usage, model) -> per-class cost``, or ``None`` when the model is unpriced
or the counts carry no evidence to charge. :func:`price_book_estimator` is
Grove's implementation; the seam stays a plain callable so a live proxy holding
provider-reported cost can supply one without a price book."""


def _cost_details(parts: CostParts) -> dict[str, float]:
    """Map per-class cost onto LangFuse's ``cost_details`` JSON blob.

    ``total`` is DERIVED from the very parts emitted beside it rather than
    computed a second way, because a total that disagrees with its breakdown is
    trusted by whichever a reader looks at first. This is also the one boundary
    where money becomes ``float``: JSON attributes have no decimal type, so the
    conversion happens once, after all the arithmetic.
    """
    total = sum(parts.values(), Decimal(0))
    return {name: float(amount) for name, amount in parts.items()} | {"total": float(total)}


@dataclass(slots=True, frozen=True)
class SpanRecord:
    """One fully-resolved span, provider- and timing-source-agnostic.

    The shared unit both this module's backfill :meth:`TraceInstrumentor.replay`
    and the live proxy build and hand to the same :class:`SpanSink` — so a
    replayed span and a live span are indistinguishable to LangFuse beyond
    timing accuracy. The three classmethods are the only constructors; they
    own the ``langfuse.observation.*`` attribute vocabulary so it is defined
    exactly once.
    """

    trace_id: int
    span_id: int
    parent_span_id: int | None
    name: str
    kind: ObservationKind
    start_time: datetime
    end_time: datetime
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)

    @staticmethod
    def _content_attributes(
        input_messages: Sequence[ChatMessage], output_messages: Sequence[ChatMessage]
    ) -> dict[str, AttributeValue]:
        """The prompt/completion pair, written once for every consumer that
        might read it.

        Shared by :meth:`agent` and :meth:`generation` because "carry this
        content on a span" is one rule, not two: the convention's structured
        document, the convention's superseded flat key, and the vendor's own
        key are three spellings of one fact, and three spellings maintained in
        two places is how a span acquires a completion that disagrees with its
        own message document. An empty document contributes NO key at all —
        absent means "nothing was said here", which a blank string cannot say.
        """
        attributes: dict[str, AttributeValue] = {}
        for document, structured_key, flat_key, vendor_key in (
            (
                input_messages,
                GenAiAttr.INPUT_MESSAGES,
                GenAiAttr.PROMPT,
                LangfuseAttr.OBSERVATION_INPUT,
            ),
            (
                output_messages,
                GenAiAttr.OUTPUT_MESSAGES,
                GenAiAttr.COMPLETION,
                LangfuseAttr.OBSERVATION_OUTPUT,
            ),
        ):
            encoded = ChatMessage.encode(document)
            if encoded is None:
                continue
            attributes[structured_key] = encoded
            flattened = "\n".join(m.flatten() for m in document if m.flatten())[:ATTR_TEXT_CAP]
            if flattened:
                attributes[flat_key] = flattened
                attributes[vendor_key] = flattened
        return attributes

    @classmethod
    def agent(
        cls,
        *,
        trace_id: int,
        span_id: int,
        parent_span_id: int | None,
        name: str,
        start_time: datetime,
        end_time: datetime,
        agent_id: str | None = None,
        depth: int | None = None,
        input_messages: Sequence[ChatMessage] = (),
        output_messages: Sequence[ChatMessage] = (),
    ) -> SpanRecord:
        """One agent invocation: a turn root, or a sub-agent nested under the
        tool call that spawned it — at any depth.

        ``name`` is the AGENT's name, not the span's. The span name is composed
        from it by :meth:`ObservationShape.span_name` per the convention's
        ``invoke_agent {gen_ai.agent.name}`` rule, which is what stops the
        rendered label and the queryable attribute from ever disagreeing.

        ``depth`` is 0 for a turn root and one more per level of spawn beneath
        it. It rides an attribute because recursion makes the tree *correct*
        but not *searchable*: "show me every third-level agent" should be a
        filter, not a parent walk a consumer has to implement itself.

        **An agent span carries content for the same reason a generation does.**
        Its input is the task it was handed and its output is what it reported
        back, and without them the one observation a reader opens FIRST — the
        node naming the whole sub-agent — is the only blank thing in an
        otherwise fully-populated tree. That reads as missing data rather than
        as a container, and it was measured: 5 of 5 agent spans in a live
        session were the only null-content observations Grove itself produced.
        """
        shape = ObservationShapes.AGENT
        attributes: dict[str, AttributeValue] = dict(shape.attributes(name))
        if agent_id:
            attributes[GenAiAttr.AGENT_ID] = agent_id
        if depth is not None:
            attributes[GroveLiveAttr.AGENT_DEPTH] = depth
        attributes.update(cls._content_attributes(input_messages, output_messages))
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=shape.span_name(name),
            kind="agent",
            start_time=start_time,
            end_time=end_time,
            attributes=attributes,
        )

    @classmethod
    def generation(
        cls,
        *,
        trace_id: int,
        span_id: int,
        parent_span_id: int | None,
        start_time: datetime,
        end_time: datetime,
        model: str | None = None,
        usage: TokenUsage | None = None,
        cost: CostParts | None = None,
        completion_start_time: datetime | None = None,
        input_messages: Sequence[ChatMessage] = (),
        output_messages: Sequence[ChatMessage] = (),
    ) -> SpanRecord:
        """One LLM turn. Every optional field is omitted (never fabricated)
        when the caller doesn't have it — a backfill replay has no TTFT (it is
        not in the transcript); a live proxy supplies it here.

        **The caller supplies structured messages and nothing else.** The flat
        ``gen_ai.prompt``/``langfuse.observation.input`` text a vendor still
        reads is DERIVED here, by :meth:`ChatMessage.flatten`, rather than
        passed alongside — two parameters carrying one fact is how a trace
        acquires a completion that disagrees with its own message document.
        A content-less span (``content: "none"``) passes neither and carries
        no input or output attribute at all.
        """
        shape = ObservationShapes.GENERATION
        attributes: dict[str, AttributeValue] = dict(shape.attributes(model))
        if model:
            attributes[LangfuseAttr.OBSERVATION_MODEL_NAME] = model
            # Request and response model are the same string here and that is
            # not a shortcut: a transcript records the model that ANSWERED, and
            # no harness writes down what was asked for when the two differ.
            # Emitting only one would make a consumer guess which.
            attributes[GenAiAttr.RESPONSE_MODEL] = model
        attributes.update(cls._content_attributes(input_messages, output_messages))
        if usage is not None:
            details = _usage_details(usage)
            if details:
                attributes[LangfuseAttr.OBSERVATION_USAGE_DETAILS] = json.dumps(details)
                attributes.update(
                    {_GEN_AI_USAGE_KEYS[name]: count for name, count in details.items()}
                )
        if cost:
            attributes[LangfuseAttr.OBSERVATION_COST_DETAILS] = json.dumps(_cost_details(cost))
        if completion_start_time is not None:
            attributes[LangfuseAttr.OBSERVATION_COMPLETION_START_TIME] = (
                completion_start_time.isoformat()
            )
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=shape.span_name(model),
            kind="generation",
            start_time=start_time,
            end_time=end_time,
            attributes=attributes,
        )

    @classmethod
    def tool(
        cls,
        *,
        trace_id: int,
        span_id: int,
        parent_span_id: int | None,
        name: str,
        start_time: datetime,
        end_time: datetime,
        tool_call_id: str | None = None,
        tool_input: object | None = None,
        tool_output: str | None = None,
        is_error: bool = False,
    ) -> SpanRecord:
        """One tool call. ``tool_input`` rides as the span's input verbatim
        (capped) and the resolving ``tool_result``'s text as its output —
        normalizing shape, never interpreting the call's meaning. A call still
        in flight has no result, so ``tool_output`` is omitted rather than
        emitted empty: "no output yet" and "returned nothing" are different
        facts and only the omission can say the first.

        ``tool_call_id`` is the harness's own id for the invocation. It is the
        join a sub-agent's root span parents itself on, so a consumer can
        reconstruct the spawn edge from attributes alone even if it discards
        Grove's span ids.
        """
        shape = ObservationShapes.TOOL
        attributes: dict[str, AttributeValue] = dict(shape.attributes(name))
        if tool_call_id:
            attributes[GenAiAttr.TOOL_CALL_ID] = tool_call_id
        if tool_input is not None:
            try:
                encoded = json.dumps(tool_input, default=str)
            except (TypeError, ValueError):
                encoded = str(tool_input)
            attributes[LangfuseAttr.OBSERVATION_INPUT] = encoded[:ATTR_TEXT_CAP]
            attributes[GenAiAttr.TOOL_CALL_ARGUMENTS] = encoded[:ATTR_TEXT_CAP]
        if tool_output:
            attributes[LangfuseAttr.OBSERVATION_OUTPUT] = tool_output[:ATTR_TEXT_CAP]
            attributes[GenAiAttr.TOOL_CALL_RESULT] = tool_output[:ATTR_TEXT_CAP]
        if is_error:
            attributes[LangfuseAttr.OBSERVATION_LEVEL] = "ERROR"
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=shape.span_name(name),
            kind="tool",
            start_time=start_time,
            end_time=end_time,
            attributes=attributes,
        )


@dataclass(slots=True, frozen=True)
class TraceManifest:
    """One complete immutable trace ready for durable reconciliation/export."""

    session_id: str
    turn_id: str
    trace_id: int
    spans: tuple[SpanRecord, ...]

    @property
    def trace_id_hex(self) -> str:
        """LangFuse's lowercase, zero-padded W3C trace-id representation."""
        return f"{self.trace_id:032x}"

    @property
    def observation_ids(self) -> frozenset[str]:
        """Every immutable observation id in this manifest."""
        return frozenset(f"{span.span_id:016x}" for span in self.spans)

    @property
    def fingerprint(self) -> str:
        """Content-sensitive identity used to reject mutation after export."""
        payload = [
            {
                "trace_id": f"{span.trace_id:032x}",
                "span_id": f"{span.span_id:016x}",
                "parent_span_id": (
                    f"{span.parent_span_id:016x}" if span.parent_span_id is not None else None
                ),
                "name": span.name,
                "kind": span.kind,
                "start_time": span.start_time.isoformat(),
                "end_time": span.end_time.isoformat(),
                "attributes": dict(sorted(span.attributes.items())),
            }
            for span in self.spans
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


class SpanSink(Protocol):
    """The seam the live proxy feeds spans through — same exporter/
    processor pipeline :func:`build_span_sink` wires up, so a replayed span
    (this module) and a live span land identically in LangFuse."""

    def __call__(self, record: SpanRecord) -> None:
        """Emit one already-built, already-timed span."""

    def flush(self) -> None:
        """Force-drain the batch processor. Call once per logical unit of
        work (e.g. once per :meth:`TraceInstrumentor.replay`), not once per
        span — calling it per-span defeats batching."""


def _to_ns(moment: datetime) -> int:
    """OTel wants epoch nanoseconds; ``AgentMessage.timestamp`` is aware
    per the adapters' own contract, but guard the naive case defensively —
    the same discipline as every other best-effort read here."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000_000)


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse ``TelemetryConfig.derive_env``'s ``key=value,key=value`` header
    string — the same shape the generic ``OTEL_EXPORTER_OTLP_HEADERS`` env
    var uses, just read directly here since we construct the exporter
    explicitly rather than letting it self-configure from the environment."""
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        if value:
            headers[key.strip()] = value.strip()
    return headers


def _trace_transport(derived: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Resolve the exact trace endpoint and headers, honoring signal overrides."""
    generic = derived.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    endpoint = derived.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if endpoint is None and generic is not None:
        endpoint = f"{generic.rstrip('/')}/v1/traces"
    headers = derived.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or derived.get(
        "OTEL_EXPORTER_OTLP_HEADERS"
    )
    return endpoint, headers


def sink_from_processor(processor: SpanProcessor, resource: Resource) -> SpanSink:
    """Wrap an OTel ``SpanProcessor`` into a :class:`SpanSink`.

    The low-level composable seam: :func:`build_span_sink` calls this with a
    real ``BatchSpanProcessor(OTLPSpanExporter(...))``; a test (or the live
    proxy, if it wants a differently-configured processor) can call it
    directly with any ``SpanProcessor`` — e.g. an in-memory one — and
    exercise the identical span-construction path production traffic takes.
    Requires ``opentelemetry-sdk`` to already be importable (the ``telemetry``
    extra); callers resolve that themselves (see :func:`build_span_sink`'s
    lazy import + ``ImportError`` handling).
    """
    from opentelemetry.sdk.trace import Span as _SdkSpan  # noqa: PLC0415
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON  # noqa: PLC0415
    from opentelemetry.trace import SpanContext, TraceFlags  # noqa: PLC0415
    from opentelemetry.trace import SpanKind as _OtelSpanKind  # noqa: PLC0415

    class _HistoricalSpan(_SdkSpan):
        """Bypasses the SDK's "Span must be instantiated via a tracer" guard.

        That guard is an identity check (``if cls is Span: raise``), so any
        trivial subclass sidesteps it — literally the technique the SDK's own
        ``Tracer.start_span`` uses internally (it constructs a private
        ``_Span`` subclass, not ``Span`` itself). Needed here because we set
        the trace/span ids and start/end times explicitly rather than letting
        a live ``Tracer`` generate/measure them.
        """

    sampled = TraceFlags(TraceFlags.SAMPLED)

    def _emit(record: SpanRecord) -> None:
        context = SpanContext(record.trace_id, record.span_id, is_remote=False, trace_flags=sampled)
        parent = (
            SpanContext(
                record.trace_id, record.parent_span_id, is_remote=False, trace_flags=sampled
            )
            if record.parent_span_id is not None
            else None
        )
        span = _HistoricalSpan(
            name=record.name,
            context=context,
            parent=parent,
            sampler=ALWAYS_ON,
            resource=resource,
            span_processor=processor,
            kind=_OtelSpanKind.INTERNAL,
        )
        span.start(start_time=_to_ns(record.start_time))
        for key, value in record.attributes.items():
            span.set_attribute(key, value)
        span.end(end_time=_to_ns(record.end_time))

    def _flush() -> None:
        if not processor.force_flush():
            raise RuntimeError("OpenTelemetry span processor did not flush")

    return _SinkAdapter(_emit, _flush)


@dataclass(slots=True, frozen=True)
class _SinkAdapter:
    """Trivial ``SpanSink`` implementation over two closures — avoids a named
    class per sink construction site while keeping ``SpanSink`` a real
    Protocol (structural, not nominal)."""

    _emit: Callable[[SpanRecord], None]
    _flush: Callable[[], None]

    def __call__(self, record: SpanRecord) -> None:
        self._emit(record)

    def flush(self) -> None:
        self._flush()


def build_span_sink(
    cfg: TelemetryConfig, *, env: Mapping[str, str] | None = None
) -> SpanSink | None:
    """Build the cfg/env-driven OTLP :class:`SpanSink`, or ``None`` when
    tracing should stay a no-op.

    Best-effort by construction: disabled config, unresolved credentials, or
    a missing ``opentelemetry`` install (the optional ``telemetry`` extra —
    see ``pyproject.toml``) all degrade to ``None`` rather than raising, so a
    caller can build a :class:`TraceInstrumentor` unconditionally and let
    ``enabled`` gate everything downstream. ``env`` defaults to
    ``os.environ`` — this module IS the edge that reads it (mirrors every
    other side-effect module), never ``TelemetryConfig`` itself.

    Degrading quietly is not the same as degrading silently: an operator who
    asked for telemetry and got none is WARNED here, while a disabled config
    says nothing (opting out is not a fault). **Build the sink once per owner,
    never per tick** — the warning is a standing condition, so a caller that
    re-derives it on a poll loop turns one diagnostic into a log flood.
    """
    if not cfg.enabled:
        return None
    derived = cfg.derive_env(env if env is not None else os.environ)
    endpoint, headers_raw = _trace_transport(derived)
    if not endpoint or not headers_raw:
        # WARN, not debug: the operator asked for telemetry and this is the only
        # place that says Grove's OWN exporter is the thing that went dark.
        # `derive_env` has already warned naming the variables that failed, so
        # this deliberately adds the consequence rather than repeating the
        # diagnosis — a second copy of the same sentence trains people to skip
        # both.
        logger.warning(
            "telemetry is enabled but LangFuse credentials did not resolve, so Grove's own "
            "trace export stays a no-op — every session replay is dropped"
        )
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: PLC0415
            ExportTraceServiceResponse,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import SpanProcessor  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import (  # noqa: PLC0415
            SpanExporter,
            SpanExportResult,
        )
    except ImportError as exc:
        logger.debug(
            "opentelemetry not installed (grove[telemetry] extra); tracing stays no-op: {}", exc
        )
        return None

    class _VerifiedOTLPSpanExporter(OTLPSpanExporter):
        """Treat an OTLP partial-success body as a failed historical write."""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._last_response: Any = None

        def _export(self, serialized_data: bytes, timeout_sec: float | None = None) -> Any:
            response = super()._export(serialized_data, timeout_sec)
            self._last_response = response
            return response

        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            result = super().export(spans)
            response = self._last_response
            if result is not SpanExportResult.SUCCESS or response is None or not response.content:
                return result
            try:
                partial = ExportTraceServiceResponse.FromString(response.content).partial_success
            except Exception:
                logger.warning("telemetry export returned an invalid OTLP response body")
                return SpanExportResult.FAILURE
            if partial.rejected_spans:
                logger.warning(
                    "telemetry backend rejected {} historical spans: {}",
                    partial.rejected_spans,
                    partial.error_message or "no reason supplied",
                )
                return SpanExportResult.FAILURE
            return result

    exporter = _VerifiedOTLPSpanExporter(endpoint=endpoint, headers=_parse_headers(headers_raw))

    class _VerifiedSpanProcessor(SpanProcessor):
        """Synchronously expose the export result at the durability boundary.

        ``BatchSpanProcessor.force_flush`` only says its queue drained; it does
        not propagate whether the exporter accepted the batch. Historical
        checkpointing needs the stronger answer before it may even attempt a
        remote read-back.
        """

        def __init__(self, span_exporter: SpanExporter) -> None:
            self._exporter = span_exporter
            self._pending: list[ReadableSpan] = []

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            del span, parent_context

        def on_end(self, span: ReadableSpan) -> None:
            self._pending.append(span)

        def shutdown(self) -> None:
            self._exporter.shutdown()

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            del timeout_millis
            pending, self._pending = self._pending, []
            try:
                return all(
                    self._exporter.export(pending[offset : offset + 512])
                    is SpanExportResult.SUCCESS
                    for offset in range(0, len(pending), 512)
                )
            except Exception:
                return False

    processor = _VerifiedSpanProcessor(exporter)
    resource = Resource.create({"service.name": "grove"})
    return sink_from_processor(processor, resource)


def _bounds(messages: Sequence[AgentMessage]) -> tuple[datetime, datetime] | None:
    """The (earliest, latest) timestamp across a message set, or ``None``
    when every message is timestamp-less (never spans an un-timeable set)."""
    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    if not stamps:
        return None
    return min(stamps), max(stamps)


@dataclass(slots=True, frozen=True)
class _ToolResult:
    """What a resolving ``tool_result`` contributes to its call's span: when it
    landed (the span's end) and what it returned (the span's output). Both
    stay ``Optional`` because a transcript can carry either without the other."""

    timestamp: datetime | None
    text: str | None
    is_error: bool


@dataclass(slots=True, frozen=True)
class _Fleet:
    """Every sub-agent thread of one session, split by whether it can be placed
    causally.

    The split exists because the two halves are attached by different evidence
    and a reader must be able to tell them apart. ``spawned`` is keyed by the
    tool call that created each thread — a fact the harness recorded. ``adrift``
    is the rest, and their content is otherwise LOST: a thread nothing attaches
    never becomes a span, and its messages are only in the transcript as
    sidechains nobody replays.

    Measured across 2592 real sidecars on 2026-08-11: 1025 are genuine
    sub-agents (``spawnDepth >= 1``) and **278 of those carry no ``toolUseId``**
    — and only 8 of the 278 carry ``parentAgentId`` either, so there is no
    second key waiting to be read; the edge was simply never written down. On
    one real session that silence cost 1434 of 2420 messages, 59% of the work,
    absent from the trace entirely.
    """

    spawned: dict[str, _SubAgentThread]
    adrift: tuple[_SubAgentThread, ...]

    @classmethod
    def of(
        cls,
        adapter: SpineAdapter,
        cwd: Path,
        session_id: str,
        messages: Sequence[AgentMessage],
    ) -> _Fleet:
        """Read the session's sub-agent threads and split them.

        Claude-only by construction: sub-agent threads are a Claude transcript
        shape, and every other adapter answers with an empty fleet rather than
        with a branch at the call site.
        """
        if not isinstance(adapter, ClaudeCodeAdapter):
            return cls(spawned={}, adrift=())
        spawned: dict[str, _SubAgentThread] = {}
        adrift: list[_SubAgentThread] = []
        for sub_session, sub_activity in adapter.fleet_activity(cwd, session_id):
            thread_id = sub_session.session_id
            thread_messages = tuple(
                message
                for message in messages
                if message.is_sidechain and message.thread_id == thread_id
            )
            bounds = _bounds(thread_messages)
            if bounds is None:
                continue  # nothing timeable, so nothing placeable
            spawn = _spawn_meta(sub_session.transcript_path)
            start, end = bounds
            thread = _SubAgentThread(
                spawning_tool_id=spawn.tool_use_id or "",
                thread_id=thread_id,
                title=sub_activity.title or f"subagent:{thread_id[:8]}",
                messages=thread_messages,
                start=start,
                end=end,
                spawn_depth=spawn.spawn_depth,
            )
            if thread.spawning_tool_id:
                spawned[thread.spawning_tool_id] = thread
            else:
                adrift.append(thread)
        return cls(spawned=spawned, adrift=tuple(adrift))

    def within(self, start: datetime, end: datetime) -> list[tuple[_SubAgentThread, int]]:
        """The adrift threads that began inside ``[start, end]``, as attachments.

        A thread with no recoverable spawn call still ran, and it ran inside
        exactly one turn — so its own start instant places it, deterministically
        and without a key the sidecar failed to record. Depth 1 because that is
        what the measurement says: every one of the 278 unrecoverable threads
        sits at ``spawnDepth`` 1, and none deeper has ever been missing a
        ``toolUseId``.
        """
        return [(thread, 1) for thread in self.adrift if start <= thread.start <= end]


@dataclass(slots=True, frozen=True)
class _SpanOwner:
    """The agent a generation or tool span belongs to.

    Every descendant span repeats these three facts, so *who ran this* is
    answerable from the observation itself rather than by walking up the tree.
    That walk is exactly what a reader cannot do in a list view — a fleet's
    tool calls all render as siblings there, and without an owner on each one
    the only way to tell a sub-agent's `Bash` from the root's is to open both.

    One object rather than three parallel parameters because the three are one
    fact: an id, its display name and its depth always travel together, and a
    call site that passed a sub-agent's id beside the root's depth would be
    silently wrong in a way no signature could catch.
    """

    agent_id: str
    name: str
    depth: int

    def attributes(self) -> dict[str, AttributeValue]:
        """The provenance keys, omitting what this owner cannot say."""
        resolved: dict[str, AttributeValue] = {
            GroveLiveAttr.AGENT_PARENT_ID: self.agent_id,
            GroveLiveAttr.AGENT_DEPTH: self.depth,
        }
        if self.name:
            resolved[GenAiAttr.AGENT_NAME] = self.name
        return resolved


@dataclass(slots=True, frozen=True)
class _SubAgentThread:
    """One sub-agent transcript thread and the tool call it hangs from.

    Replaces the 4-tuple this was: the traversal below indexes these by
    spawning call and reads their bounds, and ``child[2]``/``child[3]`` is not
    a readable way to ask when a sub-agent started.
    """

    spawning_tool_id: str
    thread_id: str
    title: str
    messages: tuple[AgentMessage, ...]
    start: datetime
    end: datetime
    spawn_depth: int | None = None
    """The harness's own depth for this thread, when it reports one. Preferred
    over the traversal's count — see :func:`_spawn_meta`."""


def _tool_use_ids(messages: Sequence[AgentMessage]) -> set[str]:
    """Every tool call issued in a thread — the spawn points a sub-agent could
    hang from."""
    return {
        block.tool_use_id
        for message in messages
        for block in message.content
        if block.type == "tool_use" and block.tool_use_id
    }


def _attachments(
    root: Sequence[AgentMessage], fleet: Mapping[str, _SubAgentThread]
) -> list[tuple[_SubAgentThread, int]]:
    """Every sub-agent thread reachable from a turn, paired with its depth.

    **Depth is unbounded on purpose.** A sub-agent may spawn a sub-agent, which
    may spawn another; the tree's shape is a property of the work, not a
    parameter, so this walks until nothing new is reachable rather than to a
    configured limit. A limit would silently truncate exactly the deep fleets
    that most need to be seen.

    The walk is over SPAWNING CALL ids, and each is consumed once, so a
    malformed sidecar that pointed two threads at one call — or at a call
    inside its own subtree — terminates instead of looping. Ids are visited in
    sorted order because every span id downstream is derived deterministically,
    and a set's iteration order would make two replays of one transcript
    disagree.
    """
    attached: list[tuple[_SubAgentThread, int]] = []
    seen: set[str] = set()
    frontier: list[tuple[Sequence[AgentMessage], int]] = [(root, 1)]
    while frontier:
        messages, depth = frontier.pop()
        for tool_id in sorted(_tool_use_ids(messages)):
            thread = fleet.get(tool_id)
            if thread is None or tool_id in seen:
                continue
            seen.add(tool_id)
            attached.append((thread, depth))
            frontier.append((thread.messages, depth + 1))
    return attached


def _tool_results(messages: Sequence[AgentMessage]) -> dict[str, _ToolResult]:
    """``tool_use_id -> its resolving tool_result`` — a tool_result is a
    forward reference (agents/CLAUDE.md), so this is a pre-scan mirroring the
    one every turn/digest projection already does."""
    return {
        block.tool_use_id: _ToolResult(
            timestamp=msg.timestamp, text=block.text, is_error=block.is_error
        )
        for msg in messages
        for block in msg.content
        if block.type == "tool_result" and block.tool_use_id
    }


def _agent_content(
    messages: Sequence[AgentMessage], content: TelemetryContent
) -> tuple[tuple[ChatMessage, ...], tuple[ChatMessage, ...]]:
    """An agent span's own content: the task it was handed, and what it reported.

    Deliberately the FIRST human message and the LAST assistant message rather
    than the whole thread — the descendants already carry every intermediate
    step, so repeating them here would duplicate the entire subtree onto its own
    root and blow the attribute cap on exactly the spans a reader opens first.
    A thread with no assistant reply yet reports its task and no result, which
    is the true state of a sub-agent still working.
    """
    if content not in {"messages", "all"}:
        return (), ()
    task = next((m for m in messages if m.role != "assistant" and m.text()), None)
    result = next((m for m in reversed(messages) if m.role == "assistant" and m.text()), None)
    return (
        (ChatMessage.of_text(_chat_role(task), task.text()),) if task else (),
        (ChatMessage.of_text("assistant", result.text()),) if result else (),
    )


def _chat_role(message: AgentMessage) -> ChatRole:
    """Narrow an adapter's role string onto the convention's four roles.

    Anything unrecognized becomes ``user`` rather than raising: a role is a
    label on content Grove has already read, and dropping a real prompt because
    a harness spelled its role a new way would lose the turn to protect an
    enum.
    """
    role = message.role
    return role if role in ("system", "user", "assistant", "tool") else "user"


def _incoming_parts(message: AgentMessage) -> tuple[MessagePart, ...]:
    """What a non-assistant message contributes to the NEXT generation's input.

    ``AgentMessage.text()`` alone is not enough: the ``tool`` role is a
    result-carrier whose payload rides ``tool_result`` blocks, so a generation
    in the middle of a tool loop would otherwise show no input at all. Each
    result keeps its ``tool_use_id``, which is what lets a consumer pair a
    response back to the call that produced it without matching on position.
    """
    parts: list[MessagePart] = []
    for block in message.content:
        if not block.text:
            continue
        if block.type == "text":
            parts.append(TextPart(content=block.text))
        elif block.type == "tool_result":
            parts.append(ToolCallResponsePart(id=block.tool_use_id or "", response=block.text))
    return tuple(parts)


def _assistant_parts(message: AgentMessage, *, arguments: bool) -> tuple[MessagePart, ...]:
    """What an assistant message contributes as the generation's output.

    Its tool calls are part of the completion, not a separate fact: a turn
    where the model said little and invoked four tools reads as empty if only
    prose survives. ``arguments`` is false under ``content: "messages"``, where
    the call is reported but its payload is not — the same distinction
    :meth:`TraceInstrumentor._thread_spans` applies to the tool span itself.
    """
    parts: list[MessagePart] = []
    text = message.text()
    if text:
        parts.append(TextPart(content=text))
    parts.extend(
        ToolCallPart(
            id=block.tool_use_id,
            name=block.tool_name or "tool",
            arguments=block.tool_input if arguments else None,
        )
        for block in message.content
        if block.type == "tool_use" and block.tool_use_id
    )
    return tuple(parts)


@dataclass(slots=True, frozen=True)
class _SpawnMeta:
    """What a sub-agent transcript's sidecar says about where it came from.

    Measured across 2590 real sidecars on 2026-08-10: ``spawnDepth`` is present
    on **every** one, ``toolUseId`` on 741, ``parentAgentId`` on 309, and 270
    threads at depth ≥ 1 carry neither correlation field and so can never be
    attached to a spawning call at all. That last number is the honest ceiling
    on this tier's coverage, and it is a property of the harness's own records,
    not of anything Grove can improve by reading harder.
    """

    tool_use_id: str | None
    spawn_depth: int | None


def _spawn_meta(transcript_path: Path | None) -> _SpawnMeta:
    """Read a sub-agent thread's sidecar once, for everything it can tell us.

    ``spawnDepth`` is the harness's OWN count of how deep this thread sits, and
    it is preferred over the depth Grove derives from walking spawn edges —
    normalizing the shape a provider reports is this codebase's rule, and
    recomputing a number the provider already published is second-guessing it.
    The walk still has to happen (it is what supplies the parent span), but
    where the two disagree the harness wins.
    """
    if transcript_path is None:
        return _SpawnMeta(None, None)
    meta_path = transcript_path.with_name(f"{transcript_path.stem}.meta.json")
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _SpawnMeta(None, None)
    if not isinstance(raw, dict):
        return _SpawnMeta(None, None)
    tool_use_id = raw.get("toolUseId")
    depth = raw.get("spawnDepth")
    return _SpawnMeta(
        tool_use_id=tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None,
        spawn_depth=depth if isinstance(depth, int) and not isinstance(depth, bool) else None,
    )


def _spawning_tool_use_id(transcript_path: Path | None) -> str | None:
    """The main-thread ``tool_use`` id that spawned a sub-agent thread, read
    from its sibling ``<transcript>.meta.json`` sidecar's ``toolUseId`` field
    (the same sidecar ``ClaudeCodeAdapter.fleet_activity`` reads for identity).

    Deliberately NOT read off ``AgentMessage.parent_tool_use_id`` — verified
    on-host (see ``claude_code.py``'s ``read_subagent_meta``) that field
    mirrors the sub-agent's own same-thread ``parentUuid``, never the main
    transcript's spawning call, so it cannot correlate a thread back to its
    spawn. Best-effort like every other sidecar read here: missing/malformed
    → ``None``, never raised.
    """
    return _spawn_meta(transcript_path).tool_use_id


def price_book_estimator(prices: PriceBook) -> CostEstimator:
    """Adapt a :class:`~grove.core.usage._pricing.PriceBook` into the
    :data:`CostEstimator` shape :meth:`TraceInstrumentor.__init__` takes.

    Reuses the usage audit's own field mapping rather than re-deriving one:
    ``TokenUsage.input`` is the book's ``fresh_input`` (Claude reports it net
    of anything cached), and ``reasoning`` is deliberately NOT priced as a
    fifth class — it is informational and already billed inside ``output`` by
    every provider that reports it (see the pricing module's own docstring), so
    charging it here would inflate every generation that thought.

    **There is no provider-reported cost to prefer over this estimate, and no
    built-in rate to fall back on.** Measured across 319 on-host Claude Code
    transcripts: an assistant message's ``usage`` object carries no cost-bearing
    key of any spelling, so a reader for one would be a seam with no producer.
    And ``UsagePricingConfig.models`` ships EMPTY — every rate is the operator's,
    so with none configured this prices nothing and the span carries no cost at
    all. Grove supplies the arithmetic, never the catalog.

    ``PriceBook.amount`` is called for its VERDICT, not its number: it owns the
    policy for when a figure is knowable at all (unpriced model, all-``None``
    counts, a non-zero rate whose class went unmeasured), and re-stating that
    policy here is how the audit's money and the trace's money would come to
    disagree about the same session. The figures themselves are then computed
    once, per class, from the same rates — so the total this module emits is
    the sum of the parts it emits beside it.
    """

    def _estimate(usage: TokenUsage, model: str | None) -> CostParts | None:
        counts = TokenCounts(
            fresh_input=usage.input,
            cache_read=usage.cache_read,
            cache_creation=usage.cache_creation,
            output=usage.output,
        )
        price = prices.price_for(model)
        if price is None or prices.amount(model, counts) is None:
            return None
        # Keyed by `_usage_details`'s vocabulary, not the price model's:
        # `cache_creation` is what the token count is called everywhere a
        # reader will see it, and the two blobs have to line up.
        priced = {
            "input": (price.input, counts.fresh_input),
            "output": (price.output, counts.output),
            "cache_read": (price.cache_read, counts.cache_read),
            "cache_creation": (price.cache_write, counts.cache_creation),
        }
        return {
            name: (Decimal(str(rate)) * Decimal(count) / _PER_MILLION).quantize(_QUANTUM)
            for name, (rate, count) in priced.items()
            if count is not None
        }

    return _estimate


@dataclass(slots=True, frozen=True)
class _Turn:
    """One main-thread user turn and whether its transcript has closed it."""

    turn_id: str
    messages: tuple[AgentMessage, ...]
    complete: bool


def _turns(messages: Sequence[AgentMessage]) -> tuple[_Turn, ...]:
    """Partition the main-thread spine at human prompts.

    A turn is immutable once another human prompt starts or once its tail is a
    final assistant message with no tool call. A trailing tool result is still
    waiting for the model and is deliberately withheld from export.
    """
    if not messages:
        return ()
    starts = [
        index
        for index, message in enumerate(messages)
        if message.role == "user" and (index == 0 or messages[index - 1].role != "user")
    ]
    boundaries = [0, *(index for index in starts if index > 0)]
    boundaries = list(dict.fromkeys(boundaries))
    turns: list[_Turn] = []
    for ordinal, start in enumerate(boundaries):
        stop = boundaries[ordinal + 1] if ordinal + 1 < len(boundaries) else len(messages)
        chunk = tuple(messages[start:stop])
        if not any(message.role == "assistant" for message in chunk):
            continue
        anchor = next((message for message in chunk if message.role == "user"), chunk[0])
        stamp = anchor.timestamp.isoformat() if anchor.timestamp is not None else "untimed"
        identity = hashlib.sha256(
            "\0".join((anchor.message_id or "", stamp, anchor.text())).encode()
        ).hexdigest()[:12]
        tail = next((message for message in reversed(chunk) if message.role == "assistant"), None)
        tail_is_final = bool(tail is not None and not tail.tool_names() and chunk[-1] is tail)
        turns.append(
            _Turn(
                turn_id=f"{ordinal + 1}:{stamp}:{identity}",
                messages=chunk,
                complete=ordinal + 1 < len(boundaries) or tail_is_final,
            )
        )
    return tuple(turns)


def _message_identity(message: AgentMessage) -> str:
    """Stable fallback for providers whose normalized message has no native id.

    It hashes normalized evidence, never an ordinal, so inserting an older
    record cannot rename every later observation. Content does not leave this
    process; only the digest participates in a W3C id seed.
    """
    payload = {
        "role": message.role,
        "timestamp": message.timestamp.isoformat() if message.timestamp else None,
        "model": message.model,
        "thread_id": message.thread_id,
        "blocks": [
            {
                "type": block.type,
                "text": block.text,
                "tool_name": block.tool_name,
                "tool_use_id": block.tool_use_id,
                "tool_input": block.tool_input,
                "is_error": block.is_error,
            }
            for block in message.content
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:24]


class TraceInstrumentor:
    """Plan and emit immutable turn traces over the normalized message spine.

    Each completed human turn becomes one trace grouped by native session id:
    root ``agent`` → ``generation`` → ``tool`` → Claude sub-agent ``agent``
    trees. ``plan`` is the side-effect-free replay seam used by durable
    backfill; ``replay`` is the best-effort live convenience wrapper.
    """

    def __init__(
        self,
        cfg: TelemetryConfig,
        *,
        sink: SpanSink | None = None,
        env: Mapping[str, str] | None = None,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        """``sink`` is the test/live-proxy injection seam (skips cfg/env entirely
        when supplied); otherwise built from ``cfg`` via :func:`build_span_sink`.

        ``cost_estimator`` is the ONLY source of ``cost_details`` — this module
        still carries no pricing config of its own. Grove's model-pricing
        catalog now lives in :class:`~grove.core.usage._pricing.PriceBook`;
        :func:`price_book_estimator` adapts one into this shape and the
        caller wires it in (:class:`~grove.core.trace_forwarder.TraceForwarder`
        is the composition root that has the resolved pricing config to
        build one from). With nothing injected the cost is genuinely unknown
        and the attribute is omitted rather than guessed. The seam takes
        ``(usage, model)`` and answers PER CLASS because per-token pricing is
        the only shape that can price cache tokens at all — LangFuse's own
        price table cannot see them — and because a single total cannot say
        which class the money went to, which is the question anyone reading a
        trace for spend is actually asking.
        """
        self._cfg = cfg
        self._sink = sink if sink is not None else build_span_sink(cfg, env=env)
        self._cost_estimator = cost_estimator
        # Live cost control only. Durable exactly-once behavior belongs to the
        # backfill coordinator, which reconciles against the remote backend.
        self._emitted: set[int] = set()

    @property
    def enabled(self) -> bool:
        """Whether :meth:`replay` will do anything — config-enabled AND a
        sink actually resolved (credentials present, SDK importable)."""
        return self._cfg.enabled and self._sink is not None

    def replay(
        self,
        cwd: Path,
        session_id: str,
        adapter: SpineAdapter,
        *,
        session_group_id: str | None = None,
        identity: TraceIdentity | None = None,
    ) -> None:
        """Emit newly completed turns once per live instrumentor instance.

        Never raises. This in-memory gate prevents poll-loop export storms; a
        historical/cold replay must go through the durable coordinator because
        OTLP ingestion itself is not an idempotency boundary.
        """
        if not self.enabled:
            return
        try:
            sink = self._sink
            if sink is None:  # pragma: no cover - guarded by enabled
                return
            for manifest in self.plan(
                cwd, session_id, adapter, session_group_id=session_group_id, identity=identity
            ):
                if manifest.trace_id in self._emitted:
                    continue
                for record in manifest.spans:
                    sink(record)
                sink.flush()
                self._emitted.add(manifest.trace_id)
        except Exception as exc:  # best-effort — mirrors peek()'s discipline
            logger.debug("trace replay failed for session {}: {}", session_id, exc)

    def plan(
        self,
        cwd: Path,
        session_id: str,
        adapter: SpineAdapter,
        *,
        session_group_id: str | None = None,
        content: TelemetryContent = "all",
        source_id: str | None = None,
        identity: TraceIdentity | None = None,
    ) -> tuple[TraceManifest, ...]:
        """Build complete immutable manifests; reads only through the adapter.

        *identity* is the workspace the session ran in. It is optional because
        this tier is deliberately reachable without one — the historical
        backfill replays transcripts whose workspace may no longer exist, and
        inventing an identity for those would be worse than omitting it. A
        caller that HAS the workspace should always pass it: without it the
        richest tree Grove produces is also the one nobody can filter.
        """
        messages = adapter.read_messages(cwd, session_id)
        if not messages:
            return ()
        main_thread = tuple(m for m in messages if not m.is_sidechain)
        fleet = _Fleet.of(adapter, cwd, session_id, messages)

        manifests: list[TraceManifest] = []
        span_seed = f"{source_id}/{session_id}" if source_id else session_id
        for ordinal, turn in enumerate(_turns(main_thread)):
            if not turn.complete:
                continue
            bounds = _bounds(turn.messages)
            if bounds is None:
                continue
            trace_id = derive_turn_trace_id(span_seed, turn.turn_id, first=ordinal == 0)
            span_turn = () if ordinal == 0 else (turn.turn_id,)
            root_span_id = derive_span_id(span_seed, "agent", "root", *span_turn)
            attachments = _attachments(turn.messages, fleet.spawned)
            start, end = bounds
            # Placed against the ORIGINAL turn bounds, before the expansion
            # below: an already-attached grandchild widening the root must not
            # pull an unrelated thread into this turn.
            attachments.extend(fleet.within(*bounds))
            if attachments:
                # The root must span its whole subtree, not just its direct
                # children: a grandchild that outlived its parent would
                # otherwise render outside the root's own bar.
                start = min(start, *(thread.start for thread, _ in attachments))
                end = max(end, *(thread.end for thread, _ in attachments))
            root_task, root_result = _agent_content(turn.messages, content)
            root_name = (
                f"session:{session_id}"
                if ordinal == 0
                else f"session:{session_id}:turn:{ordinal + 1}"
            )
            records = [
                SpanRecord.agent(
                    trace_id=trace_id,
                    span_id=root_span_id,
                    parent_span_id=None,
                    name=root_name,
                    start_time=start,
                    end_time=end,
                    agent_id=session_id,
                    depth=0,
                    input_messages=root_task,
                    output_messages=root_result,
                )
            ]
            generation_ids: set[int] = set()
            records.extend(
                self._thread_spans(
                    span_seed,
                    turn.messages,
                    trace_id=trace_id,
                    agent_span_id=root_span_id,
                    owner=_SpanOwner(agent_id=session_id, name=root_name, depth=0),
                    turn_id=turn.turn_id if ordinal else None,
                    content=content,
                    generation_ids=generation_ids,
                )
            )
            for thread, depth in attachments:
                sub_agent_span_id = derive_span_id(span_seed, "agent", thread.thread_id, *span_turn)
                thread_task, thread_result = _agent_content(thread.messages, content)
                spawned = SpanRecord.agent(
                    trace_id=trace_id,
                    span_id=sub_agent_span_id,
                    # The spawn edge. Its parent is the TOOL span of the call
                    # that created this thread — emitted by whichever
                    # `_thread_spans` pass covered the thread that made the
                    # call, at any depth. That is why the traversal above
                    # must attach a thread before it can be walked into: a
                    # parent named here and never emitted is precisely the
                    # orphan defect this shape exists to end.
                    #
                    # With no recoverable spawning call the parent is the TURN
                    # ROOT. Deriving a tool span id from an empty key would name
                    # a parent nothing emits — reintroducing that same orphan
                    # defect to preserve a link that was never recorded.
                    parent_span_id=(
                        derive_span_id(span_seed, "tool", thread.spawning_tool_id, *span_turn)
                        if thread.spawning_tool_id
                        else root_span_id
                    ),
                    name=thread.title,
                    start_time=thread.start,
                    end_time=thread.end,
                    agent_id=thread.thread_id,
                    depth=thread.spawn_depth if thread.spawn_depth is not None else depth,
                    input_messages=thread_task,
                    output_messages=thread_result,
                )
                records.append(
                    replace(
                        spawned,
                        attributes={
                            **spawned.attributes,
                            # How this thread found its parent, stated rather
                            # than inferred. A reader comparing two sub-agents
                            # must be able to tell a recorded spawn edge from a
                            # placement Grove derived from a clock, because only
                            # one of them is evidence about causation.
                            GroveLiveAttr.AGENT_ATTACHMENT: (
                                "spawn_tool" if thread.spawning_tool_id else "turn_window"
                            ),
                        },
                    )
                )
                records.extend(
                    self._thread_spans(
                        span_seed,
                        thread.messages,
                        trace_id=trace_id,
                        agent_span_id=sub_agent_span_id,
                        owner=_SpanOwner(
                            agent_id=thread.thread_id,
                            name=thread.title,
                            depth=(thread.spawn_depth if thread.spawn_depth is not None else depth),
                        ),
                        turn_id=turn.turn_id if ordinal else None,
                        content=content,
                        generation_ids=generation_ids,
                    )
                )
            # Repeated onto EVERY span of the turn, not just its root. LangFuse
            # filters and aggregates across individual observations rather than
            # only at the trace level, so a trace-scoped fact present on the root
            # alone is one a reader cannot narrow by — which is why the vendor's
            # own guidance is to propagate these to all spans.
            common: dict[str, AttributeValue] = {
                LangfuseAttr.SESSION_ID: session_group_id or session_id,
                LangfuseAttr.TRACE_NAME: "agent-turn",
                GenAiAttr.CONVERSATION_ID: session_group_id or session_id,
                GroveLiveAttr.AGENT_SESSION_ID: session_id,
                GroveLiveAttr.AGENT_TURN_ID: turn.turn_id,
            }
            if identity is not None:
                common.update(identity.attributes())
                common[LangfuseAttr.TRACE_TAGS] = identity.tags()
            immutable = tuple(
                replace(record, attributes={**record.attributes, **common}) for record in records
            )
            manifests.append(
                TraceManifest(
                    session_id=session_id,
                    turn_id=turn.turn_id,
                    trace_id=trace_id,
                    spans=immutable,
                )
            )
        return tuple(manifests)

    def _thread_spans(
        self,
        session_id: str,
        messages: Sequence[AgentMessage],
        *,
        trace_id: int,
        agent_span_id: int,
        owner: _SpanOwner,
        turn_id: str | None,
        content: TelemetryContent,
        generation_ids: set[int],
    ) -> list[SpanRecord]:
        """Build generation/tool descendants for a main or sub-agent thread.

        *owner* is stamped onto every record here, which is what makes a tool
        call say who ran it without a tree walk.
        """
        results = _tool_results(messages)
        pending_input: list[ChatMessage] = []
        records: list[SpanRecord] = []
        emit_content = content in {"messages", "all"}
        for message in messages:
            if message.role != "assistant":
                parts = _incoming_parts(message)
                if parts:
                    pending_input.append(ChatMessage(role=_chat_role(message), parts=parts))
                continue
            if message.timestamp is None:
                continue
            turn_input = tuple(pending_input) if emit_content else ()
            pending_input.clear()
            suffix = (turn_id,) if turn_id is not None else ()
            identity = message.message_id or _message_identity(message)
            generation_span_id = derive_span_id(
                session_id,
                "generation",
                identity,
                *suffix,
            )
            duplicate = 1
            while generation_span_id in generation_ids:
                generation_span_id = derive_span_id(
                    session_id,
                    "generation",
                    identity,
                    f"duplicate:{duplicate}",
                    *suffix,
                )
                duplicate += 1
            generation_ids.add(generation_span_id)
            records.append(
                SpanRecord.generation(
                    trace_id=trace_id,
                    span_id=generation_span_id,
                    parent_span_id=agent_span_id,
                    # Zero duration is the honest width, not a placeholder: the
                    # transcript stamps a message once, when it was written, and
                    # carries no request-start or first-token time. Generation
                    # LATENCY is owned by the tiers that watch the call happen —
                    # the agent's own native OTel spans and the wire-level proxy
                    # — and this tier would have to invent it.
                    start_time=message.timestamp,
                    end_time=message.timestamp,
                    model=message.model,
                    usage=message.usage,
                    cost=self._estimate_cost(message.usage, message.model),
                    input_messages=turn_input,
                    output_messages=(
                        (ChatMessage(role="assistant", parts=parts),)
                        if emit_content
                        and (parts := _assistant_parts(message, arguments=content == "all"))
                        else ()
                    ),
                )
            )
            for block in message.content:
                if block.type != "tool_use" or not block.tool_use_id:
                    continue
                tool_span_id = derive_span_id(session_id, "tool", block.tool_use_id, *suffix)
                result = results.get(block.tool_use_id)
                records.append(
                    SpanRecord.tool(
                        trace_id=trace_id,
                        span_id=tool_span_id,
                        parent_span_id=generation_span_id,
                        name=block.tool_name or "tool",
                        start_time=message.timestamp,
                        end_time=(
                            result.timestamp
                            if result is not None and result.timestamp is not None
                            else message.timestamp
                        ),
                        # The harness's own id for this call, and the join a
                        # sub-agent's root span parents itself on. It rides the
                        # span even under `content: "none"` — it is an
                        # identifier, not content, and dropping it would make
                        # the spawn edge unreconstructable for a deployment
                        # that redacts payloads.
                        tool_call_id=block.tool_use_id,
                        tool_input=block.tool_input if content == "all" else None,
                        tool_output=(
                            result.text if result is not None and content == "all" else None
                        ),
                        is_error=result.is_error if result is not None else False,
                    )
                )
        # Applied once, at the one place every descendant leaves this method,
        # rather than at each constructor: provenance that some records carry
        # and others do not is worse than none, because the gap reads as a fact
        # about the call rather than as a missed call site.
        provenance = owner.attributes()
        return [
            replace(record, attributes={**record.attributes, **provenance}) for record in records
        ]

    def _estimate_cost(self, usage: TokenUsage | None, model: str | None) -> CostParts | None:
        """``cost_details`` is emitted only when known. This stays ``None``
        unless a caller injects a ``cost_estimator`` (see
        :func:`price_book_estimator`) — an unpriced generation is honest, a
        guessed one is a number somebody will report on. A failing estimator
        degrades the same way every other best-effort hook here does."""
        if usage is None or self._cost_estimator is None:
            return None
        try:
            return self._cost_estimator(usage, model)
        except Exception as exc:  # best-effort — never break a replay over a bad estimator
            logger.debug("cost_estimator failed: {}", exc)
            return None
