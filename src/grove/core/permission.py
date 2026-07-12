"""Grove permission-prompt tool — answer Claude Code permission prompts natively (#172).

A *permission prompt* is Claude Code's "allow this tool call?" gate. In an
interactive terminal a human answers it; a headless / paneless session has no TTY
to answer at, so it would block forever. Claude Code's ``--permission-prompt-tool
<mcp_tool>`` seam redirects that gate to an MCP tool that returns an allow/deny
decision as JSON — this module *is* that tool, hosted by Grove.

This is the native replacement for typing a permission answer into the tmux pane
(``tmux.send_keys``), which only works with a human attached. It is the third
launch-composed native channel alongside the status **hook** (#171) and the
**channel** server (#182): all three are opt-in, ``claude_code``-only, composed
at launch in ``WorkspaceManager._compose_launch``, and never touch the user's own
``.claude`` config.

Wiring (all behind ``cfg.permission.enabled``, default off):

1. ``_compose_launch`` appends ``--mcp-config <grove-permission-mcp>`` (registers
   this server) + ``--permission-prompt-tool mcp__grove_permission__permission_prompt``
   (points Claude Code's gate at its tool). Disabled ⇒ no flags ⇒ no-op.
2. This module is the stdio MCP server Claude Code spawns from that config
   (``python -m grove.core.permission``). It exposes one tool that decides
   allow/deny and returns the decision JSON.

Side-effect discipline mirrors ``grove.core.channel``: the MCP SDK ships behind
the opt-in ``[mcp]`` extra, so every SDK import is **lazy** inside :meth:`run` —
the module imports and its pure policy layer tests without the SDK present.
Dependencies flow inward — this imports ``config`` / ``paths``, never the manager.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final

from grove.core import paths
from grove.core.config import PermissionConfig, load_config

# The MCP-server name Grove registers this tool under and the tool's own name.
# The ``--permission-prompt-tool`` value is the MCP-qualified reference
# ``mcp__<server>__<tool>`` Claude Code resolves the tool by; constants (not
# scattered literals) so a rename is one edit and the launch flag can't drift
# from the registration.
PERMISSION_MCP_SERVER: Final = "grove_permission"
PERMISSION_TOOL_NAME: Final = "permission_prompt"

_MCP_CONFIG_FILENAME: Final = "grove-permission-mcp.json"


def permission_tool_ref() -> str:
    """The ``--permission-prompt-tool`` value: the MCP-qualified tool reference."""
    return f"mcp__{PERMISSION_MCP_SERVER}__{PERMISSION_TOOL_NAME}"


# ─── pure decision layer (fully typed, SDK-free, unit-tested) ────────────────


@dataclass(slots=True, frozen=True)
class PermissionRequest:
    """One permission prompt Claude Code hands the tool: which tool, what input.

    ``tool_name`` / ``tool_input`` are the call the agent wants permission for;
    ``tool_use_id`` correlates the gate to the pending assistant tool_use (may be
    absent on an older CLI). Shape only — the policy never interprets the input's
    semantics (the provider boundary), it decides allow/deny on Grove policy.
    """

    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str | None = None

    @classmethod
    def from_arguments(cls, raw: object) -> PermissionRequest | None:
        """Parse the tool-call arguments Claude Code passes; ``None`` on junk.

        Best-effort like every parse at a provider boundary — a malformed prompt
        is not answered ``allow`` by accident, the caller maps ``None`` to a
        fail-closed deny.
        """
        if not isinstance(raw, dict):
            return None
        tool_name = raw.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return None
        tool_input = raw.get("tool_input")
        tool_use_id = raw.get("tool_use_id")
        return cls(
            tool_name=tool_name,
            tool_input=tool_input if isinstance(tool_input, dict) else {},
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None,
        )


@dataclass(slots=True, frozen=True)
class PermissionOutcome:
    """The allow/deny decision, serialized to Claude Code's expected result JSON.

    ``--permission-prompt-tool`` expects the tool's text result to be a JSON blob
    of ``{"behavior": "allow", "updatedInput": {...}}`` or
    ``{"behavior": "deny", "message": "..."}``. On allow, ``updated_input`` is the
    (unmodified) tool input echoed back — the seam a future input-rewrite could
    use, forwarded verbatim today. On deny, ``message`` is the human-readable
    reason surfaced to the agent.
    """

    ALLOW: ClassVar[str] = "allow"
    DENY: ClassVar[str] = "deny"

    behavior: str
    message: str | None = None
    updated_input: dict[str, Any] | None = None

    def to_result(self) -> dict[str, Any]:
        if self.behavior == self.ALLOW:
            return {"behavior": self.ALLOW, "updatedInput": self.updated_input or {}}
        return {"behavior": self.DENY, "message": self.message or "denied by Grove policy"}


class PermissionPolicy:
    """Decide a permission prompt — Grove's bridge into the allow/deny gate.

    **Fail-closed by construction**: a malformed request (``None``) or the default
    ``deny`` mode denies. The configured ``default`` is the bridged decision while
    no interactive human/daemon relay is wired — the MVP of "bridge to Grove's
    permission surface". A ``default: "allow"`` is the deliberate permissive
    opt-in for a bounded sandbox (a container workspace).

    TODO(#172 daemon plumbing): route a prompt to the human/daemon (the same
    same-host-secret loopback the hook ingest + channel receiver use) so an
    operator answers a live permission prompt, and only fall back to ``default``
    when nobody answers in time. Until that resolver exists, the config default is
    the whole policy — enabling the tool can never silently widen what an agent
    may do beyond what ``default`` names.
    """

    def __init__(self, default: str = PermissionOutcome.DENY) -> None:
        # Normalize anything but the explicit permissive opt-in to deny: an
        # unknown mode must fail closed, never accidentally allow.
        self._allow = default == PermissionOutcome.ALLOW

    def decide(self, request: PermissionRequest | None) -> PermissionOutcome:
        if request is None:
            return PermissionOutcome(
                behavior=PermissionOutcome.DENY,
                message="malformed permission request; denying (fail-closed)",
            )
        if self._allow:
            # Echo the input back unmodified (the verbatim-forward, no-rewrite
            # path) — the provider boundary: Grove permits, it never edits.
            return PermissionOutcome(
                behavior=PermissionOutcome.ALLOW, updated_input=dict(request.tool_input)
            )
        return PermissionOutcome(
            behavior=PermissionOutcome.DENY,
            message=(
                f"Grove denied {request.tool_name} (permission relay not wired; "
                "fail-closed default). Set permission.default = 'allow' for a trusted sandbox."
            ),
        )


# ─── config-dir seams (reuse the existing public `paths` helpers) ────────────


def permission_mcp_config_path() -> Path:
    """Grove's permission MCP-config file — sibling of the hook/channel settings."""
    return paths.user_config_path().parent / _MCP_CONFIG_FILENAME


