"""Daemon factory + lifespan + healthz."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove import __version__ as GROVE_VERSION
from grove.core.config import GroveConfig
from grove.core.release import ReleaseChecker
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
    # Release-skew fields default to "unknown" — the autouse offline fixture
    # keeps the default checker from reaching GitHub.
    assert body["latest_version"] is None
    assert body["update_available"] is False
    # telemetry is disabled in daemon_test_config(), so no Langfuse button.
    assert body["langfuse_host"] is None


def test_whoami_surfaces_langfuse_host_when_fully_configured(
    tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host reaches the wire only once the whole credential trio resolves.

    Mirrors the exact resolution `grove doctor`'s telemetry check uses
    (`derive_env` + `unresolved`) — see `_resolve_langfuse_host` in
    `grove.daemon.app`.
    """
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    cfg = GroveConfig.model_validate({"auth": {"enabled": False}, "telemetry": {"enabled": True}})
    app = build_app(cfg=cfg, store=JsonWorkspaceStore())
    with TestClient(app) as client:
        body = client.get("/whoami").json()
    assert body["langfuse_host"] == "https://example.invalid"


def test_whoami_omits_langfuse_host_when_credentials_are_partial(
    tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host with no matching keys must never reach the wire — that button
    would open Langfuse for a deployment that never actually exports there."""
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    cfg = GroveConfig.model_validate({"auth": {"enabled": False}, "telemetry": {"enabled": True}})
    app = build_app(cfg=cfg, store=JsonWorkspaceStore())
    with TestClient(app) as client:
        body = client.get("/whoami").json()
    assert body["langfuse_host"] is None


def test_whoami_surfaces_a_newer_release(tmp_state_dir: Path) -> None:
    """An injected checker reporting a higher tag flows to the wire fields."""
    app = build_app(
        cfg=daemon_test_config(),
        store=JsonWorkspaceStore(),
        release_checker=ReleaseChecker(installed=GROVE_VERSION, fetcher=lambda: "v999.0.0"),
    )
    with TestClient(app) as client:
        body = client.get("/whoami").json()
    assert body["latest_version"] == "999.0.0"
    assert body["update_available"] is True


def test_whoami_no_update_when_remote_not_newer(tmp_state_dir: Path) -> None:
    """A tag equal to the installed version reports the version but no nudge."""
    app = build_app(
        cfg=daemon_test_config(),
        store=JsonWorkspaceStore(),
        release_checker=ReleaseChecker(installed=GROVE_VERSION, fetcher=lambda: GROVE_VERSION),
    )
    with TestClient(app) as client:
        body = client.get("/whoami").json()
    assert body["latest_version"] == GROVE_VERSION
    assert body["update_available"] is False
