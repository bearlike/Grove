"""Mailbox-only MCP server wiring and least-privilege tool registration."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from grove.client import BackendConfig
from grove.mcp.server import GroveMcpServer, McpServerConfig, main

_MAILBOX_ONLY_TOOLS = {
    "grove_get_skill",
    "grove_list_mailbox_peers",
    "grove_send_mailbox_message",
    "grove_get_mailbox_message_status",
}
_MAILBOX_ONLY_READ_TOOLS = _MAILBOX_ONLY_TOOLS - {"grove_send_mailbox_message"}


class _ClientSpy:
    """Records the one client a mailbox-only server is allowed to own."""

    instances: ClassVar[list[_ClientSpy]] = []

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self.events: list[str] = []
        self.instances.append(self)

    async def connect(self) -> None:
        self.events.append("connect")

    async def close(self) -> None:
        self.events.append("close")


@pytest.fixture
def mailbox_only_config() -> McpServerConfig:
    return McpServerConfig(
        api_url="http://operator.example", api_token="operator-token", mailbox_only=True
    )


def test_mailbox_only_requires_a_bound_mailbox_token(
    monkeypatch: pytest.MonkeyPatch, mailbox_only_config: McpServerConfig
) -> None:
    """An owner token must not substitute for the native worker's bound token."""
    monkeypatch.delenv("GROVE_MAILBOX_TOKEN", raising=False)

    with pytest.raises(ValueError, match="GROVE_MAILBOX_TOKEN"):
        GroveMcpServer(mailbox_only_config)


def test_mailbox_only_config_resolves_from_env_and_cli_tightens() -> None:
    assert McpServerConfig.from_env({"GROVE_MCP_MAILBOX_ONLY": "1"}).mailbox_only is True
    assert McpServerConfig.from_env({}, mailbox_only=True).mailbox_only is True


def test_mailbox_only_cli_option_sets_config(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, Any] = {}

    def stop_before_server(_environ: dict[str, str], **kwargs: Any) -> McpServerConfig:
        received.update(kwargs)
        raise SystemExit

    monkeypatch.setattr(McpServerConfig, "from_env", stop_before_server)

    with pytest.raises(SystemExit):
        main(["--mailbox-only"])

    assert received["mailbox_only"] is True


async def test_mailbox_only_constructs_and_connects_only_the_bound_client(
    monkeypatch: pytest.MonkeyPatch, mailbox_only_config: McpServerConfig
) -> None:
    monkeypatch.setenv("GROVE_MAILBOX_TOKEN", "bound-token")
    monkeypatch.setenv("GROVE_MAILBOX_URL", "http://mailbox.example")
    monkeypatch.setenv("GROVE_MAILBOX_SOCKET", "/run/grove/mailbox.sock")
    _ClientSpy.instances.clear()
    monkeypatch.setattr("grove.mcp.server.GroveClient", _ClientSpy)

    server = GroveMcpServer(mailbox_only_config)

    assert server._client is None
    assert len(_ClientSpy.instances) == 1
    mailbox = _ClientSpy.instances[0]
    assert mailbox._config.label == "mcp-mailbox"
    assert mailbox._config.daemon_token == "bound-token"
    assert mailbox._config.daemon_url == "http://mailbox.example"
    assert str(mailbox._config.daemon_socket) == "/run/grove/mailbox.sock"

    async with server._lifespan(server.fastmcp):
        assert mailbox.events == ["connect"]
    assert mailbox.events == ["connect", "close"]


async def test_mailbox_only_registers_exactly_its_bound_tools_and_local_skills(
    monkeypatch: pytest.MonkeyPatch, mailbox_only_config: McpServerConfig
) -> None:
    monkeypatch.setenv("GROVE_MAILBOX_TOKEN", "bound-token")

    server = GroveMcpServer(mailbox_only_config)

    assert {tool.name for tool in await server.fastmcp.list_tools()} == _MAILBOX_ONLY_TOOLS
    contents, structured = await server.fastmcp.call_tool("grove_get_skill", {})
    assert structured["result"]
    assert contents
    templates = await server.fastmcp.list_resource_templates()
    assert [template.uriTemplate for template in templates] == ["grove://skills/{name}"]
    contents = list(await server.fastmcp.read_resource("grove://skills/using-grove"))
    assert contents[0].content


async def test_mailbox_only_read_only_withholds_send(
    monkeypatch: pytest.MonkeyPatch, mailbox_only_config: McpServerConfig
) -> None:
    monkeypatch.setenv("GROVE_MAILBOX_TOKEN", "bound-token")
    config = McpServerConfig(
        api_url=mailbox_only_config.api_url,
        api_token=mailbox_only_config.api_token,
        mailbox_only=True,
        read_only=True,
    )

    names = {tool.name for tool in await GroveMcpServer(config).fastmcp.list_tools()}

    assert names == _MAILBOX_ONLY_READ_TOOLS
