"""Session-control surface (#178): GET /controls + POST /controls/{invoke,model}.

Pins the wire contract: the enumeration read returns the composed
``SessionControlsView`` shape (fs scan + the shared model catalog), and the two
thin triggers reuse the steer path (a ``/name`` / ``/model <id>`` injection) and
map their typed refusals onto the standard envelope.
"""

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
            "title": "controls test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


# ─── GET /controls ───────────────────────────────────────────────────────────


def test_get_controls_returns_composed_shape(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.get(f"/workspaces/{created_ws}/controls")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "commands",
        "skills",
        "mcp_servers",
        "models",
        "current_model",
        "permission_mode",
    }
    # The model catalog is the manager's contribution via resolve_models — the
    # claude aliases, present even with an empty fs scan.
    assert "sonnet" in body["models"]
    assert body["current_model"] is None  # no transcript minted yet
    assert body["permission_mode"] is None  # permission answering off by default


def test_get_controls_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/controls")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


# ─── POST /controls/invoke ───────────────────────────────────────────────────


def test_invoke_control_returns_204_and_injects_slash_command(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/controls/invoke", json={"name": "review"})

    assert resp.status_code == 204
    assert resp.content == b""
    assert [text for _target, text in fake_tmux.sent_texts] == ["/review"]


def test_invoke_control_empty_name_is_422(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/controls/invoke", json={"name": ""})

    assert resp.status_code == 422
    assert fake_tmux.sent_texts == []


def test_invoke_control_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post("/workspaces/nope/controls/invoke", json={"name": "review"})

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


# ─── POST /controls/model ────────────────────────────────────────────────────


def test_switch_model_returns_204_and_injects(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/controls/model", json={"model": "opus"})

    assert resp.status_code == 204
    assert [text for _target, text in fake_tmux.sent_texts] == ["/model opus"]


def test_switch_model_empty_is_422(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/controls/model", json={"model": ""})

    assert resp.status_code == 422
    assert fake_tmux.sent_texts == []
