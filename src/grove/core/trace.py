"""Trace instrumentor — replay a session's spine into OTel spans for LangFuse (#175).

The universal backfill tier of epic #170's observability stack: any session
readable through :meth:`AgentMessage <grove.core.agents.model.AgentMessage>`'s
agentic-loop spine (``read_messages``, #179) gets a full LangFuse trace with
zero provider-specific tracing code — in contrast to #177's live wire-level
proxy (richer, TTFT-accurate, but gateway-only). One parse (already paid by
the dashboard poll or a CLI read), one OTel export.

Design decisions worth re-deriving-avoiding:

* **Raw ``opentelemetry-sdk``, never the ``langfuse`` SDK.** LangFuse's own
  ``start_observation()`` has no ``start_time`` parameter, so it cannot
  backfill a session that already happened (research pinned in
  ``agents/CLAUDE.md`` §"Native instrumentation & control"). Building spans
  against the SDK directly is what makes historical replay possible at all.
* **Deterministic W3C ids, derived from session/message/tool-call ids
  (:func:`derive_trace_id` / :func:`derive_span_id`).** LangFuse's OTLP
  ingest adopts incoming ids verbatim and de-dupes on them, so re-running
  :meth:`TraceInstrumentor.replay` against the same session is idempotent —
  no "replayed this session twice" duplicate-trace problem, and no need to
  track "have I already sent this" state anywhere in Grove.
* **A trivial ``Span`` subclass bypasses the SDK's "must be instantiated via
  a tracer" guard** (:func:`sink_from_processor`). The guard is an identity
  check (``cls is Span``), so a one-line subclass sidesteps it — the exact
  technique the SDK's own ``Tracer.start_span`` uses internally. This is
  required because a live ``Tracer`` always generates fresh ids and stamps
  "now" as the start time; replay needs to assign both explicitly from
  :class:`AgentMessage.timestamp <grove.core.agents.model.AgentMessage>`.
* **The emit sink is the seam #177's live proxy shares.** :func:`build_span_sink`
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from loguru import logger

from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.model import AgentMessage, TokenUsage
from grove.core.config import TelemetryConfig

if TYPE_CHECKING:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanProcessor

# ─── the narrow read surface this module needs ──────────────────────────────


class SpineAdapter(Protocol):
    """The read surface :class:`TraceInstrumentor` needs.

    ``read_messages`` is implemented on both filesystem adapters but is
    deliberately NOT yet promoted onto the shared ``AgentAdapter`` Protocol
    (see ``agents/CLAUDE.md``: promote only once a consumer needs it
    polymorphically). This module is that consumer — kept local so adding it
    doesn't force an edit to the shared Protocol file for a Wave-2 story.
    """

    kind: str

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]: ...


# ─── deterministic W3C id derivation (the idempotent-replay contract) ───────

_MASK_128 = (1 << 128) - 1
_MASK_64 = (1 << 64) - 1


def derive_trace_id(session_id: str) -> int:
    """Deterministic 128-bit W3C trace id for a whole session.

    Same session replayed twice (a re-run backfill, a restarted daemon)
    always yields the same trace id, so LangFuse's OTLP ingest de-dupes
    instead of double-recording. Never ``0`` (OTel's invalid-trace sentinel).
    """
    digest = hashlib.sha256(f"grove/trace/{session_id}".encode()).digest()
    return (int.from_bytes(digest[:16], "big") & _MASK_128) or 1


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

ObservationKind = Literal["agent", "generation", "tool"]
AttributeValue = str | int | float | bool
_ATTR_TEXT_CAP = 4000  # a raw tool_input can be huge; cap like every other digest text.


def _usage_details(usage: TokenUsage) -> dict[str, int]:
    """Map :class:`TokenUsage` fields onto LangFuse's ``usage_details`` JSON
    blob. Field NAMES mirror ``TokenUsage`` verbatim rather than guessing
    LangFuse's own cache/reasoning vocabulary (unresolved per epic #170
    research) — a follow-up if LangFuse's cost UI wants different keys. An
    unreported (``None``) field is omitted, never fabricated as ``0``."""
    fields = {
        "input": usage.input,
        "output": usage.output,
        "cache_creation": usage.cache_creation,
        "cache_read": usage.cache_read,
        "reasoning": usage.reasoning,
    }
    return {name: value for name, value in fields.items() if value is not None}


@dataclass(slots=True, frozen=True)
class SpanRecord:
    """One fully-resolved span, provider- and timing-source-agnostic.

    The shared unit both this module's backfill :meth:`TraceInstrumentor.replay`
    and #177's live proxy build and hand to the same :class:`SpanSink` — so a
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
    ) -> SpanRecord:
        """A root session span or a nested sub-agent span (#173's fleet, one
        level of ``agent`` per in-session thread)."""
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            kind="agent",
            start_time=start_time,
            end_time=end_time,
            attributes={"langfuse.observation.type": "agent"},
        )

    @classmethod
    def generation(
        cls,
        *,
        trace_id: int,
        span_id: int,
        parent_span_id: int | None,
        name: str,
        start_time: datetime,
        end_time: datetime,
        model: str | None = None,
        usage: TokenUsage | None = None,
        cost_usd: float | None = None,
        completion_start_time: datetime | None = None,
    ) -> SpanRecord:
        """One LLM turn. ``usage``/``cost_usd``/``completion_start_time`` are
        each omitted (never fabricated) when the caller doesn't have them —
        a backfill replay has no TTFT (not in the transcript, per epic #170
        research); a live proxy (#177) supplies it here."""
        attributes: dict[str, AttributeValue] = {"langfuse.observation.type": "generation"}
        if model:
            attributes["langfuse.observation.model.name"] = model
        if usage is not None:
            details = _usage_details(usage)
            if details:
                attributes["langfuse.observation.usage_details"] = json.dumps(details)
        if cost_usd is not None:
            attributes["langfuse.observation.cost_details"] = json.dumps({"total": cost_usd})
        if completion_start_time is not None:
            attributes["langfuse.observation.completion_start_time"] = (
                completion_start_time.isoformat()
            )
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
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
        tool_input: object | None = None,
    ) -> SpanRecord:
        """One tool call. ``tool_input`` rides as the span's input verbatim
        (capped) — normalizing shape, never interpreting the call's meaning."""
        attributes: dict[str, AttributeValue] = {"langfuse.observation.type": "tool"}
        if tool_input is not None:
            try:
                encoded = json.dumps(tool_input, default=str)
            except (TypeError, ValueError):
                encoded = str(tool_input)
            attributes["langfuse.observation.input"] = encoded[:_ATTR_TEXT_CAP]
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            kind="tool",
            start_time=start_time,
            end_time=end_time,
            attributes=attributes,
        )


