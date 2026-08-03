"""LLM-gateway passthrough proxy — verbatim forward + wire-truth capture.

Everything runs in memory: the proxy's INBOUND side is driven with
``httpx.ASGITransport`` (no socket), and its OUTBOUND client is backed by a
``httpx.MockTransport`` fake upstream (the ``mewbo.py`` boundary discipline).
So the tests pin the two contracts that matter — the forward is byte-for-byte
untouched, and capture (TTFT / usage) is teed off the stream without ever
breaking or delaying it (a raising sink is inert).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from grove.core.config import GroveConfig, ProxyConfig
from grove.core.proxy import CaptureEvent, ProxyApp

UPSTREAM = "https://api.anthropic.com"


class RecordingSink:
    """A capture sink that just remembers what it was handed."""

    def __init__(self) -> None:
        self.events: list[CaptureEvent] = []

    def emit(self, event: CaptureEvent) -> None:
        self.events.append(event)


class RaisingSink:
    """A sink that always fails — the proxy must swallow it, never the request."""

    def emit(self, event: CaptureEvent) -> None:
        raise RuntimeError("sink is down")


async def _abody(data: bytes) -> AsyncIterator[bytes]:
    """Yield a body as a real stream (empty → no chunks), mirroring a live upstream.

    ``httpx.MockTransport`` marks an eager ``content=bytes`` response as already
    consumed, so ``.stream()`` + ``aiter_raw()`` (what the proxy does, and what a
    network upstream needs) raises ``StreamConsumed``. A streaming body is how a
    real upstream actually behaves, so the fakes stream too.
    """
    if data:
        yield data


def _resp(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=_abody(body), headers=headers)


def _upstream_client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def _call(
    app: ProxyApp,
    *,
    method: str = "POST",
    path: str = "/v1/messages",
    headers: dict[str, str] | None = None,
    content: bytes = b"",
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        return await client.request(method, path, headers=headers, content=content)


# ─── verbatim forward ────────────────────────────────────────────────────────


async def test_forwards_request_body_and_auth_verbatim() -> None:
    seen: dict[str, object] = {}
    canned = b'{"model":"claude","messages":[{"role":"user","content":"hi"}]}'

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["auth"] = request.headers.get("authorization")
        seen["beta"] = request.headers.get("anthropic-beta")
        return _resp(200, b'{"ok":true,"usage":{"input_tokens":5}}')

    app = ProxyApp(upstream=UPSTREAM, provider="claude_code", client=_upstream_client(handler))
    resp = await _call(
        app,
        headers={
            "authorization": "Bearer sk-secret",
            "anthropic-beta": "x",
            "content-type": "application/json",
        },
        content=canned,
    )

    # The upstream saw the request byte-identical: same body, same auth header,
    # same custom header, rooted at the real upstream host.
    assert seen["body"] == canned
    assert seen["auth"] == "Bearer sk-secret"
    assert seen["beta"] == "x"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    # And the caller got the upstream response body back untouched.
    assert resp.status_code == 200
    assert resp.content == b'{"ok":true,"usage":{"input_tokens":5}}'


async def test_preserves_query_string_and_method() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return _resp(204)

    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler))
    await _call(app, method="GET", path="/v1/models?limit=3")

    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.anthropic.com/v1/models?limit=3"


# ─── TTFT + latency ──────────────────────────────────────────────────────────


async def test_ttft_is_the_first_chunk_wall_clock() -> None:
    ticks = [0.0]

    async def slow_stream() -> AsyncIterator[bytes]:
        ticks[0] = 0.5  # half a "second" passes before the first token
        yield b"chunk-1"
        ticks[0] = 2.0  # the rest of the stream finishes later
        yield b"chunk-2"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_stream())

    sink = RecordingSink()
    app = ProxyApp(
        upstream=UPSTREAM,
        provider="claude_code",
        client=_upstream_client(handler),
        sink=sink,
        clock=lambda: ticks[0],
    )
    resp = await _call(app)

    assert resp.content == b"chunk-1chunk-2"
    (event,) = sink.events
    # TTFT reflects the first chunk (0.5s), latency the whole stream (2.0s).
    assert event.ttft_ms == pytest.approx(500.0)
    assert event.latency_ms == pytest.approx(2000.0)
    assert event.ttft_ms is not None and event.ttft_ms <= event.latency_ms
    assert event.status == 200
    assert event.provider == "claude_code"
    assert event.path == "/v1/messages"


# ─── usage capture ───────────────────────────────────────────────────────────


async def test_captures_usage_from_json_body() -> None:
    body = (
        b'{"id":"msg","usage":{"input_tokens":11,"output_tokens":22,"cache_read_input_tokens":3}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, body)

    sink = RecordingSink()
    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=sink)
    await _call(app)

    (event,) = sink.events
    assert event.usage == {"input_tokens": 11, "output_tokens": 22, "cache_read_input_tokens": 3}
    assert event.response_size == len(body)


async def test_captures_usage_from_sse_stream() -> None:
    # Anthropic shape: input_tokens land in message_start, the final
    # output_tokens in a later message_delta — the merge takes the last of each.
    start = '{"type":"message_start","message":{"usage":{"input_tokens":100,"output_tokens":1}}}'
    delta = '{"type":"message_delta","usage":{"output_tokens":57}}'
    sse = (
        b"event: message_start\n"
        b"data: " + start.encode() + b"\n\n"
        b"event: message_delta\n"
        b"data: " + delta.encode() + b"\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, sse, headers={"content-type": "text/event-stream"})

    sink = RecordingSink()
    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=sink)
    await _call(app)

    (event,) = sink.events
    assert event.usage == {"input_tokens": 100, "output_tokens": 57}


async def test_no_usage_when_response_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, b"plain text, not json")

    sink = RecordingSink()
    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=sink)
    await _call(app)

    (event,) = sink.events
    assert event.usage is None


# ─── content-gating ──────────────────────────────────────────────────────────


async def test_request_body_captured_only_when_log_bodies() -> None:
    canned = b'{"prompt":"secret-ish"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200)

    off = RecordingSink()
    app_off = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=off)
    await _call(app_off, content=canned)
    assert off.events[0].request_body is None
    assert off.events[0].request_size == len(canned)

    on = RecordingSink()
    app_on = ProxyApp(
        upstream=UPSTREAM,
        client=_upstream_client(handler),
        sink=on,
        log_bodies=True,
        max_body_bytes=8,
    )
    await _call(app_on, content=canned)
    assert on.events[0].request_body == canned[:8].decode()


async def test_capture_never_carries_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200)

    sink = RecordingSink()
    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=sink)
    await _call(app, headers={"authorization": "Bearer sk-secret"})

    assert dict(sink.events[0].headers) == {}


# ─── best-effort isolation ───────────────────────────────────────────────────


async def test_raising_sink_never_breaks_the_forward() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(200, b"forwarded fine")

    app = ProxyApp(upstream=UPSTREAM, client=_upstream_client(handler), sink=RaisingSink())
    resp = await _call(app)

    # The sink blew up, but the caller still got the upstream response verbatim.
    assert resp.status_code == 200
    assert resp.content == b"forwarded fine"


async def test_upstream_failure_returns_502_and_records_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream down")

    sink = RecordingSink()
    app = ProxyApp(
        upstream=UPSTREAM, provider="claude_code", client=_upstream_client(handler), sink=sink
    )
    resp = await _call(app)

    assert resp.status_code == 502
    (event,) = sink.events
    assert event.status == 502
    assert event.error is not None and "ConnectError" in event.error


# ─── ProxyConfig ─────────────────────────────────────────────────────────────


def test_config_defaults_off() -> None:
    cfg = ProxyConfig()
    assert cfg.enabled is False
    assert cfg.host == "127.0.0.1"
    assert cfg.log_bodies is False
    assert GroveConfig().proxy.enabled is False


def test_proxy_env_disabled_yields_nothing() -> None:
    cfg = ProxyConfig(enabled=False)
    assert cfg.proxy_env("claude_code") == {}


def test_proxy_env_points_each_kind_at_the_proxy() -> None:
    cfg = ProxyConfig(enabled=True, host="127.0.0.1", port=9999)
    assert cfg.proxy_env("claude_code") == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"}
    assert cfg.proxy_env("codex") == {"OPENAI_BASE_URL": "http://127.0.0.1:9999"}


def test_proxy_env_empty_for_unconfigured_kind() -> None:
    cfg = ProxyConfig(enabled=True)
    # generic/mewbo have no gateway env seam by default → nothing derived.
    assert cfg.proxy_env("generic") == {}
    assert cfg.proxy_env("mewbo") == {}


def test_from_config_selects_the_kind_upstream() -> None:
    cfg = ProxyConfig(enabled=True)
    app = ProxyApp.from_config(cfg, "claude_code")
    assert app._upstream == "https://api.anthropic.com"
    app_codex = ProxyApp.from_config(cfg, "codex")
    assert app_codex._upstream == "https://api.openai.com/v1"
