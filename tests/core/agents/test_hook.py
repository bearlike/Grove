"""Managed Claude Code status hook (#18): event→state mapping + sidecar I/O."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents.hook import ClaudeHook, HookRecord, PendingQuestion, run_hook_from_stdin
from grove.core.agents.model import AgentActivityState

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

# A real AskUserQuestion PreToolUse payload shape (research-findings.md §1).
_ASK_INPUT = {
    "questions": [
        {
            "question": "Favorite color?",
            "header": "Color",
            "multiSelect": False,
            "options": [{"label": "Blue"}, {"label": "Green"}],
        }
    ]
}


def _pretooluse(
    tool_name: str, tool_use_id: str = "toolu_1", tool_input: dict | None = None
) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s",
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": _ASK_INPUT if tool_input is None else tool_input,
    }


def _event(event: str) -> dict:
    return {"hook_event_name": event, "session_id": "s"}


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

    back = ClaudeHook.read("abc-123", sidecar_dir=tmp_path)
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
    assert ClaudeHook.read("x", sidecar_dir=tmp_path) is None


def _record(event: str) -> HookRecord:
    state = ClaudeHook.state_for(event, {})
    assert state is not None
    return HookRecord(
        session_id="s",
        state=state,
        event=event,
        cwd=None,
        transcript_path=None,
        tmux_pane=None,
        ts=NOW,
    )


def test_supersedes_poll_transcript_advance_beats_any_push() -> None:
    """A transcript record newer than the push makes the push stale instantly.

    This is the steer-after-Stop fix: WAITING must not pin a re-engaged agent
    for the old 5-minute wall-clock window.
    """
    rec = _record("Stop")  # WAITING
    fresh_transcript = NOW + timedelta(seconds=2)
    assert not rec.supersedes_poll(now=NOW + timedelta(seconds=3), transcript_at=fresh_transcript)


def test_supersedes_poll_settled_states_never_age_out() -> None:
    """BLOCKED (polling-invisible) stays authoritative however old, until the
    transcript moves — expiring it into a polled guess re-creates the bug the
    hook exists to fix."""
    rec = _record("Notification")  # BLOCKED
    much_later = NOW + timedelta(hours=2)
    assert rec.supersedes_poll(now=much_later, transcript_at=None)
    assert rec.supersedes_poll(now=much_later, transcript_at=NOW - timedelta(seconds=1))


def test_supersedes_poll_working_ages_out() -> None:
    """WORKING with no newer signal eventually means a dead agent — the poller
    takes over past the window (and on negative clock skew)."""
    rec = _record("PreToolUse")  # WORKING
    assert rec.supersedes_poll(now=NOW + timedelta(seconds=200), transcript_at=None)
    assert not rec.supersedes_poll(now=NOW + timedelta(seconds=10_000), transcript_at=None)
    assert not rec.supersedes_poll(now=NOW - timedelta(seconds=5), transcript_at=None)


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert ClaudeHook.read("nope", sidecar_dir=tmp_path) is None


def test_naive_ts_sidecar_is_rejected_as_malformed(tmp_path: Path) -> None:
    """A timezone-naive ts parses fine but would raise TypeError later, inside
    supersedes_poll's aware comparisons — past the malformed-handling boundary,
    where it breaks the whole snapshot. Reject it at parse time instead."""
    (tmp_path / "s.json").write_text(
        json.dumps(
            {
                "session_id": "s",
                "state": "working",
                "event": "PreToolUse",
                "ts": "2026-06-01T12:00:00",  # no offset → naive
            }
        ),
        encoding="utf-8",
    )
    assert ClaudeHook.read("s", sidecar_dir=tmp_path) is None


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
    rec = ClaudeHook.read("cli-1", sidecar_dir=sidecar)
    assert rec is not None
    assert rec.state is AgentActivityState.WAITING
    assert rec.tmux_pane == "%3"


def test_run_hook_from_stdin_tolerates_garbage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert run_hook_from_stdin() == 0  # never fails the agent


# ─── live-question capture lifecycle (#109) ──────────────────────────────────


def _record_and_read(payload: dict, tmp_path: Path, *, now: datetime = NOW) -> HookRecord | None:
    ClaudeHook.record_event(payload, sidecar_dir=tmp_path, tmux_pane=None, now=now)
    return ClaudeHook.read("s", sidecar_dir=tmp_path)


def test_pretooluse_question_tool_captures_the_pending_question(tmp_path: Path) -> None:
    rec = _record_and_read(_pretooluse("AskUserQuestion"), tmp_path)
    assert rec is not None and rec.question is not None
    q = rec.question
    assert q.tool_use_id == "toolu_1"
    assert q.tool_name == "AskUserQuestion"
    assert q.tool_input == _ASK_INPUT
    assert q.asked_at == NOW
    # State mapping is untouched: a PreToolUse is still WORKING.
    assert rec.state is AgentActivityState.WORKING


def test_pretooluse_non_question_tool_clears_pending(tmp_path: Path) -> None:
    _record_and_read(_pretooluse("AskUserQuestion"), tmp_path)
    rec = _record_and_read(_pretooluse("Bash", tool_use_id="toolu_2", tool_input={}), tmp_path)
    assert rec is not None and rec.question is None


def test_notification_carries_the_pending_question_forward(tmp_path: Path) -> None:
    """The whole point (#109): the permission Notification fires ~6s AFTER the
    ask while the question is still on screen, so it must PRESERVE the capture —
    and it stamps BLOCKED, the state polling can't see."""
    _record_and_read(_pretooluse("AskUserQuestion"), tmp_path)
    rec = _record_and_read(_event("Notification"), tmp_path)
    assert rec is not None
    assert rec.state is AgentActivityState.BLOCKED
    assert rec.question is not None and rec.question.tool_use_id == "toolu_1"


@pytest.mark.parametrize("event", ["PostToolUse", "Stop", "UserPromptSubmit", "SessionEnd"])
def test_terminal_events_clear_pending_question(event: str, tmp_path: Path) -> None:
    """Answered (PostToolUse), Esc-cancel / turn end (Stop), a new prompt, or
    session end all end the question's life."""
    _record_and_read(_pretooluse("AskUserQuestion"), tmp_path)
    rec = _record_and_read(_event(event), tmp_path)
    assert rec is not None and rec.question is None


def test_exit_plan_mode_is_captured_via_the_generic_seam(tmp_path: Path) -> None:
    """Capture keys off QUESTION_TOOL_NAMES, not a hard-coded name — ExitPlanMode
    is captured too (its own tool_input shape), though answering it is out of v1."""
    rec = _record_and_read(
        _pretooluse("ExitPlanMode", tool_use_id="toolu_p", tool_input={"plan": "do it"}),
        tmp_path,
    )
    assert rec is not None and rec.question is not None
    assert rec.question.tool_name == "ExitPlanMode"


def test_pending_question_round_trips_through_the_sidecar_file(tmp_path: Path) -> None:
    rec = _record_and_read(_pretooluse("AskUserQuestion"), tmp_path)
    assert rec is not None and rec.question is not None
    # A fresh read parses the same shape off disk.
    reread = ClaudeHook.read("s", sidecar_dir=tmp_path)
    assert reread is not None and reread.question == rec.question


def test_malformed_question_payload_captures_nothing(tmp_path: Path) -> None:
    # tool_use_id missing → nothing to answer back to → no capture, but the
    # status half of the record still stands.
    rec = _record_and_read(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "s",
            "tool_name": "AskUserQuestion",
            "tool_input": _ASK_INPUT,
        },
        tmp_path,
    )
    assert rec is not None and rec.question is None
    assert rec.state is AgentActivityState.WORKING


def test_pending_question_from_json_rejects_naive_timestamp() -> None:
    assert (
        PendingQuestion.from_json(
            {
                "tool_use_id": "t",
                "tool_name": "AskUserQuestion",
                "tool_input": {},
                "asked_at": "2026-06-01T12:00:00",
            }
        )
        is None
    )
