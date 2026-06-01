"""SshTransport opens asyncssh.connect, port-forwards to the remote daemon."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import asyncssh
import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from grove.client.backend import BackendConfig
from grove.client.transport import SshTransport

_TEST_HTTP_PORT = 18421


async def _run_upstream_app() -> tuple[uvicorn.Server, asyncio.Task[None]]:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok-via-ssh"}

    config = uvicorn.Config(app, host="127.0.0.1", port=_TEST_HTTP_PORT, log_config=None)
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    # Wait for bind by polling /healthz.
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"http://127.0.0.1:{_TEST_HTTP_PORT}/healthz", timeout=0.1)
                if resp.status_code == 200:
                    break
        except Exception:
            await asyncio.sleep(0.05)
    return server, server_task


class _PWAuthSshServer(asyncssh.SSHServer):
    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "test" and password == "test"

    def connection_requested(
        self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
    ) -> bool:
        # Allow standard direct TCP/IP forwarding to any host:port — the test
        # fixture is fully in-process, so there's no risk surface.
        return True


@pytest.fixture
async def upstream_http() -> AsyncIterator[None]:
    server, server_task = await _run_upstream_app()
    try:
        yield None
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)


@pytest.fixture
async def ssh_server() -> AsyncIterator[int]:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _PWAuthSshServer,
        host="127.0.0.1",
        port=0,
        server_host_keys=[host_key],
        process_factory=lambda p: p.exit(0),
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_ssh_transport_forwards_http_through_tunnel(
    upstream_http: None, ssh_server: int
) -> None:
    cfg = BackendConfig(
        label="remote",
        ssh_target=f"test:test@127.0.0.1:{ssh_server}",
        daemon_port=_TEST_HTTP_PORT,
    )
    transport = SshTransport(cfg, known_hosts=None)
    await transport.start()
    try:
        async with httpx.AsyncClient(base_url=transport.http_url) as client:
            resp = await client.get("/healthz", timeout=5.0)
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok-via-ssh"}
    finally:
        await transport.close()