class SpanSink(Protocol):
    """The seam #177's live proxy feeds spans through — same exporter/
    processor pipeline :func:`build_span_sink` wires up, so a replayed span
    (this module) and a live span (#177) land identically in LangFuse."""

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


def sink_from_processor(processor: SpanProcessor, resource: Resource) -> SpanSink:
    """Wrap an OTel ``SpanProcessor`` into a :class:`SpanSink`.

    The low-level composable seam: :func:`build_span_sink` calls this with a
    real ``BatchSpanProcessor(OTLPSpanExporter(...))``; a test (or #177's
    live proxy, if it wants a differently-configured processor) can call it
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
        processor.force_flush()

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
    """
    if not cfg.enabled:
        return None
    derived = cfg.derive_env(env if env is not None else os.environ)
    endpoint = derived.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers_raw = derived.get("OTEL_EXPORTER_OTLP_HEADERS")
    if not endpoint or not headers_raw:
        logger.debug("telemetry enabled but LangFuse credentials unresolved; tracing stays no-op")
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError as exc:
        logger.debug(
            "opentelemetry not installed (grove[telemetry] extra); tracing stays no-op: {}", exc
        )
        return None
    exporter = OTLPSpanExporter(
        endpoint=f"{endpoint}/v1/traces", headers=_parse_headers(headers_raw)
    )
    processor = BatchSpanProcessor(exporter)
    resource = Resource.create({"service.name": "grove"})
    return sink_from_processor(processor, resource)


def _bounds(messages: Sequence[AgentMessage]) -> tuple[datetime, datetime] | None:
    """The (earliest, latest) timestamp across a message set, or ``None``
    when every message is timestamp-less (never spans an un-timeable set)."""
    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    if not stamps:
        return None
    return min(stamps), max(stamps)


