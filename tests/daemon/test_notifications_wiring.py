"""Daemon ↔ notification-broker wiring (#70).

Two layers: ``NotificationBroker.from_config`` (config → broker or None), and the
real edge-trigger loop — a workspace whose agent transitions to WAITING drives a
notification to a capturing channel through the broker the lifespan bound to the
live ``ActivityService`` bus. In-memory fakes for tmux and the channel; the
transcript is a real on-disk file the adapter parses.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents import AgentActivityState
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.notifications import Notification, NotificationBroker, NotificationChannel
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux


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


# ─── real edge-trigger loop through the daemon lifespan ─────────────────────────


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

    cfg = GroveConfig.model_validate(
        {
            "auth": {"enabled": False},
            "tmux": {"session_prefix": "test-"},
            "notifications": {"enabled": True, "deep_link_base_url": "https://grove.example.com"},
        }
    )
    channel = _CapturingChannel()
    broker = NotificationBroker(
        channels=[channel], deep_link_base_url=cfg.notifications.deep_link_base_url
    )
    store = JsonWorkspaceStore()
    app = build_app(cfg=cfg, store=store, notification_broker=broker)

    with TestClient(app) as client:
        del client
        registry = app.state.registry
        activity = app.state.activity
        repo = _init_repo(tmp_path / "repo")
        state = registry.get(repo).create(
            CreateWorkspaceRequest(agent_name="claude", title="finish")
        )

        folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
        folder.mkdir(parents=True)
        transcript = folder / f"{state.agent_session_id}.jsonl"

        activity.poll_once()  # seed: STARTING, no file → no fire (first observation)

        transcript.write_text(
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
            '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
            '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
            '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n',
            encoding="utf-8",
        )
        activity.poll_once()  # STARTING → WAITING: rising edge → fire

        deadline = time.monotonic() + 3.0
        while not channel.received and time.monotonic() < deadline:
            time.sleep(0.02)

    assert len(channel.received) == 1
    n = channel.received[0]
    assert n.workspace_id == state.id
    assert n.state is AgentActivityState.WAITING
    assert n.deep_link == f"https://grove.example.com/w/{state.id}"
