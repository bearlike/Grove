"""UrlTransport + the explicit-token path — the backend shape grove-mcp uses."""

from __future__ import annotations

import pytest

from grove.client import BackendConfig, GroveClient, TransportError
from grove.client.transport import UrlTransport


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
        await t.open_attach("grove-some-session")


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
