"""SshAttach: round-trip bytes through a real asyncssh server fixture."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import asyncssh
import pytest

from grove.client.attach import SshAttach


class _EchoSshServer(asyncssh.SSHServer):
    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "test" and password == "test"


async def _echo_handler(process: asyncssh.SSHServerProcess) -> None:
    """Echo whatever stdin sends, then exit when stdin closes."""
    async for chunk in process.stdin:
        process.stdout.write(chunk)
    process.exit(0)


@pytest.fixture
async def echo_server() -> AsyncIterator[int]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _EchoSshServer,
        host="127.0.0.1",
        port=0,
        server_host_keys=[host_key],
        process_factory=_echo_handler,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ssh_attach_round_trip(echo_server: int) -> None:
    conn = await asyncssh.connect(
        host="127.0.0.1",
        port=echo_server,
        username="test",
        password="test",
        known_hosts=None,
    )
    try:
        # The echo handler ignores command + just pipes stdin→stdout, so
        # _command_override is fine here; tmux_session is irrelevant.
        attach = SshAttach(conn, "irrelevant", _command_override="echo")
        received: list[bytes] = []
        attach.on_output(received.append)
        await attach.start()
        try:
            await attach.feed_input(b"ping\n")
            for _ in range(100):
                if b"ping" in b"".join(received):
                    break
                await asyncio.sleep(0.02)
            assert b"ping" in b"".join(received)
            await attach.resize(cols=80, rows=24)
        finally:
            await attach.close()
    finally:
        conn.close()
        await conn.wait_closed()
