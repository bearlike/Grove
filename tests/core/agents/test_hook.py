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


def test_settings_registers_subagent_events_with_no_state_mapping() -> None:
    """#171: SubagentStart/Stop are registered (so the daemon push path fires
    on sub-agent lifecycle) even though `state_for` still returns `None` for
    both — a sub-agent never flips the main thread's state."""
    hooks = ClaudeHook.settings("grove agent-hook")["hooks"]
    assert "SubagentStart" in hooks
    assert "SubagentStop" in hooks
    assert ClaudeHook.state_for("SubagentStart", {}) is None
    assert ClaudeHook.state_for("SubagentStop", {}) is None


def test_settings_notification_registers_all_three_matchers() -> None:
    """#171: the Notification event is split into its three known sub-kinds as
    distinct matcher entries rather than one untyped catch-all."""
    hooks = ClaudeHook.settings("grove agent-hook")["hooks"]
    matchers = {entry["matcher"] for entry in hooks["Notification"]}
    assert matchers == {"permission_prompt", "idle_prompt", "agent_needs_input"}
    # Every matcher still routes to the same handlers, and the mapped state is
    # unaffected — the split is registration-only.
    for entry in hooks["Notification"]:
        assert entry["hooks"][0] == {"type": "command", "command": "grove agent-hook"}
    assert ClaudeHook.state_for("Notification", {}) is AgentActivityState.BLOCKED


def test_settings_adds_an_http_handler_carrying_the_bearer_token() -> None:
    """#171: the http handler POSTs to the daemon's ingest route with the
    same-host ingest token as its bearer — the daemon push half."""
    hooks = ClaudeHook.settings("grove agent-hook")["hooks"]
    handlers = hooks["Stop"][0]["hooks"]
    assert handlers[0] == {"type": "command", "command": "grove agent-hook"}
    http_handler = handlers[1]
    assert http_handler["type"] == "http"
    assert http_handler["url"].endswith("/hooks/agent-events")
    token = ClaudeHook.ensure_ingest_token()
    assert http_handler["headers"] == {"Authorization": f"Bearer {token}"}


def test_settings_daemon_url_none_omits_the_http_handler() -> None:
    """The pre-#171 command-only shape is still reachable (the test seam)."""
    hooks = ClaudeHook.settings("grove agent-hook", daemon_url=None)["hooks"]
    assert hooks["Stop"][0]["hooks"] == [{"type": "command", "command": "grove agent-hook"}]


def test_ensure_ingest_token_is_stable_across_calls() -> None:
    """The same secret round-trips off disk rather than re-minting each call —
    otherwise the daemon and the rendered settings file would disagree."""
    first = ClaudeHook.ensure_ingest_token()
    second = ClaudeHook.ensure_ingest_token()
    assert first == second
    assert len(first) > 20  # a real secrets.token_urlsafe, not a placeholder


# ─── #171 VERIFICATION-ONLY: Stop-hook decision:block is NOT acted on here ───


def test_stop_hook_decision_block_is_not_yet_a_delivery_channel(tmp_path: Path) -> None:
    """VERIFICATION-ONLY note (#171): pins today's scope so #172 (native
    answer path) has a clean baseline to diff against.

    Claude Code's ``Stop`` hook accepts a ``{"decision": "block", "reason":
    ...}`` reply on the hook's OWN stdout to make the agent keep going with
    ``reason`` injected as additional context — a genuine continuation
    mechanism, distinct from a status push. This issue is detection + push
    only: an incoming ``Stop`` event (which never carries ``decision``/
    ``additionalContext`` — those are the hook's *response* shape, not its
    input) still maps to plain WAITING, and `grove agent-hook` never emits a
    stdout reply for Claude Code to parse as a Stop-hook decision. Building
    Stop-as-delivery (answering a captured question by replying to its own
    Stop invocation, instead of the tmux-keystroke path in #109) is out of
    scope here — tracked as the #171 follow-up (#172).
    """
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        # Included only to document that unknown payload keys never change
        # the mapping today (`state_for`'s `del payload`) — a real Stop event
        # never carries these; they'd be the hook's OWN reply shape.
        "decision": "block",
        "reason": "keep going",
    }
    rec = ClaudeHook.record_event(payload, sidecar_dir=tmp_path, tmux_pane=None, now=NOW)
    assert rec is not None
    assert rec.state is AgentActivityState.WAITING
    # `record_event` returns a `HookRecord | None` — never a string `grove
    # agent-hook` could echo to stdout, so there is no delivery channel yet.


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
