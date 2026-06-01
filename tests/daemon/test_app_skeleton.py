"""Daemon factory + lifespan + healthz."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove import __version__ as GROVE_VERSION
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon_client(tmp_state_dir: Path) -> Iterator[TestClient]:
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        yield client


def test_healthz(daemon_client: TestClient) -> None:
    resp = daemon_client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "version": GROVE_VERSION}


def test_healthz_omits_host_identity(daemon_client: TestClient) -> None:
    """The unauthenticated probe must not leak host / user / uptime."""
    body = daemon_client.get("/healthz").json()
    for key in ("host", "user", "uptime_seconds", "started_at", "python_version"):
        assert key not in body


def test_whoami_returns_daemon_identity(daemon_client: TestClient) -> None:
    """Auth is disabled in this test config so /whoami is reachable.

    The auth gate itself is covered in test_auth_endpoints.py — this
    test pins the response shape, not the gate.
    """
    resp = daemon_client.get("/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == GROVE_VERSION
    assert isinstance(body["uptime_seconds"], int)
    assert body["uptime_seconds"] >= 0
    assert isinstance(body["started_at"], str)
    assert body["host"]
    assert body["user"]
    assert body["platform"] in {"linux", "darwin", "windows"}
    assert body["python_version"]
