"""LocalTransport spawns 'grove daemon serve --print-port' and returns a usable URL."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from grove.client.backend import BackendConfig
from grove.client.transport import LocalTransport


@pytest.fixture(autouse=True)
def _isolated_daemon_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The spawned daemon runs its WHOLE lifespan, so it must not see the host's state.

    A monkeypatch stops at the process boundary, so conftest's path redirects never
    reach the child; only its environment does. Without this the child read the
    developer's real workspace store and reconciled standing watches into the live
    ``watches.json``, which a running daemon on older code then failed to parse.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.mark.asyncio
async def test_local_transport_spawns_daemon_and_serves_healthz() -> None:
    cfg = BackendConfig(label="Local")
    transport = LocalTransport(cfg)
    await transport.start()
    try:
        async with httpx.AsyncClient(base_url=transport.http_url) as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["version"]
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_local_transport_close_terminates_subprocess() -> None:
    cfg = BackendConfig(label="Local")
    transport = LocalTransport(cfg)
    await transport.start()
    proc = transport._proc  # private — pinned for the test on purpose
    assert proc is not None and proc.poll() is None
    await transport.close()
    assert proc.poll() is not None
