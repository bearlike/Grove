"""POST /workspaces/{id}/message and /interrupt — the steer surface (#37)."""

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
            "title": "steer test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


# ─── /message ────────────────────────────────────────────────────────────────


def test_send_message_returns_204_and_injects(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/message", json={"text": "carry on"})

    assert resp.status_code == 204
    assert resp.content == b""
    assert [text for _target, text in fake_tmux.sent_texts] == ["carry on"]


def test_send_message_empty_text_is_422(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/message", json={"text": ""})

    assert resp.status_code == 422
    assert fake_tmux.sent_texts == []


def test_send_message_missing_text_is_422(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.post(f"/workspaces/{created_ws}/message", json={})

    assert resp.status_code == 422


def test_send_message_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post("/workspaces/nope/message", json={"text": "hi"})

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_send_message_paused_is_409_state_error_and_never_injects(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    daemon.post(f"/workspaces/{created_ws}/pause", json={"force": False})

    resp = daemon.post(f"/workspaces/{created_ws}/message", json={"text": "hi"})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "workspace_state_error"
    assert detail["message"]
    assert fake_tmux.sent_texts == []


def test_send_message_no_pane_is_409_pane_not_found(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    # Session up, zero windows — the no-pane shape pane_target returns None for.
    for session in fake_tmux.windows:
        fake_tmux.windows[session] = []

    resp = daemon.post(f"/workspaces/{created_ws}/message", json={"text": "hi"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "pane_not_found"
    assert fake_tmux.sent_texts == []


# ─── /interrupt ──────────────────────────────────────────────────────────────


def test_interrupt_is_501_steering_unsupported(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    # Capability refusal, not a state conflict: no tmux-hosted agent kind
    # has a safe interrupt (the mewbo API arm lands with issue #36).
    resp = daemon.post(f"/workspaces/{created_ws}/interrupt")

    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert detail["error"] == "steering_unsupported"
    assert fake_tmux.sent_texts == []


def test_interrupt_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post("/workspaces/nope/interrupt")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
