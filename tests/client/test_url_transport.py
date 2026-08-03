"""UrlTransport + the explicit-token path — the backend shape grove-mcp uses."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from grove.client import BackendConfig, GroveClient, TransportError
from grove.client.transport import UrlTransport
from grove.core.contracts import ContainerAttachView, HostAttachView


def test_url_transport_normalizes_trailing_slash() -> None:
    t = UrlTransport(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421/"))
    assert t.http_url == "http://127.0.0.1:7421"


def test_url_transport_requires_daemon_url() -> None:
    with pytest.raises(ValueError):
        UrlTransport(BackendConfig(label="x"))


async def test_url_transport_lifecycle_is_noop() -> None:
    t = UrlTransport(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421"))
    await t.start()  # no process spawned, no connection opened
    await t.close()
    assert t.http_url == "http://127.0.0.1:7421"  # still valid after close


async def test_url_transport_refuses_interactive_attach() -> None:
    t = UrlTransport(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421"))
    with pytest.raises(TransportError):
        await t.open_attach(["tmux", "attach", "-t", "grove-some-session"])


async def test_open_attach_runs_what_the_engine_named_for_this_runtime() -> None:
    """The pty runs the instruction's own argv, so a containerized
    workspace is entered by exec'ing into its container rather than by naming a
    session on the daemon's host — and nothing in the client branches on it."""
    opened: list[list[str]] = []

    class _Recorder(UrlTransport):
        async def open_attach(self, argv: Sequence[str]) -> Any:
            opened.append(list(argv))
            return None

    client = GroveClient(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421"))
    client._transport = _Recorder(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421"))

    await client.open_attach(HostAttachView(tmux_session="grove-x", inside_outer_tmux=True))
    await client.open_attach(ContainerAttachView(argv=("devcontainer", "exec", "--", "tmux")))

    # `inside_outer_tmux` describes the DAEMON host, never this fresh pty.
    assert opened == [
        ["tmux", "attach", "-t", "grove-x"],
        ["devcontainer", "exec", "--", "tmux"],
    ]


def test_client_picks_url_transport_for_daemon_url() -> None:
    client = GroveClient(BackendConfig(label="x", daemon_url="http://127.0.0.1:7421"))
    assert isinstance(client._transport, UrlTransport)


def test_client_rejects_daemon_url_plus_ssh_target() -> None:
    with pytest.raises(ValueError):
        GroveClient(
            BackendConfig(label="x", daemon_url="http://127.0.0.1:7421", ssh_target="user@host")
        )


async def test_explicit_token_wins_over_local_mint() -> None:
    """With daemon_token set the client must use it verbatim — never mint a
    local session (the URL may point at another machine where the local
    auth.json is meaningless). Pinned via the wire-visible header."""
    client = GroveClient(
        BackendConfig(label="x", daemon_url="http://127.0.0.1:7421", daemon_token="tok-123")
    )
    await client.connect()
    try:
        assert client._http is not None
        assert client._http.headers["authorization"] == "Bearer tok-123"
    finally:
        await client.close()
