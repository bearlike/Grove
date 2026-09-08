"""Grove channel — native delivery + permission relay via Claude Code Channels.

A *channel* is Claude Code's native seam for pushing a message a **running,
interactive** session acts on, and for relaying a permission decision back to
the agent. It is the third leg of the agent-control surface, distinct from the
other two Grove already has:

- a **hook** (``grove.core.agents.hook``) pushes status ONE way (agent → Grove);
- **steering** (``tmux.send_text`` / ``send_keys``) types raw keystrokes at the
  pane, which races the agent's own turn;
- a **channel** is a first-class, structured, bidirectional MCP transport the
  agent connects to — Grove delivers ``notifications/claude/channel`` the agent
  consumes mid-turn, exposes a ``reply`` tool the agent calls back through, and
  answers ``claude/channel/permission`` requests with allow/deny.

Wiring (all opt-in behind ``cfg.channels.enabled``, default off):

1. ``WorkspaceManager._compose_launch`` appends ``--channels <settings>`` to a
   ``claude_code`` launch (mirroring the hook ``--settings`` append), so the
   agent boots already connected to this server. Disabled ⇒ no flag ⇒ no-op.
2. This module is the stdio MCP **channel server** Claude Code spawns from that
   settings file (``python -m grove.core.channel``). It declares the
   ``claude/channel`` experimental capability, emits deliveries, exposes
   ``reply``, and relays permission requests.
3. A loopback HTTP **receiver** accepts queued messages the Grove daemon POSTs;
   each delivery is emitted into the live session as a channel notification.
   The daemon-side POST route is a deliberate TODO seam (see ``ChannelReceiver``)
   — the launch composition, this server, and the config toggle are the
   deliverable here; the queue plumbing lands with the daemon work.

Side-effect discipline: the MCP SDK ships behind the opt-in ``[mcp]`` extra (a
``.[daemon]``-only host lacks it), so every SDK import is **lazy**, exactly like
``grove.core.mewbo``'s HTTP client and ``grove.mcp.server``'s FastMCP load — the
module imports and its pure surface tests without the SDK present. The
``claude/channel/*`` methods are an unstable Claude Code research-preview
protocol with no typed shapes in ``mcp.types``, so the live transport narrows
them at the boundary (``Any``) while the pure envelope/policy layer stays
strictly typed. Dependencies flow inward — this imports ``config``/``paths``,
never the manager.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, ClassVar, Final

from loguru import logger

from grove._mcp_sdk import McpSdk
from grove.core import paths
from grove.core.admission import Admission, AdmissionLimits, BoundedInbox
from grove.core.config import ChannelsConfig, load_config

# The MCP SDK is behind the opt-in `[mcp]` extra, so every import of it here is
# deferred. `McpSdk` reports an SDK that is installed but incompatible as such,
# naming its version — a bare `except ImportError` around a deep submodule
# import cannot tell that apart from an absent distribution and hands the
# operator an install hint for something they already have.
_SDK = McpSdk("grove channel server")

# The experimental capability key Claude Code negotiates a channel over. A bare
# ``{}`` value is the "present, no sub-options" declaration — the mechanism is
# the presence of the key, so an evolving preview can add sub-fields later
# without changing this seam.
CAPABILITY_KEY: Final = "claude/channel"

# The outbound delivery notification method and the inbound permission-request
# method — the two custom JSON-RPC methods of the channel protocol. Constants,
# not literals scattered across the transport, so a protocol rename is one edit.
DELIVER_NOTIFICATION: Final = "notifications/claude/channel"
PERMISSION_REQUEST: Final = "claude/channel/permission"

# The one tool this server exposes: the agent's reply path back to Grove.
REPLY_TOOL: Final = "reply"

# The loopback receiver's POST path + the config-dir files that let the daemon
# discover the bound port and authenticate. Same-host-secret discipline as the
# hook ingest token (``ClaudeHook.ensure_ingest_token``): the daemon and this
# server both read one file rather than threading a token through a launch flag.
RECEIVE_ROUTE: Final = "/channel/messages"
# How long one loopback peer may take to send the body it declared. Generous for
# a same-host client that has already been admitted by the length cap, and short
# enough that a stalled one cannot hold the single receiver thread.
_REQUEST_READ_TIMEOUT_SECONDS: Final = 5.0
_SETTINGS_FILENAME: Final = "claude-channel-settings.json"
_ENDPOINT_FILENAME: Final = "channel-endpoint.json"

# Env vars the launch flag / daemon use to configure a spawned server. The port
# is 0 by default (bind an ephemeral loopback port, publish it to the endpoint
# file); a fixed port is only for a pinned deployment.
ENV_PORT: Final = "GROVE_CHANNEL_PORT"


# ─── pure envelope + policy (fully typed, SDK-free, unit-tested) ─────────────


@dataclass(slots=True, frozen=True)
class ChannelMessage:
    """One delivery: the ``{content, meta}`` payload of a channel notification.

    ``content`` is the human/agent-facing message text the running session acts
    on; ``meta`` is free-form structured context (never interpreted here —
    forwarded verbatim, the provider boundary). ``sender`` is the declared
    origin used for the allowlist check; it is dropped from the emitted params
    (it is Grove-side attribution, not channel-protocol payload).
    """

    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    sender: str | None = None

    @classmethod
    def from_payload(cls, raw: object) -> ChannelMessage | None:
        """Parse an inbound POST body; ``None`` on anything malformed (best-effort).

        A receiver must never raise on a junk body — a bad delivery is dropped,
        never crashing the server that a live session depends on.
        """
        if not isinstance(raw, dict):
            return None
        content = raw.get("content")
        if not isinstance(content, str) or not content:
            return None
        meta = raw.get("meta")
        sender = raw.get("sender")
        return cls(
            content=content,
            meta=meta if isinstance(meta, dict) else {},
            sender=sender if isinstance(sender, str) and sender else None,
        )

    def to_notification_params(self) -> dict[str, Any]:
        """The ``notifications/claude/channel`` params: ``{content, meta}`` only."""
        return {"content": self.content, "meta": self.meta}


@dataclass(slots=True, frozen=True)
class PermissionDecision:
    """The resolution of a ``claude/channel/permission`` request: allow or deny.

    ``behavior`` is the wire verb the agent expects; ``reason`` is an optional
    human-readable note surfaced on a deny. A structured value (not a bare bool)
    so the relay can carry *why* — and so a future third state (ask-the-human)
    extends this without reshaping every call site.
    """

    ALLOW: ClassVar[str] = "allow"
    DENY: ClassVar[str] = "deny"

    behavior: str
    reason: str | None = None

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"behavior": self.behavior}
        if self.reason is not None:
            result["message"] = self.reason
        return result


class ChannelPolicy:
    """Sender allowlist + permission relay — the security seam of the channel.

    Both decisions are **fail-closed**: an inbound delivery whose sender is not
    permitted is dropped, and a permission request with no wired human/daemon
    resolver is DENIED (never silently allowed). Enabling the feature is the
    deliberate opt-in; the allowlist RESTRICTS further, and an unwired permission
    relay must not become an "allow everything" hole.
    """

    def __init__(self, allowed_senders: tuple[str, ...]) -> None:
        # A tuple, immutable — the policy is fixed for the process lifetime (it
        # is resolved once from config at spawn).
        self._allowed = allowed_senders

    def permits_sender(self, sender: str | None) -> bool:
        """Whether ``sender`` may deliver into the running session.

        Empty allowlist ⇒ allow all (the bare-enable permissive default,
        documented on ``ChannelsConfig.allowed_senders``). A populated list is a
        strict membership test; an unattributed delivery (``sender is None``) is
        rejected once the list is non-empty — you cannot pass a named gate
        anonymously.
        """
        if not self._allowed:
            return True
        return sender is not None and sender in self._allowed

    def resolve_permission(self, request: dict[str, Any]) -> PermissionDecision:
        """Relay a ``claude/channel/permission`` request to an allow/deny decision.

        TODO(daemon plumbing): route this to the human/daemon (the same
        loopback channel the receiver uses) so an operator answers a live
        permission prompt. Until that resolver is wired, this is fail-closed:
        every request is DENIED with a clear reason, so enabling channels can
        never widen what a session is allowed to do without an explicit answer.
        """
        tool = request.get("tool_name") if isinstance(request, dict) else None
        detail = f" for {tool}" if isinstance(tool, str) and tool else ""
        return PermissionDecision(
            behavior=PermissionDecision.DENY,
            reason=f"Grove channel permission relay is not wired{detail}; denying (fail-closed).",
        )


@dataclass(slots=True, frozen=True)
class ChannelEndpoint:
    """The bound loopback receiver's discovery record (port + same-host token).

    Published to a config-dir file on bind so the Grove daemon can find the
    ephemeral port and authenticate its POSTs without the port being threaded
    through the launch flag. Mirrors the hook ingest-token discipline.
    """

    port: int
    token: str

    def to_json(self) -> dict[str, Any]:
        return {"port": self.port, "token": self.token}

    @classmethod
    def from_json(cls, data: object) -> ChannelEndpoint | None:
        if not isinstance(data, dict):
            return None
        port = data.get("port")
        token = data.get("token")
        if not isinstance(port, int) or not isinstance(token, str) or not token:
            return None
        return cls(port=port, token=token)


# ─── config-dir seams (reuse existing public `paths` helpers, no paths.py edit) ─


def channel_settings_path() -> Path:
    """Grove's channel settings file — sibling of the hook settings, same dir."""
    return paths.user_config_path().parent / _SETTINGS_FILENAME


