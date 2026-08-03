"""GroveMcpServer wiring — published tool names, config resolution, and a
round-trip through FastMCP's dispatch."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from grove.client import TransportError
from grove.mcp.server import GroveMcpServer, McpServerConfig, SharedSecretVerifier, main
from tests.mcp.conftest import FakeGroveClient

EXPECTED_TOOLS = {
    "grove_list_projects",
    "grove_list_workspaces",
    "grove_get_workspace",
    "grove_list_agents",
    "grove_list_sessions",
    "grove_create_workspace",
    "grove_peek_workspace",
    "grove_get_fleet_status",
    "grove_pause_workspace",
    "grove_resume_workspace",
    "grove_respawn_workspace",
    "grove_kill_workspace",
    "grove_attach_instruction",
    "grove_send_workspace_message",
    "grove_remap_workspace_session",
    "grove_attach_ticket",
    "grove_detach_ticket",
    "grove_get_workspace_phase",
    "grove_set_workspace_phase",
    "grove_get_workspace_todo",
}

# The read-only scope: tools that only observe. Every OTHER published tool
# mutates and must be withheld under `read_only`, so the mutating set is
# derived (EXPECTED_TOOLS - this) rather than listed a second time — adding a
# tool to the contract above without classifying it here fails these tests.
NON_MUTATING_TOOLS = {
    "grove_list_projects",
    "grove_list_workspaces",
    "grove_get_workspace",
    "grove_list_agents",
    "grove_list_sessions",
    "grove_peek_workspace",
    "grove_get_fleet_status",
    "grove_attach_instruction",
    "grove_get_workspace_phase",
    "grove_get_workspace_todo",
}


def make_network_config(**overrides: Any) -> McpServerConfig:
    """A minimal *valid* networked config — i.e. one carrying an inbound token."""
    fields: dict[str, Any] = {
        "api_url": "http://127.0.0.1:7421",
        "api_token": None,
        "transport": "streamable-http",
        "auth_token": "inbound-secret",
    }
    fields.update(overrides)
    return McpServerConfig(**fields)


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


async def test_tool_failure_reaches_the_client_with_a_reason(
    server: GroveMcpServer, fake_client: FakeGroveClient
) -> None:
    """FastMCP renders a failing tool as ``f"Error executing tool {name}: {e}"``,
    so an exception whose ``str()`` is empty — which is exactly what an unguarded
    ``httpx`` timeout is — reaches the caller as that prefix and *nothing else*.
    An orchestrator then cannot tell a dropped connection from a dead workspace
    from a transient blip, and blind retry is its only strategy. Assert on the
    body after the prefix, not merely that something raised: an empty reason
    passes every ``pytest.raises(ToolError)`` ever written.
    """
    fake_client.send_message_error = TransportError(
        "POST /workspaces/ws-1/message to daemon http://127.0.0.1:7421 timed out after 30s."
    )
    with pytest.raises(ToolError) as excinfo:
        await server.fastmcp.call_tool(
            "grove_send_workspace_message", {"workspace_id": "ws-1", "text": "hello"}
        )

    prefix = "Error executing tool grove_send_workspace_message:"
    rendered = str(excinfo.value)
    assert rendered.startswith(prefix)
    assert rendered.removeprefix(prefix).strip(), "tool error reached the caller with no reason"


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


# ─── transport config ────────────────────────────────────────────────────────


def test_transport_defaults_to_stdio_with_no_socket_settings() -> None:
    """Zero config must stay the historical stdio server, and the bind
    settings must be inert defaults rather than something already listening."""
    cfg = McpServerConfig.from_env({})
    assert cfg.transport == "stdio"
    assert cfg.is_networked is False
    assert (cfg.host, cfg.port, cfg.path) == (
        McpServerConfig.DEFAULT_BIND_HOST,
        McpServerConfig.DEFAULT_PORT,
        McpServerConfig.DEFAULT_PATH,
    )
    assert cfg.auth_token is None
    assert cfg.read_only is False
    assert cfg.allowed_hosts == ()
    assert cfg.allowed_origins == ()


def test_transport_config_reads_env() -> None:
    cfg = McpServerConfig.from_env(
        {
            "GROVE_MCP_TRANSPORT": "streamable-http",
            "GROVE_MCP_HOST": "0.0.0.0",
            "GROVE_MCP_PORT": "9000",
            "GROVE_MCP_PATH": "/grove-mcp",
            "GROVE_MCP_TOKEN": "inbound-secret",
        }
    )
    assert cfg.transport == "streamable-http"
    assert cfg.is_networked is True
    assert (cfg.host, cfg.port, cfg.path) == ("0.0.0.0", 9000, "/grove-mcp")
    assert cfg.auth_token == "inbound-secret"


def test_transport_http_is_an_alias_for_streamable_http() -> None:
    """`http` is Claude Code's spelling of this transport; a value copied from
    an MCP client's config must resolve, not error."""
    assert McpServerConfig.from_env({"GROVE_MCP_TRANSPORT": "http"}).transport == "streamable-http"
    assert McpServerConfig._as_transport("http") == "streamable-http"


