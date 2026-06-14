"""The Grove MCP server — stdio transport, env-driven config, FastMCP wiring.

Boundary (issues #1/#6): MCP client → this server → ``GroveClient`` →
Grove daemon REST → core. Nothing here imports engine internals; the
daemon is the single authority, so this process can run anywhere it can
reach one — same host by default, remote with ``GROVE_API_TOKEN``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from grove.client import BackendConfig, GroveClient
from grove.mcp.tools import GroveTools

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

# The MCP SDK ships behind the opt-in `[mcp]` extra, but `grove-mcp` is an
# unconditional console script — a `.[daemon]`-only host has it on PATH. The
# import is deferred to construction time so `main()` is reachable to turn a
# missing SDK into a clean hint + non-zero exit instead of an import traceback.
_MISSING_SDK_HINT = "grove-mcp requires the MCP SDK — install with: pip install 'grove[mcp]'"


def _load_fastmcp() -> type[FastMCP]:
    """Import FastMCP, re-raising the install hint if the `[mcp]` extra is absent."""
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_MISSING_SDK_HINT) from exc
    return FastMCP


_INSTRUCTIONS = (
    "Grove manages isolated coding-agent workspaces: each is a git worktree "
    "plus a tmux session running an agent. Use grove_list_workspaces to see "
    "the fleet, grove_create_workspace to start work, grove_peek_workspace "
    "for a bounded status snapshot, and the pause/resume/respawn/kill tools "
    "for lifecycle. Destructive operations require explicit inputs."
)


@dataclass(slots=True, frozen=True)
class McpServerConfig:
    """Resolved server configuration — env in, immutable value out.

    Resolution order per field: CLI flag → environment → built-in default
    (the config-cascade rule applied to a two-knob surface). The token is
    optional on purpose: without one, ``GroveClient`` mints a session from
    the same-host ``auth.json``, so a co-located daemon needs zero setup.
    """

    DEFAULT_API_URL: ClassVar[str] = "http://127.0.0.1:7421"

    api_url: str
    api_token: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str], *, api_url: str | None = None) -> McpServerConfig:
        return cls(
            api_url=api_url or environ.get("GROVE_API_URL") or cls.DEFAULT_API_URL,
            # `or None` collapses an empty-string env var to "unset" so it
            # falls back to the local mint instead of sending "Bearer ".
            api_token=environ.get("GROVE_API_TOKEN") or None,
        )


class GroveMcpServer:
    """One stdio MCP server over one ``GroveClient``.

    Owns the wiring only: client lifecycle via the FastMCP lifespan, tool
    registration with the ``grove_``-prefixed names that are the published
    contract. All behavior lives in ``GroveTools``; tests target that class
    directly and use :attr:`fastmcp` to pin the registered surface.
    """

    def __init__(self, config: McpServerConfig, *, client: GroveClient | None = None) -> None:
        self._client = client or GroveClient(
            BackendConfig(
                label="mcp",
                daemon_url=config.api_url,
                daemon_token=config.api_token,
            )
        )
        self._tools = GroveTools(self._client)
        fastmcp = _load_fastmcp()
        self._mcp: FastMCP = fastmcp("grove", instructions=_INSTRUCTIONS, lifespan=self._lifespan)
        self._register_tools()

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
        # Tool names are the published contract (issue #6's MVP table) —
        # renaming one breaks every configured MCP client.
        t = self._tools
        for fn, name in (
            (t.list_workspaces, "grove_list_workspaces"),
            (t.get_workspace, "grove_get_workspace"),
            (t.create_workspace, "grove_create_workspace"),
            (t.peek_workspace, "grove_peek_workspace"),
            (t.pause_workspace, "grove_pause_workspace"),
            (t.resume_workspace, "grove_resume_workspace"),
            (t.respawn_workspace, "grove_respawn_workspace"),
            (t.kill_workspace, "grove_kill_workspace"),
            (t.attach_instruction, "grove_attach_instruction"),
            (t.send_workspace_message, "grove_send_workspace_message"),
        ):
            self._mcp.add_tool(fn, name=name)

    def run(self) -> None:
        """Serve MCP over stdio until the client disconnects (blocking)."""
        self._mcp.run("stdio")


def main(argv: Sequence[str] | None = None) -> None:
    """Console entry point for ``grove-mcp`` / ``python -m grove.mcp``."""
    parser = argparse.ArgumentParser(
        prog="grove-mcp",
        description=(
            "Grove MCP server (stdio). Connects to a running Grove daemon; "
            "configure via --api-url / GROVE_API_URL and GROVE_API_TOKEN."
        ),
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            f"Grove daemon base URL (default: $GROVE_API_URL or {McpServerConfig.DEFAULT_API_URL})"
        ),
    )
    args = parser.parse_args(argv)
    config = McpServerConfig.from_env(os.environ, api_url=args.api_url)
    try:
        server = GroveMcpServer(config)
    except ImportError as exc:
        # A `.[daemon]`-only host has `grove-mcp` on PATH but no MCP SDK; fail
        # with the install hint, not a chained traceback the MCP client buries.
        print(str(exc) or _MISSING_SDK_HINT, file=sys.stderr)
        sys.exit(1)
    server.run()


__all__ = ["GroveMcpServer", "McpServerConfig", "main"]
