"""Mailbox tools are part of the ordinary MCP surface, not an opt-in mode."""

from __future__ import annotations

import pytest

from grove.mcp.server import GroveMcpServer, McpServerConfig

_MAILBOX_TOOLS = {"grove_list_mailbox_contacts", "grove_send_mailbox_message"}


def _config(**overrides: object) -> McpServerConfig:
    return McpServerConfig(
        api_url="http://127.0.0.1:7421",
        api_token=None,
        **overrides,  # type: ignore[arg-type]
    )


async def _tool_names(server: GroveMcpServer) -> set[str]:
    return {tool.name for tool in await server.fastmcp.list_tools()}


async def test_mailbox_tools_register_without_any_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: a missing token used to withhold the tools entirely."""
    monkeypatch.delenv("GROVE_MAILBOX_TOKEN", raising=False)

    names = await _tool_names(GroveMcpServer(_config()))

    assert names >= _MAILBOX_TOOLS


async def test_read_only_withholds_the_send_but_keeps_the_directory() -> None:
    """Withholding is the only honest denial, and it still applies here."""
    names = await _tool_names(GroveMcpServer(_config(read_only=True)))

    assert "grove_list_mailbox_contacts" in names
    assert "grove_send_mailbox_message" not in names


def test_the_mailbox_only_mode_is_gone() -> None:
    """One server surface; a worker's MCP server is an ordinary one."""
    assert not hasattr(McpServerConfig, "mailbox_only")
    assert "mailbox_only" not in McpServerConfig.__dataclass_fields__