def channel_endpoint_path() -> Path:
    """The bound-receiver discovery file the daemon reads to POST deliveries."""
    return paths.user_config_path().parent / _ENDPOINT_FILENAME


def channel_settings() -> dict[str, Any]:
    """The ``--channels`` settings dict declaring Grove as a channel server.

    Mirrors ``ClaudeHook.settings``: Grove writes this to its own file and passes
    it via ``claude --channels`` so the user's own config is never touched
    (uninstall = stop passing the flag). The server command is
    ``python -m grove.core.channel`` (the running interpreter, so no console
    script or ``PATH`` assumption), and the block declares the experimental
    ``claude/channel`` capability the agent negotiates over. The exact schema is
    a Claude Code research preview; the shape here is the MCP-server-style
    declaration Grove owns — an evolving preview refines it in one place.
    """
    return {
        "channels": {
            "grove": {
                "command": sys.executable,
                "args": ["-m", "grove.core.channel"],
                "capabilities": {"experimental": {CAPABILITY_KEY: {}}},
            }
        }
    }


# ─── loopback receiver (server side of the daemon → session delivery path) ──────


class ChannelDeliveryStatus(StrEnum):
    """A terminal disposition for an accepted channel delivery."""

    SENT = "sent"
    FAILED = "failed"
    UNDLVRD_PENDING = "undelivered_pending"
    UNDLVRD_IN_FLIGHT = "undelivered_in_flight"


