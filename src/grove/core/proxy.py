"""Loopback LLM-gateway passthrough proxy — wire-truth capture at the edge (#177).

A Grove-owned HTTP proxy an agent points at (``ANTHROPIC_BASE_URL`` for Claude,
the ``model_providers`` base-url env for Codex — see :meth:`ProxyConfig.proxy_env`).
It forwards provider traffic **verbatim** to the real upstream — body untouched,
headers (including ``authorization``) untouched and NEVER logged — while teeing
per-exchange telemetry off the stream: the request body (content-gated), the
true time-to-first-token (the wall-clock of the first response byte, the one
signal absent from stream-json and the transcript), the terminal token usage,
total latency, and status.

Two boundaries, both deliberate:

* **Inbound is a plain ASGI app** (``ProxyApp.__call__``) — a protocol, not a
  dependency, so ``grove.core`` stays lean. Tests drive it in-memory with
  ``httpx.ASGITransport``; production binds it with :meth:`ProxyApp.serve`
  (uvicorn, imported lazily so the module imports without the daemon extra).
* **Outbound is an injected ``httpx.AsyncClient``** whose transport is the test
  seam (``httpx.MockTransport`` fakes the upstream — the ``mewbo.py`` discipline).

The capture is emitted to a pluggable :class:`CaptureSink` (a one-method
Protocol). The default :class:`LoggingCaptureSink` is dependency-free and logs
metadata only; the OTel/LangFuse exporter (#175) is wired in by the orchestrator
at integration behind this same interface — ``proxy.py`` never hard-depends on
opentelemetry. **A capture or emit failure NEVER breaks or delays the forward
path**: bytes are relayed downstream before the tee touches them, and every sink
call is guarded (bounded, logged per outcome, never re-raised) — the proxy is a
transparent wire first and a telemetry tap second.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from loguru import logger

if TYPE_CHECKING:
    from grove.core.config import AgentKind, ProxyConfig

# Headers that describe THIS hop's framing, not the payload — a proxy must not
# relay them or the downstream ASGI server double-frames the response (a chunked
# upstream + a forwarded ``transfer-encoding: chunked`` header = a broken body).
# The request-host is dropped separately so httpx addresses the real upstream.
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)

# Hard ceiling on the response bytes buffered FOR USAGE PARSING only — never a
# limit on what is forwarded (every chunk is relayed regardless). LLM turn
# responses (JSON or SSE) sit far under this; the cap only bounds a pathological
# large-file passthrough so a tee can't grow memory without bound.
_USAGE_SCAN_CAP = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CaptureEvent:
    """One normalized proxied exchange — the telemetry projection of a forward.

    Intentionally metadata + numbers, never response content: only ``usage``
    (the provider's own token counts, extracted by shape) is kept from the
    response, and ``request_body`` is populated ONLY when the config opts in
    (``log_bodies``), truncated to the cap. Auth/headers are never carried here.
    ``ttft_ms`` is ``None`` when the upstream produced no body byte (an empty or
    failed response); ``error`` is set when the forward itself failed.
    """

    provider: str
    """The upstream identity (the ``AgentKind`` this proxy instance fronts)."""

    method: str
    path: str
    """Request path + query as received (no scheme/host)."""

    status: int
    """Upstream HTTP status; ``0`` if the forward never got a response."""

    ttft_ms: float | None
    """True time-to-first-token: wall-clock ms from request-sent to the first
    response byte. The signal stream-json and the JSONL transcript both omit."""

    latency_ms: float
    """Total wall-clock ms from request-sent to the last response byte."""

    usage: Mapping[str, int] | None = None
    """The provider's own ``usage`` token counts (``input_tokens`` /
    ``output_tokens`` / cache / reasoning …), extracted by SHAPE from the
    response — never renamed or re-interpreted (the provider boundary). ``None``
    when the response carried no recognizable usage object."""

    request_size: int = 0
    response_size: int = 0
    request_body: str | None = None
    """The request body, decoded and truncated — populated ONLY when
    ``ProxyConfig.log_bodies`` is set (content-gating); otherwise ``None``."""

    error: str | None = None
    """Set when forwarding raised (upstream unreachable, transport error). The
    client still receives a ``502``; the capture records why."""

    headers: Mapping[str, str] = field(default_factory=dict)
    """Deliberately EMPTY by construction — headers (auth included) are never
    captured or logged. Present as an explicit contract, not a container."""


class CaptureSink(Protocol):
    """Where a :class:`CaptureEvent` goes — the one seam the exporter plugs into.

    ``emit`` is synchronous and called ONCE per exchange, AFTER the last byte is
    relayed downstream (off the forward hot path). An implementation must be
    non-blocking or fire-and-forget (the OTel/LangFuse sink batches); the proxy
    guards every call, so a slow or raising sink degrades capture, never the
    proxied request. Grove ships the dependency-free :class:`LoggingCaptureSink`;
    #175 supplies the real OTLP sink behind this same interface.
    """

    def emit(self, event: CaptureEvent) -> None: ...


class LoggingCaptureSink:
    """The default sink: a structured debug line, metadata only, zero deps.

    Never logs the request body or any header — only the numbers that make the
    capture useful at a glance (status, TTFT, latency, usage). Swap in the OTel
    sink (#175) for real ingestion; this keeps the proxy self-contained.
    """

    def emit(self, event: CaptureEvent) -> None:
        logger.debug(
            "proxy {} {} {} status={} ttft_ms={} latency_ms={:.1f} usage={}",
            event.provider,
            event.method,
            event.path,
            event.status,
            None if event.ttft_ms is None else round(event.ttft_ms, 1),
            event.latency_ms,
            dict(event.usage) if event.usage else None,
        )


class ProxyApp:
    """ASGI passthrough proxy for one upstream, teeing capture off each exchange.

    Construct one per provider (its ``upstream`` base URL + ``provider`` label);
    the outbound ``client`` and the ``sink`` are injectable seams (a
    ``MockTransport``-backed client and a capturing sink in tests, the real OTLP
    sink in production). ``clock`` is injectable for deterministic TTFT tests
    (the ``release.py`` precedent).
    """

    def __init__(
        self,
        *,
        upstream: str,
        provider: str = "",
        sink: CaptureSink | None = None,
        client: httpx.AsyncClient | None = None,
        log_bodies: bool = False,
        max_body_bytes: int = 8192,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._upstream = upstream.rstrip("/")
        self._provider = provider
        self._sink = sink or LoggingCaptureSink()
        # timeout=None: an LLM turn streams for minutes; the proxy must not
        # impose its own deadline on a legitimately long generation.
        self._client = client or httpx.AsyncClient(timeout=None)
        self._log_bodies = log_bodies
        self._max_body_bytes = max_body_bytes
        self._clock = clock

    @classmethod
    def from_config(
        cls,
        cfg: ProxyConfig,
        kind: AgentKind,
        *,
        sink: CaptureSink | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> ProxyApp:
        """Build the proxy for one agent kind from a :class:`ProxyConfig`.

        The orchestrator constructs one app per passthrough kind, pointing it at
        that kind's configured upstream, and serves each; the launch boundary
        then feeds the agent :meth:`ProxyConfig.proxy_env` so it dials the proxy.
        """
        return cls(
            upstream=cfg.upstreams.get(kind, ""),
            provider=kind,
            sink=sink,
            client=client,
            log_bodies=cfg.log_bodies,
            max_body_bytes=cfg.max_body_bytes,
        )

    # ─── ASGI entry ──────────────────────────────────────────────────────────

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._lifespan(receive, send)
        elif kind == "http":
            await self._handle_http(scope, receive, send)
        # websocket/other: silently ignored — an LLM gateway is HTTP-only.

    @staticmethod
    async def _lifespan(
        receive: Callable[[], Any], send: Callable[[Mapping[str, Any]], Any]
    ) -> None:
        """Ack uvicorn's startup/shutdown so the server boots; no state to manage."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _handle_http(
        self,
        scope: Mapping[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        method = scope["method"]
        raw_path = scope.get("raw_path") or scope["path"].encode("latin-1")
        path = raw_path.decode("latin-1")
        query: bytes = scope.get("query_string", b"")
        url = self._upstream + path + (f"?{query.decode('latin-1')}" if query else "")

        # Forward every header VERBATIM except the request host — httpx must
        # address the real upstream, not echo the loopback listener. Auth and
        # every other header pass untouched; none is logged, anywhere.
        fwd_headers = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope["headers"]
            if k.lower() != b"host"
        ]
        body = await self._read_request_body(receive)

        start = self._clock()
        first_at: float | None = None
        status = 0
        response_size = 0
        scanned = bytearray()
        error: str | None = None

        try:
            async with self._client.stream(method, url, headers=fwd_headers, content=body) as resp:
                status = resp.status_code
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": self._response_headers(resp),
                    }
                )
                async for chunk in resp.aiter_raw():
                    if first_at is None:
                        first_at = self._clock()
                    # Relay downstream FIRST — capture must never delay a byte.
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    response_size += len(chunk)
                    if len(scanned) < _USAGE_SCAN_CAP:
                        scanned.extend(chunk)
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("proxy forward to {} failed: {}", self._provider, error)
            if status == 0:
                # No response line sent yet — surface a 502 to the caller.
                await send({"type": "http.response.start", "status": 502, "headers": []})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                status = 502

        end = self._clock()
        self._emit(
            CaptureEvent(
                provider=self._provider,
                method=method,
                path=path,
                status=status,
                ttft_ms=None if first_at is None else (first_at - start) * 1000,
                latency_ms=(end - start) * 1000,
                usage=self._extract_usage(bytes(scanned)),
                request_size=len(body),
                response_size=response_size,
                request_body=self._gated_body(body),
                error=error,
            )
        )

    # ─── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _read_request_body(receive: Callable[[], Any]) -> bytes:
        """Aggregate the full ASGI request body (needed to capture + forward it).

        LLM request bodies are small JSON — reading fully is cheap and is the
        only way to both capture the request and hand httpx a stable ``content``.
        """
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            body.extend(message.get("body", b""))
            more = message.get("more_body", False)
        return bytes(body)

    @staticmethod
    def _response_headers(resp: httpx.Response) -> list[tuple[bytes, bytes]]:
        """Upstream response headers, verbatim minus this-hop framing headers."""
        return [
            (name, value)
            for name, value in resp.headers.raw
            if name.decode("latin-1").lower() not in _HOP_BY_HOP
        ]

    def _gated_body(self, body: bytes) -> str | None:
        """The request body decoded + truncated — only when ``log_bodies`` opts in."""
        if not self._log_bodies:
            return None
        return body[: self._max_body_bytes].decode("utf-8", "replace")

    def _emit(self, event: CaptureEvent) -> None:
        """Hand the capture to the sink — best-effort, isolated at the edge.

        A raising or slow sink must never break or delay a proxied request, so
        the call is bounded to a guarded try/except with a structured log per
        failure and no re-raise (the best-effort side-effect discipline).
        """
        try:
            self._sink.emit(event)
        except Exception as exc:
            logger.warning("proxy capture sink failed for {}: {}", self._provider, exc)

    @staticmethod
    def _extract_usage(raw: bytes) -> dict[str, int] | None:
        """Best-effort token usage from a response body — by SHAPE, never semantics.

        Handles both wire shapes Grove's providers use: a plain JSON body with a
        top-level (or ``message.usage``) ``usage`` object, and an SSE stream
        whose ``data:`` frames each carry a fragment of it (Anthropic emits
        ``input_tokens`` in ``message_start`` and the final ``output_tokens`` in
        ``message_delta``; OpenAI carries a single terminal ``usage``). Every
        int-valued field under the provider's own ``usage`` key is kept as-is —
        no renaming, no derived totals (that would interpret model semantics).
        Any parse failure yields ``None`` — this feeds telemetry, never the
        forward.
        """
        if not raw:
            return None
        text = raw.decode("utf-8", "replace")
        merged: dict[str, int] = {}
        if "data:" in text:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    ProxyApp._merge_usage(merged, json.loads(payload))
                except (json.JSONDecodeError, ValueError):
                    continue
        if not merged:
            try:
                ProxyApp._merge_usage(merged, json.loads(text))
            except (json.JSONDecodeError, ValueError):
                return None
        return merged or None

    @staticmethod
    def _merge_usage(acc: dict[str, int], obj: Any) -> None:
        """Fold an object's ``usage`` int fields into ``acc`` (last non-null wins)."""
        if not isinstance(obj, dict):
            return
        sources = [obj.get("usage")]
        message = obj.get("message")
        if isinstance(message, dict):
            sources.append(message.get("usage"))
        for source in sources:
            if isinstance(source, dict):
                for key, value in source.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        acc[key] = value

    # ─── serving (the runnable seam) ─────────────────────────────────────────

    async def serve(self, *, host: str, port: int) -> None:
        """Bind the proxy on ``host:port`` with uvicorn (imported lazily).

        The one line that makes the proxy an actual loopback listener an agent
        can dial. uvicorn is imported here, not at module load, so
        ``import grove.core.proxy`` needs no server extra — the same guard the
        daemon CLI uses. The orchestrator (not the manager) owns calling this
        and wiring :meth:`ProxyConfig.proxy_env` into the launch env.
        """
        import uvicorn  # noqa: PLC0415 — lazy so `import grove.core.proxy` needs no server extra

        config = uvicorn.Config(self, host=host, port=port, log_level="warning")
        await uvicorn.Server(config).serve()
