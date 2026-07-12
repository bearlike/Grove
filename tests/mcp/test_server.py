"""GroveMcpServer wiring — published tool names, config resolution, and a
round-trip through FastMCP's dispatch (issue #6's connect-and-list check)."""

from __future__ import annotations

import json
import sys

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from grove.mcp.server import GroveMcpServer, McpServerConfig, main
from tests.mcp.conftest import FakeGroveClient

EXPECTED_TOOLS = {
    "grove_list_workspaces",
    "grove_get_workspace",
    "grove_list_agents",
    "grove_create_workspace",
    "grove_peek_workspace",
    "grove_pause_workspace",
    "grove_resume_workspace",
    "grove_respawn_workspace",
    "grove_kill_workspace",
    "grove_attach_instruction",
    "grove_send_workspace_message",
    "grove_remap_workspace_session",
}


@pytest.fixture
def server(fake_client: FakeGroveClient) -> GroveMcpServer:
    cfg = McpServerConfig(api_url="http://127.0.0.1:7421", api_token=None)
    return GroveMcpServer(cfg, client=fake_client)


async def test_registers_exactly_the_published_tool_surface(
    server: GroveMcpServer,
) -> None:
    tools = await server.fastmcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_list_workspaces_round_trips_through_fastmcp(
    server: GroveMcpServer, fake_client: FakeGroveClient
) -> None:
    """Issue #6 acceptance shape: an MCP client calls grove_list_workspaces
    and gets a valid, parseable response."""
    content, structured = await server.fastmcp.call_tool("grove_list_workspaces", {})
    # FastMCP wraps a list return under {"result": [...]} in the structured
    # output; the text content carries the same items serialized one by one.
    assert [item["id"] for item in structured["result"]] == ["ws-1", "ws-2"]
    assert json.loads(content[0].text)["id"] == "ws-1"
    assert fake_client.calls == [
        ("list_workspaces", {"repo": None, "ticket_provider": None, "ticket_id": None})
    ]


async def test_kill_without_delete_branch_is_rejected_at_the_schema(
    server: GroveMcpServer, fake_client: FakeGroveClient
) -> None:
    """Destructive-tool safety end to end: omitting delete_branch fails
    validation before the client is ever called."""
    with pytest.raises(ToolError):
        await server.fastmcp.call_tool("grove_kill_workspace", {"workspace_id": "ws-1"})
    assert fake_client.calls == []


# ─── config resolution ───────────────────────────────────────────────────────


def test_config_defaults_when_env_is_empty() -> None:
    cfg = McpServerConfig.from_env({})
    assert cfg.api_url == McpServerConfig.DEFAULT_API_URL
    assert cfg.api_token is None


def test_config_reads_env() -> None:
    cfg = McpServerConfig.from_env(
        {"GROVE_API_URL": "http://127.0.0.1:9999", "GROVE_API_TOKEN": "tok"}
    )
    assert cfg.api_url == "http://127.0.0.1:9999"
    assert cfg.api_token == "tok"


def test_config_cli_flag_beats_env() -> None:
    cfg = McpServerConfig.from_env(
        {"GROVE_API_URL": "http://127.0.0.1:9999"}, api_url="http://127.0.0.1:7777"
    )
    assert cfg.api_url == "http://127.0.0.1:7777"


def test_config_empty_token_env_means_unset() -> None:
    """An empty GROVE_API_TOKEN must fall back to the local mint, never
    send `Bearer ` with an empty credential."""
    cfg = McpServerConfig.from_env({"GROVE_API_TOKEN": ""})
    assert cfg.api_token is None


# ─── missing-SDK entry point ─────────────────────────────────────────────────


def test_main_exits_cleanly_when_mcp_sdk_is_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`grove-mcp` is an unconditional console script, but the `mcp` SDK is
    behind the `[mcp]` extra. On a `.[daemon]`-only host `main()` must surface
    the install hint and exit non-zero — never crash at import with a traceback.

    Stubs the real import boundary (Python's module system) rather than any
    Grove symbol: setting the SDK submodule to ``None`` makes the deferred
    ``import mcp.server.fastmcp`` raise, exactly as a missing extra would.
    """
    for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
        monkeypatch.setitem(sys.modules, name, None)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code != 0
    # SystemExit(str) prints the message to stderr with no traceback.
    assert "pip install 'grove[mcp]'" in capsys.readouterr().err
