"""One request id and one trace context per HTTP request, carried across a federation hop.

The daemon is the outermost hop a request reaches, so this layer gives it an
``X-Request-ID`` (the caller's, else a fresh one) and a W3C ``traceparent``
(the caller's, else a fresh root) by writing both into the ASGI scope's
headers. Everything below reads them from there, including the federation
proxy, which forwards headers untouched, so a node continues the hub's trace
and logs the hub's request id without either side passing anything by hand.

After the response it emits one audit span, parented to that trace context, naming
the route, the status and the principal the auth gate admitted. The span sink
is the daemon's own (``build_span_sink``), so with telemetry off this costs one
id per request and nothing else.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from grove.core.telemetry.semconv import GroveIdentityAttr, GroveRequestAttr, LangfuseAttr
from grove.core.trace import AttributeValue, SpanRecord, SpanSink

#: ``version-traceid-spanid-flags``, version 00 (W3C Trace Context section 3.2).
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


class RequestAudit:
    """ASGI middleware: request id + trace context in, audit span out."""

    def __init__(self, app: ASGIApp, *, sink: SpanSink | None, node: str) -> None:
        """Args:
        app: the application this wraps.
        sink: where the audit span goes, or ``None`` when telemetry is off.
        node: this daemon's node name, stamped on every span it emits.
        """
        self._app = app
        self._sink = sink
        self._node = node

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = _header_map(scope)
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or secrets.token_hex(16)
        trace_id, parent_span_id = _parse_traceparent(headers.get(b"traceparent", b""))
        span_id = secrets.randbits(64) or 1
        if trace_id is None:
            trace_id = secrets.randbits(128) or 1
        _set_header(scope, b"x-request-id", request_id.encode("latin-1"))
        # Downstream (and the federation proxy) see THIS span as the parent, so a
        # forwarded request's node span sits under the hub's audit span.
        _set_header(scope, b"traceparent", f"00-{trace_id:032x}-{span_id:016x}-01".encode())
        state: MutableMapping[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        started = datetime.now(UTC)
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self._app(scope, receive, send_with_id)
        finally:
            self._emit(scope, request_id, trace_id, span_id, parent_span_id, status, started)

    def _emit(
        self,
        scope: Scope,
        request_id: str,
        trace_id: int,
        span_id: int,
        parent_span_id: int | None,
        status: int,
        started: datetime,
    ) -> None:
        """One span per request that an authenticated caller made; best-effort."""
        principal = scope.get("state", {}).get("principal")
        if self._sink is None or principal is None:
            return
        route = scope.get("route")
        attributes: dict[str, AttributeValue] = {
            GroveRequestAttr.REQUEST_ID: request_id,
            GroveRequestAttr.METHOD: str(scope.get("method", "")),
            GroveRequestAttr.ROUTE: str(getattr(route, "path", scope.get("path", ""))),
            GroveRequestAttr.STATUS: status,
            GroveIdentityAttr.PRINCIPAL_SUBJECT: principal.subject,
            GroveIdentityAttr.NODE_NAME: self._node,
            LangfuseAttr.USER_ID: principal.subject,
        }
        try:
            self._sink(
                SpanRecord(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    name=f"{attributes[GroveRequestAttr.METHOD]} "
                    f"{attributes[GroveRequestAttr.ROUTE]}",
                    kind="tool",
                    start_time=started,
                    end_time=datetime.now(UTC),
                    attributes=attributes,
                )
            )
        except Exception as exc:
            logger.debug("request audit span not emitted: {}", exc)


def _header_map(scope: Scope) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _set_header(scope: Scope, name: bytes, value: bytes) -> None:
    """Replace (or add) one header in the scope, so every layer below reads the same value."""
    scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() != name]
    scope["headers"].append((name, value))


def _parse_traceparent(raw: bytes) -> tuple[int | None, int | None]:
    """The caller's ``(trace id, parent span id)``, or ``(None, None)`` for none or a malformed one.

    A malformed header starts a fresh root rather than being refused: tracing
    is observation, and a bad header must never cost the request.
    """
    match = _TRACEPARENT.match(raw.decode("latin-1").strip().lower())
    if match is None:
        return None, None
    trace_id, parent = int(match.group(1), 16), int(match.group(2), 16)
    if not trace_id or not parent:
        return None, None
    return trace_id, parent


__all__ = ["RequestAudit"]