def test_transport_cli_flags_beat_env() -> None:
    cfg = McpServerConfig.from_env(
        {
            "GROVE_MCP_TRANSPORT": "stdio",
            "GROVE_MCP_HOST": "10.0.0.1",
            "GROVE_MCP_PORT": "9000",
            "GROVE_MCP_PATH": "/from-env",
            "GROVE_MCP_TOKEN": "inbound-secret",
        },
        transport="streamable-http",
        host="127.0.0.1",
        port=7777,
        path="/from-flag",
    )
    assert cfg.transport == "streamable-http"
    assert (cfg.host, cfg.port, cfg.path) == ("127.0.0.1", 7777, "/from-flag")


def test_read_only_env_cannot_be_loosened_by_omitting_the_flag() -> None:
    """--read-only only ever tightens: an operator who exported the env var
    must not get a full-control server back by dropping the flag."""
    cfg = McpServerConfig.from_env({"GROVE_MCP_READ_ONLY": "1"}, read_only=False)
    assert cfg.read_only is True
    # ...and the flag alone still works with no env var set.
    assert McpServerConfig.from_env({}, read_only=True).read_only is True


def test_allowlists_parse_as_trimmed_csv() -> None:
    cfg = McpServerConfig.from_env(
        {
            "GROVE_MCP_ALLOWED_HOSTS": "grove.example.com, localhost:7431 ,",
            "GROVE_MCP_ALLOWED_ORIGINS": "https://grove.example.com",
        }
    )
    assert cfg.allowed_hosts == ("grove.example.com", "localhost:7431")
    assert cfg.allowed_origins == ("https://grove.example.com",)


def test_unknown_transport_is_rejected() -> None:
    """`sse` is deliberately unsupported — it must fail loudly rather than
    silently degrade to stdio."""
    with pytest.raises(ValueError, match="unknown MCP transport"):
        McpServerConfig.from_env({"GROVE_MCP_TRANSPORT": "sse"})


def test_non_integer_port_is_rejected() -> None:
    with pytest.raises(ValueError, match="GROVE_MCP_PORT"):
        McpServerConfig.from_env({"GROVE_MCP_PORT": "not-a-port"})


# ─── fail-closed: no network socket without an inbound token ─────────────────


def test_networked_config_without_a_token_is_rejected() -> None:
    cfg = McpServerConfig.from_env({"GROVE_MCP_TRANSPORT": "streamable-http"})
    with pytest.raises(ValueError, match="GROVE_MCP_TOKEN"):
        cfg.validate()


def test_constructing_a_networked_server_without_a_token_is_rejected(
    fake_client: FakeGroveClient,
) -> None:
    """The guard has to bite at construction, not only when validate() is
    called by hand — nothing may build a serveable app without a token."""
    cfg = McpServerConfig.from_env({"GROVE_MCP_TRANSPORT": "http"})
    with pytest.raises(ValueError, match="GROVE_MCP_TOKEN"):
        GroveMcpServer(cfg, client=fake_client)


def test_stdio_needs_no_inbound_token(fake_client: FakeGroveClient) -> None:
    """The zero-config local path must not regress: under stdio the spawning
    client IS the authentication, so no token exists or is required."""
    cfg = McpServerConfig.from_env({})
    assert cfg.auth_token is None
    cfg.validate()  # must not raise
    GroveMcpServer(cfg, client=fake_client)


# ─── read-only tool registration ─────────────────────────────────────────────


async def test_read_only_registers_only_the_non_mutating_tools(
    fake_client: FakeGroveClient,
) -> None:
    """Withholding is the only honest denial: a registered tool an agent can
    see is a tool it will try to call."""
    cfg = McpServerConfig(api_url="http://127.0.0.1:7421", api_token=None, read_only=True)
    tools = await GroveMcpServer(cfg, client=fake_client).fastmcp.list_tools()
    assert {t.name for t in tools} == NON_MUTATING_TOOLS


async def test_full_control_registers_the_mutating_tools_too(
    fake_client: FakeGroveClient,
) -> None:
    cfg = McpServerConfig(api_url="http://127.0.0.1:7421", api_token=None, read_only=False)
    tools = await GroveMcpServer(cfg, client=fake_client).fastmcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert names - NON_MUTATING_TOOLS  # the mutating half is genuinely non-empty


# ─── inbound bearer verification ─────────────────────────────────────────────


async def test_verifier_accepts_the_shared_secret() -> None:
    access = await SharedSecretVerifier("inbound-secret").verify_token("inbound-secret")
    assert access is not None
    assert access.token == "inbound-secret"
    assert access.client_id == "grove-mcp"
    # No scopes on purpose: Grove has no authorization model to mint them from.
    assert access.scopes == []