class ChannelDeliveryLedger:
    """Records bounded terminal delivery outcomes without retaining message bodies."""

    def __init__(self, *, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._outcomes: deque[tuple[ChannelDeliveryStatus, int]] = deque(maxlen=capacity)

    @property
    def outcomes(self) -> tuple[tuple[ChannelDeliveryStatus, int], ...]:
        """Terminal statuses and content lengths, in delivery order."""
        return tuple(self._outcomes)

    def record(self, message: ChannelMessage, status: ChannelDeliveryStatus) -> None:
        """Publish an outcome with safe attribution, never its content or metadata."""
        self._outcomes.append((status, len(message.content)))
        logger.debug("channel delivery {} ({} chars)", status, len(message.content))


class ChannelReceiver:
    """Loopback HTTP receiver the daemon POSTs queued channel messages to.

    Bind-only-to-127.0.0.1, ephemeral port by default, gated by a same-host
    bearer token. A valid POST body (``{content, meta, sender}``) whose sender
    the policy permits reserves the message in a bounded inbox before any
    cross-thread wakeup; overload is refused explicitly. The side effect (the
    socket) lives here at the edge; parsing + admission are the pure
    ``handle_body`` seam so the accept/reject logic is testable without a socket.

    TODO(daemon plumbing): the matching daemon-side POST route (read the
    endpoint file, authenticate, forward a delivery) is intentionally deferred —
    this receiver is the ready seam it targets.
    """

    def __init__(
        self,
        policy: ChannelPolicy,
        token: str,
        inbox: BoundedInbox[ChannelMessage],
        limits: AdmissionLimits,
    ) -> None:
        self._policy = policy
        self._token = token
        self._inbox = inbox
        self._limits = limits
        self._request_slots = BoundedSemaphore(limits.max_items)

    def handle_body(self, *, authorization: str | None, body: bytes) -> tuple[int, str]:
        """Pure request handling: ``(status_code, reason)``. No socket, no I/O.

        Fail-closed at every step: a bad token → 401, a junk/empty body → 400,
        a disallowed sender → 403, an oversized body → 413, and unavailable
        admission → 503. Only a permitted, well-formed delivery with a reservation
        returns 202.
        """
        if authorization != f"Bearer {self._token}":
            return 401, "unauthorized"
        try:
            raw = json.loads(body) if body else None
        except json.JSONDecodeError:
            return 400, "invalid json"
        message = ChannelMessage.from_payload(raw)
        if message is None:
            return 400, "malformed message"
        if not self._policy.permits_sender(message.sender):
            return 403, "sender not allowed"
        return self._admit(message, len(body))

    def _admit(self, message: ChannelMessage, size_bytes: int) -> tuple[int, str]:
        """Reserve before scheduling, translating admission outcomes to HTTP."""
        admission = self._inbox.offer(message, size_bytes=size_bytes)
        if admission in (Admission.ACCEPTED, Admission.COALESCED):
            return 202, "accepted"
        if admission is Admission.TOO_LARGE:
            return 413, "message too large"
        return 503, "channel unavailable"

    def acquire_request(self, *, content_length: str | None) -> tuple[int, str] | None:
        """Reserve a request slot and reject declared oversized bodies before read."""
        try:
            size_bytes = int(content_length or 0)
        except ValueError:
            return 400, "invalid content length"
        if size_bytes < 0:
            return 400, "invalid content length"
        if size_bytes > self._limits.max_bytes:
            return 413, "message too large"
        if not self._request_slots.acquire(blocking=False):
            return 503, "channel unavailable"
        return None

    def release_request(self) -> None:
        """Release the request reservation after its body was handled or dropped."""
        self._request_slots.release()

    def serve(self, port: int) -> tuple[HTTPServer, int]:
        """Bind a loopback HTTP receiver with bounded body admission.

        Bound to ``127.0.0.1`` only (never a routable interface — a channel into
        a running agent is a same-host trust boundary). Each request's declared
        length is refused before reading, and synchronous handling avoids spawning
        an unbounded thread per connection.
        """
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            # A HALF-SENT REQUEST MUST COST ITS OWN CONNECTION, NEVER THE
            # CHANNEL. Handling is single-threaded on purpose (a channel into a
            # running agent must not spawn a thread per connection), so a client
            # that declares an in-cap Content-Length and then stops sending
            # blocks `rfile.read` and every later delivery queues behind it
            # forever. The length cap bounds how much may be read; this bounds
            # how long anyone may take to send it, which is the half that makes
            # "synchronous handling" safe rather than a way for any local
            # process to silence the channel by opening a socket.
            timeout = _REQUEST_READ_TIMEOUT_SECONDS

            def do_POST(self) -> None:  # BaseHTTPRequestHandler API name
                if self.path != RECEIVE_ROUTE:
                    self.send_error(404, "not found")
                    return
                refusal = receiver.acquire_request(
                    content_length=self.headers.get("Content-Length")
                )
                if refusal is not None:
                    self._respond(*refusal)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(length) if length else b""
                    self._respond(
                        *receiver.handle_body(
                            authorization=self.headers.get("Authorization"), body=body
                        )
                    )
                finally:
                    receiver.release_request()

            def _respond(self, status: int, reason: str) -> None:
                self.send_response(status)
                self.end_headers()
                self.wfile.write(reason.encode("utf-8"))

            def log_message(self, *_args: Any) -> None:
                # Silence the default stderr access log — this shares stdio with
                # nothing, but a channel server should be quiet by default.
                return

        server = HTTPServer(("127.0.0.1", port), _Handler)
        return server, server.server_address[1]


# ─── the channel server (thin, lazy SDK transport over the pure layer) ──────────


class ChannelServer:
    """The stdio MCP channel server Claude Code spawns from the ``--channels`` file.

    Composes the pure layer (``ChannelMessage`` / ``ChannelPolicy`` /
    ``ChannelReceiver``) with the MCP SDK transport. The SDK is imported lazily
    inside :meth:`run` (the ``[mcp]`` extra may be absent), and the
    ``claude/channel/*`` methods — a research-preview protocol with no typed
    shapes — are narrowed at that boundary. Everything testable lives on the pure
    layer this only wires together.
    """

    def __init__(
        self,
        config: ChannelsConfig,
        *,
        port: int = 0,
        admission_limits: AdmissionLimits | None = None,
    ) -> None:
        self._policy = ChannelPolicy(tuple(config.allowed_senders))
        self._port = port
        self._admission_limits = admission_limits or config.admission
        self._token = ensure_channel_token()

    @property
    def policy(self) -> ChannelPolicy:
        """The security seam — the test hook and the receiver's decision source."""
        return self._policy

    def capabilities(self) -> dict[str, Any]:
        """The experimental capability block advertised at initialize."""
        return {CAPABILITY_KEY: {}}

    def publish_endpoint(self, port: int) -> ChannelEndpoint:
        """Persist the bound receiver's ``{port, token}`` for the daemon to read.

        Best-effort: an unwritable dir logs and returns the record anyway (the
        daemon simply can't discover it — the agent side still works, only the
        inbound-delivery path is dark). Mirrors the hook settings write.
        """
        endpoint = ChannelEndpoint(port=port, token=self._token)
        path = channel_endpoint_path()
        try:
            paths.ensure_dir(path.parent)
            path.write_text(json.dumps(endpoint.to_json()), encoding="utf-8")
            path.chmod(0o600)
        except OSError as exc:
            logger.debug("could not publish channel endpoint: {}", exc)
        return endpoint

    # The live MCP lifecycle keeps setup, admission, and teardown in one owner.
    def run(self) -> None:  # noqa: PLR0915  # pragma: no cover - live stdio transport
        """Serve the channel over stdio until the agent disconnects (blocking).

        Lazily builds the low-level MCP server, waits until its session exists,
        then starts the loopback receiver on a daemon thread and bridges each
        admitted delivery into an outbound ``notifications/claude/channel``.
        Reservations are completed after send and shutdown accounts for pending
        values. Not unit-tested (a live stdio handshake + real SDK, same as
        ``GroveMcpServer.run``); the pieces it composes are.
        """
        import threading  # noqa: PLC0415

        server, session_holder = self._build_server()
        loop = asyncio.new_event_loop()
        inbox = BoundedInbox[ChannelMessage](self._admission_limits)
        ledger = ChannelDeliveryLedger(capacity=self._admission_limits.max_items)
        http_server: HTTPServer | None = None

        def _account_undelivered() -> None:
            for delivery in inbox.close():
                ledger.record(delivery.value, ChannelDeliveryStatus.UNDLVRD_PENDING)

        async def _pump() -> None:
            while True:
                delivery = await inbox.take()
                try:
                    session = session_holder["session"]
                    await session.send_notification(
                        DELIVER_NOTIFICATION, delivery.value.to_notification_params()
                    )
                except asyncio.CancelledError:
                    ledger.record(delivery.value, ChannelDeliveryStatus.UNDLVRD_IN_FLIGHT)
                    raise
                except Exception as exc:  # best-effort delivery, never crash the pump
                    logger.debug("channel delivery failed: {}", exc)
                    ledger.record(delivery.value, ChannelDeliveryStatus.FAILED)
                else:
                    ledger.record(delivery.value, ChannelDeliveryStatus.SENT)
                finally:
                    inbox.complete(delivery)

        async def _serve() -> None:
            nonlocal http_server
            inbox.bind()
            session_ready = asyncio.Event()
            stdio = asyncio.create_task(self._serve_stdio(server, session_holder, session_ready))
            ready = asyncio.create_task(session_ready.wait())
            done, _ = await asyncio.wait((stdio, ready), return_when=asyncio.FIRST_COMPLETED)
            if stdio in done:
                ready.cancel()
                with suppress(asyncio.CancelledError):
                    await ready
                try:
                    await stdio
                finally:
                    _account_undelivered()
                return
            ready.result()
            receiver = ChannelReceiver(self._policy, self._token, inbox, self._admission_limits)
            http_server, bound_port = receiver.serve(self._port)
            self.publish_endpoint(bound_port)
            threading.Thread(target=http_server.serve_forever, daemon=True).start()
            # Held for the serve lifetime so the task is not GC'd mid-run (RUF006).
            pump = asyncio.create_task(_pump())
            try:
                await stdio
            finally:
                _account_undelivered()
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
                _account_undelivered()

        try:
            loop.run_until_complete(_serve())
        finally:
            _account_undelivered()
            if http_server is not None:
                http_server.shutdown()
            loop.close()

    def _build_server(self) -> tuple[Any, dict[str, Any]]:  # pragma: no cover
        """Build the low-level MCP ``Server`` with the channel wiring.

        Returns the server plus a mutable holder the running session is stashed
        in (the outbound pump reads it). All SDK types are ``Any`` — the
        ``claude/channel/*`` methods have no typed shapes and this is the single
        narrow boundary.
        """
        (Server,) = _SDK.load("mcp.server.lowlevel", "Server")
        TextContent, Tool = _SDK.load("mcp.types", "TextContent", "Tool")

        server: Any = Server("grove-channel")
        holder: dict[str, Any] = {}

        @server.list_tools()  # type: ignore[untyped-decorator]  # dynamic SDK decorator (server: Any)
        async def _list_tools() -> list[Any]:
            return [
                Tool(
                    name=REPLY_TOOL,
                    description="Reply to Grove from within a channel-connected session.",
                    inputSchema={
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                        "required": ["content"],
                    },
                )
            ]

        @server.call_tool()  # type: ignore[untyped-decorator]  # dynamic SDK decorator (server: Any)
        async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
            # The reply rides back to Grove over the same loopback trust
            # boundary; recording it is the daemon-plumbing TODO. Acknowledge so
            # the agent's tool call completes.
            content = arguments.get("content", "") if name == REPLY_TOOL else ""
            logger.debug("channel reply received ({} chars)", len(content))
            return [TextContent(type="text", text="delivered")]

        # The permission request is a custom method with no typed handler; register
        # it directly on the low-level dispatch table, narrowed at this boundary.
        self._register_permission(server)
        return server, holder

    def _register_permission(self, server: Any) -> None:  # pragma: no cover
        """Wire the ``claude/channel/permission`` request to the policy's relay."""
        policy = self._policy

        async def _handle(request: Any) -> Any:
            params = getattr(request, "params", None)
            payload = params if isinstance(params, dict) else {}
            return policy.resolve_permission(payload).to_result()

        # The low-level Server keys handlers by request type; a research-preview
        # method with no type slots into the raw registry under its method name.
        try:
            server.request_handlers[PERMISSION_REQUEST] = _handle
        except (AttributeError, TypeError) as exc:
            logger.debug("could not register channel permission handler: {}", exc)

    async def _serve_stdio(
        self, server: Any, holder: dict[str, Any], ready: asyncio.Event
    ) -> None:  # pragma: no cover
        """Run the server over stdio, stashing the live session before admitting work."""
        from mcp.server.stdio import stdio_server  # noqa: PLC0415

        init_options = server.create_initialization_options()
        # Advertise the experimental capability on the negotiated options.
        experimental = getattr(init_options.capabilities, "experimental", None)
        if isinstance(experimental, dict):
            experimental.update(self.capabilities())

        async with (
            stdio_server() as (read_stream, write_stream),
            server.run(read_stream, write_stream, init_options) as session,
        ):
            holder["session"] = session
            ready.set()
            await session.wait_closed()


# ─── same-host token (mirrors ClaudeHook.ensure_ingest_token) ───────────────────

_TOKEN_FILENAME: Final = "channel.token"


def ensure_channel_token() -> str:
    """Get-or-create the same-host secret the receiver and daemon both trust.

    Not the pairing bearer every other daemon route uses (that needs a human to
    approve): a channel delivery fires with nobody watching, so a persisted
    same-host secret gates it instead — identical discipline to
    ``ClaudeHook.ensure_ingest_token``. An unwritable dir returns a fresh
    in-memory token (the delivery simply won't authenticate), never raising.
    """
    path = channel_settings_path().parent / _TOKEN_FILENAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        paths.ensure_dir(path.parent)
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        logger.debug("could not persist channel token: {}", exc)
    return token


def main() -> int:  # pragma: no cover - process entry
    """Console/`python -m grove.core.channel` entry: spawn and run the server.

    Reads the global Grove config for the channel policy (the allowlist) and the
    optional fixed port from the environment, then serves over stdio. Returns a
    process exit code; a missing MCP SDK is a clean hint + non-zero exit, never a
    chained traceback the agent buries.
    """
    config = load_config(repo_root=None).channels
    port = _env_port(os.environ.get(ENV_PORT))
    try:
        ChannelServer(config, port=port).run()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _env_port(raw: str | None) -> int:
    """Parse ``GROVE_CHANNEL_PORT``; 0 (ephemeral) on unset/invalid."""
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CAPABILITY_KEY",
    "ChannelDeliveryLedger",
    "ChannelDeliveryStatus",
    "ChannelEndpoint",
    "ChannelMessage",
    "ChannelPolicy",
    "ChannelReceiver",
    "ChannelServer",
    "PermissionDecision",
    "channel_endpoint_path",
    "channel_settings",
    "channel_settings_path",
    "ensure_channel_token",
    "main",
]
