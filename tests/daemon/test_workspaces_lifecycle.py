"""GET /workspaces/{id}, POST pause/resume/respawn/kill."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def created_ws(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "lifecycle test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


def test_get_returns_state_view(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.get(f"/workspaces/{created_ws}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created_ws


def test_get_unknown_returns_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_pause(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/pause", json={"force": False})
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


def test_resume_after_pause(daemon: TestClient, created_ws: str) -> None:
    daemon.post(f"/workspaces/{created_ws}/pause", json={"force": False})
    resp = daemon.post(f"/workspaces/{created_ws}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"running", "active", "idle"}


def test_kill_returns_204(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/kill", json={"delete_branch": True})
    assert resp.status_code == 204
    assert resp.content == b""


def test_kill_then_get_returns_404(daemon: TestClient, created_ws: str) -> None:
    daemon.post(f"/workspaces/{created_ws}/kill", json={})
    resp = daemon.get(f"/workspaces/{created_ws}")
    assert resp.status_code == 404


def test_respawn_offline_workspace(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    # Simulate the tmux session vanishing externally.
    fake_tmux.sessions.clear()
    # Force-list so the reconciler promotes the persisted RUNNING intent to
    # OFFLINE — which is the only state from which respawn() is permitted.
    daemon.get("/workspaces")
    resp = daemon.post(f"/workspaces/{created_ws}/respawn")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in {"running", "active", "idle"}