@pytest.mark.parametrize("presented", ["wrong-secret", "", "inbound-secre", "inbound-secret "])
async def test_verifier_rejects_anything_but_an_exact_match(presented: str) -> None:
    """Includes the near-misses a prefix-wise comparison would let through."""
    assert await SharedSecretVerifier("inbound-secret").verify_token(presented) is None


# ─── FastMCP network wiring ──────────────────────────────────────────────────


def test_network_kwargs_are_empty_under_stdio() -> None:
    cfg = McpServerConfig(api_url="http://127.0.0.1:7421", api_token=None)
    assert GroveMcpServer._network_kwargs(cfg) == {}


def test_network_kwargs_thread_the_bind_settings_through() -> None:
    cfg = make_network_config(host="0.0.0.0", port=9000, path="/grove-mcp")
    kwargs = GroveMcpServer._network_kwargs(cfg)
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9000
    # The SDK names the mount path `streamable_http_path`, not `path`.
    assert kwargs["streamable_http_path"] == "/grove-mcp"


def test_network_kwargs_pair_auth_with_the_token_verifier() -> None:
    """Load-bearing pairing: the SDK gates AuthenticationMiddleware behind
    `auth` but wraps the route behind `token_verifier`. Only one of the two
    yields a route that 401s everything, which reads as 'secure' in a smoke
    test while actually being broken."""
    kwargs = GroveMcpServer._network_kwargs(make_network_config())
    assert kwargs["auth"] is not None
    assert isinstance(kwargs["token_verifier"], SharedSecretVerifier)
    assert kwargs["token_verifier"].token == "inbound-secret"
    # None suppresses the RFC 9728 protected-resource metadata routes: we are
    # not an OAuth resource server, and advertising one would send a
    # spec-compliant client into a doomed OAuth flow past its static bearer.
    assert kwargs["auth"].resource_server_url is None
    assert kwargs["auth"].required_scopes == []


def test_dns_rebinding_protection_is_off_until_an_allowlist_is_configured() -> None:
    """Enabling it with an empty allowlist 421s every request, so it stays
    opt-in — the operator names the hostnames clients actually use."""
    assert GroveMcpServer._network_kwargs(make_network_config())["transport_security"] is None


def test_dns_rebinding_protection_turns_on_with_an_allowlist() -> None:
    cfg = make_network_config(
        allowed_hosts=("grove.example.com",),
        allowed_origins=("https://grove.example.com",),
    )
    security = GroveMcpServer._network_kwargs(cfg)["transport_security"]
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["grove.example.com"]
    assert security.allowed_origins == ["https://grove.example.com"]


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


def test_main_refuses_to_bind_a_network_port_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fail-closed check reaching the console script: `--transport http`
    with no inbound token must die with one actionable line and exit 2 —
    never reach run() and publish grove_kill_workspace to the network."""
    for key in ("GROVE_MCP_TOKEN", "GROVE_MCP_TRANSPORT", "GROVE_MCP_HOST", "GROVE_MCP_PORT"):
        monkeypatch.delenv(key, raising=False)

    def never(_self: GroveMcpServer) -> None:
        raise AssertionError("run() must not be reached without an inbound token")

    monkeypatch.setattr(GroveMcpServer, "run", never)

    with pytest.raises(SystemExit) as exc_info:
        main(["--transport", "http"])

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "GROVE_MCP_TOKEN" in err
    assert "grove-mcp:" in err


def test_explicit_port_zero_is_rejected_not_silently_defaulted() -> None:
    """An explicit `--port 0` must fail loudly.

    Port 0 asks the OS for an ephemeral port, which is meaningless for a server
    clients must be pointed at (the daemon gets away with it only because it has
    `--print-port`). The trap is the resolution chain: an `or`-chain would treat
    0 as falsy and silently serve on the default instead of the requested port.
    """
    cfg = McpServerConfig.from_env({"GROVE_MCP_TOKEN": "tok"}, transport="streamable-http", port=0)
    assert cfg.port == 0, "explicit --port 0 must survive resolution, not become the default"
    with pytest.raises(ValueError, match="between 1 and 65535"):
        cfg.validate()


def test_out_of_range_port_is_rejected() -> None:
    cfg = McpServerConfig.from_env(
        {"GROVE_MCP_TOKEN": "tok"}, transport="streamable-http", port=99999
    )
    with pytest.raises(ValueError, match="between 1 and 65535"):
        cfg.validate()


def test_port_range_is_not_enforced_under_stdio() -> None:
    """stdio binds nothing, so the port field is inert — validating it there
    would reject a harmless leftover env var."""
    McpServerConfig.from_env({"GROVE_MCP_PORT": "0"}).validate()
