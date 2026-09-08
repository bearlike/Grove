"""POST /workspaces/{id}/message and /interrupt — the steer surface."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import SendKey
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


# ─── /keys ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", list(SendKey))
def test_send_keys_returns_204_and_delivers_exactly_one_named_key(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux, key: SendKey
) -> None:
    response = daemon.post(f"/workspaces/{created_ws}/keys", json={"key": key.value})

    assert response.status_code == 204
    assert response.content == b""
    assert len(fake_tmux.sent_keys) == 1
    target, sent = fake_tmux.sent_keys[0]
    assert target.endswith(":agent")
    assert sent == [key]


@pytest.mark.parametrize(
    "body",
    [
        {"key": "C-c; new-session -d"},
        {"key": "$(id)"},
        {"key": "Enter", "target": "other:agent"},
        {"key": "Enter", "command": ["tmux", "send-keys"]},
        {"key": "Enter", "keys": ["C-c", "Enter"]},
        {"key": ["C-c", "Enter"]},
        {"key": "Enter", "repeat": 2},
        {},
    ],
)
def test_send_keys_rejects_untrusted_input_shapes_before_delivery(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux, body: dict[str, object]
) -> None:
    response = daemon.post(f"/workspaces/{created_ws}/keys", json=body)

    assert response.status_code == 422
    assert fake_tmux.sent_keys == []


def test_send_keys_remote_agent_is_501_without_local_injection(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    store = JsonWorkspaceStore()
    state = store.get(created_ws)
    store.save(replace(state, agent_kind="mewbo"))

    response = daemon.post(f"/workspaces/{created_ws}/keys", json={"key": "Escape"})

    assert response.status_code == 501
    assert response.json()["detail"]["error"] == "steering_unsupported"
    assert fake_tmux.sent_keys == []


# ─── /interrupt ──────────────────────────────────────────────────────────────


def test_interrupt_claude_code_sends_escape_and_returns_204(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    # Escape drives Claude Code's own abort controller — the same path its
    # remote-control interrupt frame uses — so a claude_code workspace now
    # succeeds rather than hitting the generic-kind capability refusal.
    resp = daemon.post(f"/workspaces/{created_ws}/interrupt")

    assert resp.status_code == 204
    assert resp.content == b""
    assert len(fake_tmux.escapes) == 1
    assert fake_tmux.escapes[0].endswith(":agent")
    assert fake_tmux.sent_texts == []
    assert fake_tmux.sent_keys == []


def test_interrupt_generic_kind_is_501_steering_unsupported(
    daemon: TestClient, created_ws: str, fake_tmux: FakeTmux
) -> None:
    # Capability refusal, not a state conflict: an agent kind with no verified
    # abort path never gets a guessed cancel keystroke.
    store = JsonWorkspaceStore()
    state = store.get(created_ws)
    store.save(replace(state, agent_kind="generic"))

    resp = daemon.post(f"/workspaces/{created_ws}/interrupt")

    assert resp.status_code == 501
    detail = resp.json()["detail"]
    assert detail["error"] == "steering_unsupported"
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []


def test_interrupt_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post("/workspaces/nope/interrupt")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
