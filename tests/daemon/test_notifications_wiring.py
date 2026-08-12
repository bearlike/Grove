"""Daemon ↔ notification-broker wiring.

The broker the daemon lifespan binds to the live ``ActivityService`` bus, driven
through all three triggers on real engine paths: an agent finishing its turn (a
transcript the adapter parses), a question the agent is asking right now (a hook
sidecar seam), and the workspace itself going offline (tmux
reconciliation emitting a ``WorkspaceEvent`` the service bridges onto the bus).
In-memory fakes for tmux and the channel; nothing else is stubbed.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.agents import AgentActivityState
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.notifications import Notification, NotificationBroker, NotificationChannel
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon import build_app
from tests.conftest import FakeTmux

DEEP_LINK_BASE = "https://grove.example.com"

TRANSCRIPT_TURN = (
    '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
    '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
    '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
    '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
    '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n'
)


class _CapturingChannel(NotificationChannel):
    name = "capture"

    def __init__(self) -> None:
        self.received: list[Notification] = []

    def deliver(self, notification: Notification) -> None:
        self.received.append(notification)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@g.l"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def _build_app(store: JsonWorkspaceStore, channel: _CapturingChannel) -> FastAPI:
    """The daemon as a user with notifications on would run it: the broker is bound
    to the live activity bus by ``build_app``'s lifespan, not by the test."""
    cfg = GroveConfig.model_validate(
        {
            "auth": {"enabled": False},
            "tmux": {"session_prefix": "test-"},
            "notifications": {"enabled": True, "deep_link_base_url": DEEP_LINK_BASE},
        }
    )
    broker = NotificationBroker(
        channels=[channel],
        deep_link_base_url=cfg.notifications.deep_link_base_url,
        # These tests are about the wiring (activity → broker → channel), not the
        # quiet-window policy — see tests/core/test_notifications.py for that.
        waiting_quiet=timedelta(0),
    )
    return build_app(cfg=cfg, store=store, notification_broker=broker)


def _await_push(channel: _CapturingChannel, *, count: int = 1) -> None:
    """Block until the dispatch pool has delivered — the one async seam in the path."""
    deadline = time.monotonic() + 3.0
    while len(channel.received) < count and time.monotonic() < deadline:
        time.sleep(0.02)


def _transcript_path(cfg_home: Path, state: WorkspaceState) -> Path:
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{state.agent_session_id}.jsonl"


# ─── the three triggers, each through the daemon lifespan ───────────────────────


def test_agent_finishing_turn_pushes_to_channel(
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    channel = _CapturingChannel()
    app = _build_app(JsonWorkspaceStore(), channel)

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        activity = app.state.activity
        repo = _init_repo(tmp_path / "repo")
        state = registry.get(repo).create(
            CreateWorkspaceRequest(agent_name="claude", title="finish")
        )

        activity.poll_once()  # seed: STARTING, no file → no fire (first observation)
        _transcript_path(cfg_home, state).write_text(TRANSCRIPT_TURN, encoding="utf-8")
        activity.poll_once()  # STARTING → WAITING: rising edge → fire
        _await_push(channel)

    assert len(channel.received) == 1
    n = channel.received[0]
    assert n.trigger == "agent_state"
    assert n.workspace_id == state.id
    assert n.state is AgentActivityState.WAITING
    assert n.severity == "normal"
    assert n.reason == "finished its turn"
    assert n.title() == f"repo · {state.title}"
    assert n.deep_link == f"{DEEP_LINK_BASE}/w/{state.id}"


def test_pending_question_pushes_the_question_notification(
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    """The hook sidecar carries the ask onto the activity stream the instant it is
    posted; the broker turns that into the high-severity push with the options in it."""
    del fake_tmux
    cfg_home = tmp_path / "claude"
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)

    channel = _CapturingChannel()
    app = _build_app(JsonWorkspaceStore(), channel)

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        activity = app.state.activity
        repo = _init_repo(tmp_path / "repo")
        state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="ask"))

        activity.poll_once()  # seed the session (no question yet → no fire)

        ClaudeHook.record_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": state.agent_session_id,
                "tool_name": "AskUserQuestion",
                "tool_use_id": "toolu_1",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Pick a color",
                            "header": "Color",
                            "multiSelect": False,
                            "options": [{"label": "Blue"}, {"label": "Green"}],
                        }
                    ]
                },
            },
            sidecar_dir=sidecar_dir,
            tmux_pane=None,
            now=datetime.now(tz=UTC),
        )
        activity.poll_once()  # the ask appears → question edge → fire
        _await_push(channel)

    assert len(channel.received) == 1
    n = channel.received[0]
    assert n.trigger == "question"
    assert n.severity == "high"
    assert n.workspace_id == state.id
    assert [q.prompt for q in n.questions] == ["Pick a color"]
    body = n.body()
    assert "Color — Pick a color" in body
    assert "- Blue" in body and "- Green" in body


def test_workspace_going_offline_pushes_a_lifecycle_notification(
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_path: Path,
) -> None:
    """The lifecycle arm on its real path: the tmux session vanishes under a running
    agent, status reconciliation emits ``offline_detected``, the activity service
    bridges it onto the bus, and the broker renders it from its identity cache."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    channel = _CapturingChannel()
    app = _build_app(JsonWorkspaceStore(), channel)

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        activity = app.state.activity
        repo = _init_repo(tmp_path / "repo")
        state = registry.get(repo).create(
            CreateWorkspaceRequest(agent_name="claude", title="vanish")
        )

        activity.poll_once()  # seeds the broker's identity cache from the activity row
        fake_tmux.sessions.discard(state.tmux_session)  # the session dies under the agent
        activity.poll_once()  # reconcile → offline_detected → bridged delta → fire
        _await_push(channel)

    assert len(channel.received) == 1
    n = channel.received[0]
    assert n.trigger == "lifecycle"
    assert n.event == "offline_detected"
    assert n.severity == "normal"
    assert n.state is None
    assert n.workspace_id == state.id
    # Identity came from the cache the session-activity arm populated, so the push
    # reads with the workspace's real title/branch rather than a bare short id.
    assert n.title() == f"repo · {state.title}"
    assert n.workspace.branch == state.branch
    assert n.deep_link == f"{DEEP_LINK_BASE}/w/{state.id}"