def _tool_result_timestamps(messages: Sequence[AgentMessage]) -> dict[str, datetime]:
    """``tool_use_id -> the timestamp of its resolving tool_result`` — a
    tool_result is a forward reference (agents/CLAUDE.md), so this is a
    pre-scan mirroring the one every turn/digest projection already does."""
    return {
        block.tool_use_id: msg.timestamp
        for msg in messages
        for block in msg.content
        if block.type == "tool_result" and block.tool_use_id and msg.timestamp is not None
    }


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
    if transcript_path is None:
        return None
    meta_path = transcript_path.with_name(f"{transcript_path.stem}.meta.json")
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tool_use_id = raw.get("toolUseId") if isinstance(raw, dict) else None
    return tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None


class TraceInstrumentor:
    """Replay one session's spine into a full LangFuse trace (#175).

    ``agent`` (root, session-wide) → ``generation`` per assistant message →
    ``tool`` per ``tool_use`` block → nested ``agent`` per sub-agent thread
    (#173's fleet, parented at the exact tool span that spawned it) — the
    fleet tree renders 1:1 as the LangFuse observation tree. Best-effort:
    :meth:`replay` never raises into a caller's poll/backfill loop.
    """

    def __init__(
        self,
        cfg: TelemetryConfig,
        *,
        sink: SpanSink | None = None,
        env: Mapping[str, str] | None = None,
        cost_estimator: Callable[[TokenUsage, str | None], float | None] | None = None,
    ) -> None:
        """``sink`` is the test/#177 injection seam (skips cfg/env entirely
        when supplied); otherwise built from ``cfg`` via :func:`build_span_sink`.
        ``cost_estimator`` is the pluggable seam for ``cost_details`` — Grove
        carries no model-pricing catalog today, so it defaults to ``None``
        (cost is genuinely unknown, never fabricated) until one exists."""
        self._cfg = cfg
        self._sink = sink if sink is not None else build_span_sink(cfg, env=env)
        self._cost_estimator = cost_estimator

    @property
    def enabled(self) -> bool:
        """Whether :meth:`replay` will do anything — config-enabled AND a
        sink actually resolved (credentials present, SDK importable)."""
        return self._cfg.enabled and self._sink is not None

    def replay(self, cwd: Path, session_id: str, adapter: SpineAdapter) -> None:
        """Emit (or re-emit — idempotent, see module docstring) the full
        trace for one session. Never raises: a read failure, an export
        failure, or a disabled instrumentor are all a structured debug log,
        never an exception into the caller's loop."""
        if not self.enabled:
            return
        try:
            self._replay(cwd, session_id, adapter)
        except Exception as exc:  # best-effort — mirrors peek()'s discipline
            logger.debug("trace replay failed for session {}: {}", session_id, exc)

    def _replay(self, cwd: Path, session_id: str, adapter: SpineAdapter) -> None:
        sink = self._sink
        if sink is None:  # pragma: no cover - guarded by `enabled` above
            return
        messages = adapter.read_messages(cwd, session_id)
        if not messages:
            return
        bounds = _bounds(messages)
        if bounds is None:
            return
        start, end = bounds
        trace_id = derive_trace_id(session_id)
        root_span_id = derive_span_id(session_id, "agent", "root")
        sink(
            SpanRecord.agent(
                trace_id=trace_id,
                span_id=root_span_id,
                parent_span_id=None,
                name=f"session:{session_id}",
                start_time=start,
                end_time=end,
            )
        )
        main_thread = tuple(m for m in messages if not m.is_sidechain)
        self._emit_thread_spans(
            sink, session_id, main_thread, trace_id=trace_id, agent_span_id=root_span_id
        )

        if isinstance(adapter, ClaudeCodeAdapter):
            self._emit_fleet(sink, cwd, session_id, adapter, messages, trace_id, root_span_id)
        sink.flush()

    def _emit_fleet(
        self,
        sink: SpanSink,
        cwd: Path,
        session_id: str,
        adapter: ClaudeCodeAdapter,
        messages: tuple[AgentMessage, ...],
        trace_id: int,
        root_span_id: int,
    ) -> None:
        """Nested ``agent`` spans for #173's in-session fleet. Uses
        ``fleet_activity`` for identity (title, sidecar-backed) and re-groups
        the already-read spine by ``thread_id`` for per-message timing —
        ``fleet_activity`` itself only returns aggregated metrics, not the
        per-message detail a span tree needs.

        The spawning tool span is resolved from the sub-agent's own
        ``.meta.json`` sidecar (``toolUseId``), NOT ``AgentMessage.
        parent_tool_use_id`` — ``claude_code.py``'s ``read_subagent_meta``
        documents (verified against 1300+ on-host transcripts) that
        ``sourceToolAssistantUUID`` mirrors the record's own same-thread
        ``parentUuid``, never the main thread's spawning call, so it cannot
        correlate a thread back to its spawn. ``toolUseId`` is the field that
        actually does.
        """
        for sub_session, sub_activity in adapter.fleet_activity(cwd, session_id):
            thread_id = sub_session.session_id
            thread_messages = tuple(
                m for m in messages if m.is_sidechain and m.thread_id == thread_id
            )
            thread_bounds = _bounds(thread_messages)
            if thread_bounds is None:
                continue
            t_start, t_end = thread_bounds
            spawning_tool_use_id = _spawning_tool_use_id(sub_session.transcript_path)
            parent_span_id = (
                derive_span_id(session_id, "tool", spawning_tool_use_id)
                if spawning_tool_use_id
                else root_span_id
            )
            sub_agent_span_id = derive_span_id(session_id, "agent", thread_id)
            sink(
                SpanRecord.agent(
                    trace_id=trace_id,
                    span_id=sub_agent_span_id,
                    parent_span_id=parent_span_id,
                    name=sub_activity.title or f"subagent:{thread_id[:8]}",
                    start_time=t_start,
                    end_time=t_end,
                )
            )
            self._emit_thread_spans(
                sink,
                session_id,
                thread_messages,
                trace_id=trace_id,
                agent_span_id=sub_agent_span_id,
            )

    def _emit_thread_spans(
        self,
        sink: SpanSink,
        session_id: str,
        messages: Sequence[AgentMessage],
        *,
        trace_id: int,
        agent_span_id: int,
    ) -> None:
        """One ``generation`` span per assistant message, one ``tool`` span
        per ``tool_use`` block inside it — the main-thread AND each
        sub-agent-thread walk both call this (same shape, different message
        set), so the two can't drift."""
        tool_result_at = _tool_result_timestamps(messages)
        for index, message in enumerate(messages):
            if message.role != "assistant" or message.timestamp is None:
                continue
            generation_span_id = derive_span_id(
                session_id, "generation", message.message_id or f"idx:{index}"
            )
            sink(
                SpanRecord.generation(
                    trace_id=trace_id,
                    span_id=generation_span_id,
                    parent_span_id=agent_span_id,
                    name=f"generation:{message.model}" if message.model else "generation",
                    start_time=message.timestamp,
                    end_time=message.timestamp,
                    model=message.model,
                    usage=message.usage,
                    cost_usd=self._estimate_cost(message.usage, message.model),
                )
            )
            for block in message.content:
                if block.type != "tool_use" or not block.tool_use_id:
                    continue
                tool_span_id = derive_span_id(session_id, "tool", block.tool_use_id)
                sink(
                    SpanRecord.tool(
                        trace_id=trace_id,
                        span_id=tool_span_id,
                        parent_span_id=generation_span_id,
                        name=block.tool_name or "tool",
                        start_time=message.timestamp,
                        end_time=tool_result_at.get(block.tool_use_id, message.timestamp),
                        tool_input=block.tool_input,
                    )
                )

    def _estimate_cost(self, usage: TokenUsage | None, model: str | None) -> float | None:
        """``cost_details`` is only emitted "when known" (#175) — Grove has
        no pricing catalog today, so this is ``None`` unless a caller injects
        ``cost_estimator``; a failing estimator degrades the same way every
        other best-effort hook here does."""
        if usage is None or self._cost_estimator is None:
            return None
        try:
            return self._cost_estimator(usage, model)
        except Exception as exc:  # best-effort — never break a replay over a bad estimator
            logger.debug("cost_estimator failed: {}", exc)
            return None
