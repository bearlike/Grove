"""Managed Claude Code status hook (#18): event→state mapping + sidecar I/O."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents.hook import ClaudeHook, run_hook_from_stdin
from grove.core.agents.model import AgentActivityState

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("SessionStart", AgentActivityState.WORKING),
        ("UserPromptSubmit", AgentActivityState.WORKING),
        ("PostToolUse", AgentActivityState.WORKING),
        ("Notification", AgentActivityState.BLOCKED),  # the polling-can't-see signal
        ("Stop", AgentActivityState.WAITING),
        ("SessionEnd", AgentActivityState.IDLE),
        ("SubagentStop", None),  # never flip the main thread on a sub-agent event
        ("WeirdFutureEvent", None),
    ],
)
def test_state_for(event: str, expected: AgentActivityState | None) -> None:
    assert ClaudeHook.state_for(event, {}) is expected


def test_record_event_round_trips(tmp_path: Path) -> None:
    payload = {
        "hook_event_name": "Notification",
        "session_id": "abc-123",
        "cwd": "/home/kk/work",
        "transcript_path": "/t/abc-123.jsonl",
    }
    rec = ClaudeHook.record_event(payload, sidecar_dir=tmp_path, tmux_pane="%7", now=NOW)
    assert rec is not None and rec.state is AgentActivityState.BLOCKED

    back = ClaudeHook.read("abc-123", sidecar_dir=tmp_path, now=NOW)
    assert back is not None
    assert back.state is AgentActivityState.BLOCKED
    assert back.tmux_pane == "%7"
    assert back.cwd == "/home/kk/work"


def test_record_event_ignores_untracked(tmp_path: Path) -> None:
    rec = ClaudeHook.record_event(
        {"hook_event_name": "SubagentStop", "session_id": "x"},
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )
    assert rec is None
    assert ClaudeHook.read("x", sidecar_dir=tmp_path, now=NOW) is None


def test_read_stale_sidecar_returns_none(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        {"hook_event_name": "Stop", "session_id": "s"},
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )
    later = NOW + timedelta(seconds=10_000)
    assert ClaudeHook.read("s", sidecar_dir=tmp_path, now=later, max_age_seconds=300) is None
    # Within the window it's still trusted.
    assert ClaudeHook.read("s", sidecar_dir=tmp_path, now=NOW + timedelta(seconds=5)) is not None


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert ClaudeHook.read("nope", sidecar_dir=tmp_path, now=NOW) is None


def test_settings_installs_a_command_per_event() -> None:
    settings = ClaudeHook.settings("grove agent-hook")
    hooks = settings["hooks"]
    assert "Notification" in hooks and "Stop" in hooks
    entry = hooks["Notification"][0]["hooks"][0]
    assert entry == {"type": "command", "command": "grove agent-hook"}


def test_run_hook_from_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "cli-1"}))
    )
    monkeypatch.setenv("TMUX_PANE", "%3")

    assert run_hook_from_stdin() == 0
    rec = ClaudeHook.read("cli-1", sidecar_dir=sidecar, now=datetime.now(tz=UTC))
    assert rec is not None
    assert rec.state is AgentActivityState.WAITING
    assert rec.tmux_pane == "%3"


def test_run_hook_from_stdin_tolerates_garbage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert run_hook_from_stdin() == 0  # never fails the agent
