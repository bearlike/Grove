"""The Grove MCP server — stdio or networked HTTP, env-driven config, FastMCP wiring.

Boundary: MCP client → this server → ``GroveClient`` →
Grove daemon REST → core. Nothing here imports engine internals; the
daemon is the single authority, so this process can run anywhere it can
reach one — same host by default, remote with ``GROVE_API_TOKEN``.

Two transports, two very different trust models:

* ``stdio`` — the client *spawns* this process, so the OS process boundary
  IS the authentication. No inbound token exists or is needed.
* ``streamable-http`` / ``sse`` — this process binds a socket, so it becomes
  the network edge and must authenticate callers itself. Grove's daemon
  stays loopback-only (see ``daemon/CLAUDE.md``); THIS is the process that
  is allowed to face a network, and it fails closed without a token.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from grove._mcp_sdk import McpSdk
from grove.client import BackendConfig, GroveClient
from grove.mcp.instructions import SERVER_INSTRUCTIONS
from grove.mcp.tools import GroveTools

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken
    from mcp.server.fastmcp import FastMCP

# The SDK's own transport names, used verbatim so a Grove flag value and an
# MCP client's `"type"` read the same. Note `streamable-http` here is spelled
# `http` by Claude Code's `--transport` flag; both mean this transport.
#
# The SDK also offers `sse`, deliberately NOT exposed: it is deprecated in the
# MCP spec in favour of Streamable HTTP, and it takes its mount path from a
# different setting (`sse_path`), so carrying it would mean one `--path` flag
# with two meanings. Adding it back later is a one-line change.
McpTransport = Literal["stdio", "streamable-http"]

# The MCP SDK ships behind the opt-in `[mcp]` extra, but `grove-mcp` is an
# unconditional console script — a `.[daemon]`-only host has it on PATH. The
# import is deferred to construction time so `main()` is reachable to turn a
# missing SDK into a clean hint + non-zero exit instead of an import traceback.
# `McpSdk` is what makes that hint honest: an SDK that is installed but has
# moved `mcp.server.fastmcp` reports its version and the supported range
# instead of telling an operator to install what they already have.
_SDK = McpSdk("grove-mcp")


def _load_fastmcp() -> type[FastMCP]:
    """Import FastMCP, or raise the diagnosed absent-vs-incompatible ``ImportError``."""
    (fastmcp,) = _SDK.load("mcp.server.fastmcp", "FastMCP")
    return cast("type[FastMCP]", fastmcp)


@dataclass(slots=True, frozen=True)
class McpServerConfig:
    """Resolved server configuration — env in, immutable value out.

    Resolution order per field: CLI flag → environment → built-in default.
    This deliberately does NOT read the six-layer ``GroveConfig`` cascade:
    the import-linter contract forbids ``grove.mcp`` from importing
    ``grove.core.config`` (the daemon is the only engine authority), so env
    IS this package's cascade. Keep new knobs on that same pattern.

    ``api_token`` (OUTBOUND, this server → daemon) is optional: without one
    ``GroveClient`` mints a session from the same-host ``auth.json``, so a
    co-located daemon needs zero setup. ``auth_token`` (INBOUND, caller →
    this server) is a different credential in the opposite direction and is
    mandatory for a network transport — see :meth:`validate`.
    """

    DEFAULT_API_URL: ClassVar[str] = "http://127.0.0.1:7421"
    # Deliberately not the SDK's 8000: adjacent to the daemon's 7421 so the
    # two Grove ports read as a pair, and unlikely to collide with a dev server.
    DEFAULT_PORT: ClassVar[int] = 7431
    DEFAULT_BIND_HOST: ClassVar[str] = "127.0.0.1"
    DEFAULT_PATH: ClassVar[str] = "/mcp"

    api_url: str
    api_token: str | None
    transport: McpTransport = "stdio"
    host: str = DEFAULT_BIND_HOST
    port: int = DEFAULT_PORT
    path: str = DEFAULT_PATH
    auth_token: str | None = None
    read_only: bool = False
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    @property
    def is_networked(self) -> bool:
        """True when this config binds a socket rather than owning stdio."""
        return self.transport != "stdio"

    # Env values arrive as strings; these narrow them to the field types.
    # They live on the class (not as module-level helpers) because they are
    # part of how this value object resolves itself, per the codebase's
    # "state and the methods over it live together" rule.

    @staticmethod
    def _as_transport(raw: str | None) -> McpTransport | None:
        if not raw:
            return None
        # `http` is what Claude Code calls this transport; accept the alias so
        # a value copied from an MCP client's config just works.
        value = "streamable-http" if raw == "http" else raw
        if value not in ("stdio", "streamable-http"):
            raise ValueError(
                f"unknown MCP transport {raw!r} — expected stdio or streamable-http (http)"
            )
        return value  # type: ignore[return-value]

    @staticmethod
    def _as_port(raw: str | None) -> int | None:
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"GROVE_MCP_PORT must be an integer, got {raw!r}") from exc

    @staticmethod
    def _as_bool(raw: str | None) -> bool:
        return (raw or "").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _as_csv(raw: str | None) -> tuple[str, ...]:
        return tuple(item.strip() for item in (raw or "").split(",") if item.strip())

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        *,
        api_url: str | None = None,
        transport: McpTransport | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
        read_only: bool = False,
    ) -> McpServerConfig:
        return cls(
            api_url=api_url or environ.get("GROVE_API_URL") or cls.DEFAULT_API_URL,
            # `or None` collapses an empty-string env var to "unset" so it
            # falls back to the local mint instead of sending "Bearer ".
            api_token=environ.get("GROVE_API_TOKEN") or None,
            transport=transport or cls._as_transport(environ.get("GROVE_MCP_TRANSPORT")) or "stdio",
            host=host or environ.get("GROVE_MCP_HOST") or cls.DEFAULT_BIND_HOST,
            # `is not None` rather than `or`: an explicit `--port 0` must reach
            # validate() and be rejected, not silently become the default. (The
            # daemon uses port 0 for an OS-assigned port, but that only works
            # because it has --print-port to report the result; a server clients
            # must be pointed at has no such escape.)
            port=port
            if port is not None
            else (cls._as_port(environ.get("GROVE_MCP_PORT")) or cls.DEFAULT_PORT),
            path=path or environ.get("GROVE_MCP_PATH") or cls.DEFAULT_PATH,
            auth_token=environ.get("GROVE_MCP_TOKEN") or None,
            # A CLI --read-only can only ever tighten: an operator who set the
            # env var must not have it silently loosened by omitting the flag.
            read_only=read_only or cls._as_bool(environ.get("GROVE_MCP_READ_ONLY")),
            allowed_hosts=cls._as_csv(environ.get("GROVE_MCP_ALLOWED_HOSTS")),
            allowed_origins=cls._as_csv(environ.get("GROVE_MCP_ALLOWED_ORIGINS")),
        )

    def validate(self) -> None:
        """Fail closed before a socket is ever bound.

        A network transport with no inbound token would publish full
        workspace lifecycle control — including ``grove_kill_workspace`` —
        to anyone who can reach the port. There is deliberately no
        ``--insecure`` escape hatch: setting a token is one env var, and an
        opt-out flag is the kind of thing that survives into production.
        """
        if not self.is_networked:
            return
        if not self.auth_token:
            raise ValueError(
                f"--transport {self.transport} binds a network socket and requires an "
                "inbound token: set GROVE_MCP_TOKEN (any high-entropy string, e.g. "
                "`openssl rand -hex 32`). Callers then send `Authorization: Bearer <token>`."
            )
        if not 1 <= self.port <= 65535:
            raise ValueError(f"--port must be between 1 and 65535, got {self.port}")


@dataclass(slots=True, frozen=True)
class SharedSecretVerifier:
    """Inbound bearer check against one shared secret.

    Structurally satisfies the SDK's ``TokenVerifier`` protocol without
    inheriting from it — the SDK is an optional dependency, so no name from
    it may appear at class-definition time (see :func:`_load_fastmcp`).

    Why a static secret rather than Grove's ``SessionStore`` pairing flow:
    the import-linter contract forbids ``grove.mcp`` from importing
    ``grove.core.auth``, and pairing needs a human to approve at a TUI. A
    networked MCP server starts unattended, which is the same problem the
    daemon's hook-ingest token solves the same way (``daemon/auth.py``).
    """

    token: str = field(repr=False)

    async def verify_token(self, token: str) -> AccessToken | None:
        from mcp.server.auth.provider import AccessToken  # noqa: PLC0415

        # compare_digest, never `==`: a short-circuiting comparison leaks the
        # length of the shared prefix through timing.
        if not secrets.compare_digest(token, self.token):
            return None
        # Empty scopes on purpose. Grove's own sessions carry no scopes either
        # (every authenticated caller has full authority), so minting fictional
        # ones here would imply an authorization model that does not exist.
        # Read-only exposure is enforced by which tools are REGISTERED instead.
        return AccessToken(token=token, client_id="grove-mcp", scopes=[])


class GroveMcpServer:
    """One MCP server — stdio or networked HTTP — over one ``GroveClient``.

    Owns the wiring only: client lifecycle via the FastMCP lifespan, tool
    registration with the ``grove_``-prefixed names that are the published
    contract. All behavior lives in ``GroveTools``; tests target that class
    directly and use :attr:`fastmcp` to pin the registered surface.
    """

    def __init__(self, config: McpServerConfig, *, client: GroveClient | None = None) -> None:
        config.validate()
        self._config = config
        self._client = client or GroveClient(
            BackendConfig(
                label="mcp",
                daemon_url=config.api_url,
                daemon_token=config.api_token,
            )
        )
        self._tools = GroveTools(self._client)
        fastmcp = _load_fastmcp()
        self._mcp: FastMCP = fastmcp(
            "grove",
            instructions=SERVER_INSTRUCTIONS,
            lifespan=self._lifespan,
            **self._network_kwargs(config),
        )
        self._register_tools()

    @staticmethod
    def _network_kwargs(config: McpServerConfig) -> dict[str, Any]:
        """FastMCP constructor kwargs for a bound socket — empty under stdio.

        Host/port/path are constructor *settings* in this SDK, not arguments
        to ``run()`` (verified against the locked 1.27.2), so they must be
        decided here rather than at serve time.

        ``Any`` is the honest type for a heterogeneous kwargs bag aimed at an
        external constructor; every value is checked by FastMCP's own signature
        at the call site, which is the boundary where it belongs.
        """
        if not config.is_networked:
            return {}

        from mcp.server.auth.settings import AuthSettings  # noqa: PLC0415
        from mcp.server.transport_security import TransportSecuritySettings  # noqa: PLC0415

        assert config.auth_token is not None  # guaranteed by config.validate()
        return {
            "host": config.host,
            "port": config.port,
            "streamable_http_path": config.path,
            "token_verifier": SharedSecretVerifier(config.auth_token),
            # BOTH `auth` and `token_verifier` are required to get a guarded
            # route: the SDK gates AuthenticationMiddleware behind `auth` but
            # wraps the route in RequireAuthMiddleware behind `token_verifier`.
            # Passing only the verifier yields a route that 401s everything
            # because no backend ever populates the auth scope.
            #
            # `resource_server_url=None` is load-bearing: it suppresses the
            # RFC 9728 protected-resource metadata routes. We are not an OAuth
            # resource server, and advertising a nonexistent authorization
            # server would invite a spec-compliant client into a doomed OAuth
            # flow instead of using the static bearer it was configured with.
            # `issuer_url` is required by the model but inert while both
            # `resource_server_url` and `auth_server_provider` are absent.
            "auth": AuthSettings(
                issuer_url=f"http://{config.host}:{config.port}",
                resource_server_url=None,
                required_scopes=[],
            ),
            # DNS-rebinding protection defaults to OFF in the SDK when unset.
            # Enabling it with an empty allowlist 421s every request, so it is
            # opt-in via config: the operator names the hostnames clients use.
            "transport_security": TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(config.allowed_hosts),
                allowed_origins=list(config.allowed_origins),
            )
            if config.allowed_hosts or config.allowed_origins
            else None,
        }

    @property
    def fastmcp(self) -> FastMCP:
        """The underlying FastMCP app — the test seam, and the hook for
        embedding this server under another transport later."""
        return self._mcp

    @asynccontextmanager
    async def _lifespan(self, _server: FastMCP) -> AsyncIterator[None]:
        # Connect once for the whole stdio session instead of per tool call:
        # token resolution (the local mint writes auth.json) is not free.
        await self._client.connect()
        try:
            yield
        finally:
            await self._client.close()

    def _register_tools(self) -> None:
        # Tool names are the published contract — renaming one breaks every
        # configured MCP client. The `mutates` flag
        # is the read-only scope: withholding a tool is the only honest way to
        # deny it, since a registered tool an agent can see it will try to call.
        t = self._tools
        for fn, name, mutates in (
            (t.list_projects, "grove_list_projects", False),
            (t.list_workspaces, "grove_list_workspaces", False),
            (t.get_workspace, "grove_get_workspace", False),
            (t.list_agents, "grove_list_agents", False),
            (t.list_sessions, "grove_list_sessions", False),
            (t.peek_workspace, "grove_peek_workspace", False),
            (t.get_fleet_status, "grove_get_fleet_status", False),
            (t.get_workspace_phase, "grove_get_workspace_phase", False),
            (t.get_workspace_todo, "grove_get_workspace_todo", False),
            (t.attach_instruction, "grove_attach_instruction", False),
            (t.create_workspace, "grove_create_workspace", True),
            (t.pause_workspace, "grove_pause_workspace", True),
            (t.resume_workspace, "grove_resume_workspace", True),
            (t.respawn_workspace, "grove_respawn_workspace", True),
            (t.kill_workspace, "grove_kill_workspace", True),
            (t.send_workspace_message, "grove_send_workspace_message", True),
            (t.remap_workspace_session, "grove_remap_workspace_session", True),
            (t.attach_ticket, "grove_attach_ticket", True),
            (t.detach_ticket, "grove_detach_ticket", True),
            (t.set_workspace_phase, "grove_set_workspace_phase", True),
        ):
            if mutates and self._config.read_only:
                continue
            self._mcp.add_tool(fn, name=name)

    def run(self) -> None:
        """Serve MCP until the client disconnects or the process is stopped.

        Blocking. Under stdio this ends when the spawning client closes the
        pipe; under a network transport it runs until terminated.
        """
        self._mcp.run(self._config.transport)


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point for ``grove-mcp`` / ``python -m grove.mcp``."""
    parser = argparse.ArgumentParser(
        prog="grove-mcp",
        description=(
            "Grove MCP server. Connects to a running Grove daemon; configure via "
            "--api-url / GROVE_API_URL and GROVE_API_TOKEN. Serves stdio by default; "
            "--transport streamable-http binds a port for remote agent harnesses."
        ),
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            f"Grove daemon base URL (default: $GROVE_API_URL or {McpServerConfig.DEFAULT_API_URL})"
        ),
    )
    parser.add_argument(
        "--transport",
        default=None,
        choices=("stdio", "streamable-http", "http"),
        help=(
            "MCP transport; 'http' is an alias for streamable-http "
            "(default: $GROVE_MCP_TRANSPORT or stdio)"
        ),
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Interface to bind for a network transport (default: "
            f"$GROVE_MCP_HOST or {McpServerConfig.DEFAULT_BIND_HOST}). Use 0.0.0.0 to accept "
            "off-host clients — only behind a tunnel, VPN, or TLS-terminating proxy."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to bind (default: $GROVE_MCP_PORT or {McpServerConfig.DEFAULT_PORT})",
    )
    parser.add_argument(
        "--path",
        default=None,
        help=f"HTTP mount path (default: $GROVE_MCP_PATH or {McpServerConfig.DEFAULT_PATH})",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "Register only non-mutating tools, hiding create/kill/pause/resume/"
            "respawn/message/remap. Also settable via GROVE_MCP_READ_ONLY."
        ),
    )
    args = parser.parse_args(argv)
    try:
        config = McpServerConfig.from_env(
            os.environ,
            api_url=args.api_url,
            transport=McpServerConfig._as_transport(args.transport),
            host=args.host,
            port=args.port,
            path=args.path,
            read_only=args.read_only,
        )
        server = GroveMcpServer(config)
    except ImportError as exc:
        # A `.[daemon]`-only host has `grove-mcp` on PATH but no MCP SDK; fail
        # with the install hint, not a chained traceback the MCP client buries.
        print(str(exc) or _SDK.diagnose(exc), file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        # Bad transport name, unparseable port, or the fail-closed missing-token
        # check. Same treatment: one actionable line, never a traceback.
        print(f"grove-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    if config.is_networked:
        # stdout belongs to the JSON-RPC stream under stdio; under HTTP it is
        # free, but stderr keeps the two paths' logging identical either way.
        scope = "read-only" if config.read_only else "full-control"
        print(
            f"grove-mcp: serving {config.transport} ({scope}) on "
            f"http://{config.host}:{config.port}{config.path} → daemon {config.api_url}",
            file=sys.stderr,
        )
    server.run()


__all__ = ["GroveMcpServer", "McpServerConfig", "McpTransport", "SharedSecretVerifier", "main"]