def permission_mcp_config() -> dict[str, Any]:
    """The ``--mcp-config`` dict registering this server as ``grove_permission``.

    Mirrors ``channel.channel_settings`` / ``ClaudeHook.settings``: Grove writes
    this to its own file and passes it via ``claude --mcp-config`` so the user's
    own config is never touched (uninstall = stop passing the flag). The server
    command is ``python -m grove.core.permission`` (the running interpreter, so no
    console-script or ``PATH`` assumption).
    """
    return {
        "mcpServers": {
            PERMISSION_MCP_SERVER: {
                "command": sys.executable,
                "args": ["-m", "grove.core.permission"],
            }
        }
    }


# ─── the permission-prompt server (thin, lazy SDK transport over the policy) ──


class PermissionServer:
    """The stdio MCP server Claude Code spawns from the ``--mcp-config`` file.

    Composes the pure :class:`PermissionPolicy` with the MCP SDK transport. The
    SDK is imported lazily inside :meth:`run` (the ``[mcp]`` extra may be absent);
    everything decidable lives on the pure layer this only wires together, exactly
    like ``channel.ChannelServer``.
    """

    def __init__(self, config: PermissionConfig) -> None:
        self._policy = PermissionPolicy(config.default)

    @property
    def policy(self) -> PermissionPolicy:
        """The decision seam — the test hook and the tool handler's source."""
        return self._policy

    def answer(self, arguments: object) -> str:
        """Decide a prompt and serialize the result JSON string the tool returns.

        Pure over the policy (no SDK, no I/O) so the whole answer path is
        unit-tested without a live MCP handshake. Claude Code reads the tool's
        text content as the decision JSON.
        """
        outcome = self._policy.decide(PermissionRequest.from_arguments(arguments))
        return json.dumps(outcome.to_result())

    def run(self) -> None:  # pragma: no cover - live stdio transport
        """Serve the permission tool over stdio until the agent disconnects.

        Not unit-tested (a live stdio handshake + real SDK, like
        ``ChannelServer.run`` / ``GroveMcpServer.run``); the decision it composes
        (:meth:`answer`) is.
        """
        import asyncio  # noqa: PLC0415

        server = self._build_server()

        async def _serve() -> None:
            from mcp.server.stdio import stdio_server  # noqa: PLC0415

            init_options = server.create_initialization_options()
            async with (
                stdio_server() as (read_stream, write_stream),
                server.run(read_stream, write_stream, init_options) as session,
            ):
                await session.wait_closed()

        asyncio.run(_serve())

    def _build_server(self) -> Any:  # pragma: no cover - needs the SDK
        """Build the low-level MCP ``Server`` exposing the permission tool."""
        try:
            from mcp.server.lowlevel import Server  # noqa: PLC0415
            from mcp.types import TextContent, Tool  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - missing extra
            raise ImportError(
                "grove permission server requires the MCP SDK — install 'grove[mcp]'"
            ) from exc

        server: Any = Server("grove-permission")
        answer = self.answer

        @server.list_tools()  # type: ignore[untyped-decorator]  # dynamic SDK decorator (server: Any)
        async def _list_tools() -> list[Any]:
            return [
                Tool(
                    name=PERMISSION_TOOL_NAME,
                    description="Grove decides whether a Claude Code tool call is permitted.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string"},
                            "tool_input": {"type": "object"},
                            "tool_use_id": {"type": "string"},
                        },
                        "required": ["tool_name"],
                    },
                )
            ]

        @server.call_tool()  # type: ignore[untyped-decorator]  # dynamic SDK decorator (server: Any)
        async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
            # The permission gate expects the decision JSON as the tool's text
            # result; anything but our tool name fails closed to a deny blob.
            payload = arguments if name == PERMISSION_TOOL_NAME else None
            return [TextContent(type="text", text=answer(payload))]

        return server


def main() -> int:  # pragma: no cover - process entry
    """``python -m grove.core.permission`` entry: spawn and run the server.

    Reads the global Grove config for the permission policy, then serves over
    stdio. A missing MCP SDK is a clean hint + non-zero exit, never a chained
    traceback the agent buries.
    """
    config = load_config(repo_root=None).permission
    try:
        PermissionServer(config).run()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PERMISSION_MCP_SERVER",
    "PERMISSION_TOOL_NAME",
    "PermissionOutcome",
    "PermissionPolicy",
    "PermissionRequest",
    "PermissionServer",
    "main",
    "permission_mcp_config",
    "permission_mcp_config_path",
    "permission_tool_ref",
]
