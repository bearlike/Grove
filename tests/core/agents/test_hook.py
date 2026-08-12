"""Managed Claude Code status hook (#18): event→state mapping + sidecar I/O."""

from __future__ import annotations

import io
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core import paths
from grove.core.agents.hook import (
    DEFAULT_DAEMON_LOOPBACK_URL,
    ClaudeHook,
    HookRecord,
    PendingQuestion,
    SubagentHookRecord,
    run_hook_from_stdin,
)
from grove.core.agents.model import AgentActivityState
from grove.core.workspace import WorkspaceState, WorkspaceStatus

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
        "cwd": "/home/dev/work",
        "transcript_path": "/t/abc-123.jsonl",
    }
    rec = ClaudeHook.record_event(payload, sidecar_dir=tmp_path, tmux_pane="%7", now=NOW)
    assert rec is not None and rec.state is AgentActivityState.BLOCKED

    back = ClaudeHook.read("abc-123", sidecar_dir=tmp_path)
    assert back is not None
    assert back.state is AgentActivityState.BLOCKED
    assert back.tmux_pane == "%7"
    assert back.cwd == "/home/dev/work"


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


def test_settings_registers_exactly_one_handler_per_event() -> None:
    """The daemon push (#171) rides the ENTRY POINT, not a second `http` handler.

    A registered http handler cannot ask whether the address it names is
    reachable, and from a container's network namespace the daemon's loopback
    never is — so every event reported `connect ECONNREFUSED 127.0.0.1:7421` in
    Claude's own UI, the same user-facing damage the missing binary caused and
    not fixed by fixing the binary (#269). A host with no daemon running showed
    the identical banner. One handler, and the push made by the one process
    that only exists where it can work.
    """
    hooks = ClaudeHook.settings()["hooks"]

    for event in ("Stop", "SessionStart", "SubagentStop"):
        assert [h["type"] for h in hooks[event][0]["hooks"]] == ["command"]
    for entry in hooks["Notification"]:
        assert [h["type"] for h in entry["hooks"]] == ["command"]


def test_the_settings_command_carries_the_daemon_url_as_an_argv() -> None:
    """An argv, not a config read: this is the hottest process in the system, and
    the flag reaching the entry point is also what makes the push structurally
    host-only — the entry point is exactly what a container does not have."""
    command = ClaudeHook.settings()["hooks"]["Stop"][0]["hooks"][0]["command"]

    assert f"--daemon-url {DEFAULT_DAEMON_LOOPBACK_URL}" in command
    assert DEFAULT_DAEMON_LOOPBACK_URL not in ClaudeHook.spool_script(Path("/spool"))


def test_settings_daemon_url_none_renders_a_command_that_pushes_nothing() -> None:
    """The test seam, and the shape an operator gets by pointing the knob nowhere."""
    command = ClaudeHook.settings(daemon_url=None)["hooks"]["Stop"][0]["hooks"][0]["command"]

    assert "--daemon-url" not in command


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


def test_the_daemon_push_runs_after_the_sidecar_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The sidecar is the offline truth; the push only asks the daemon to look
    at it sooner, so an ordering inversion would push a stale read (#171/#269)."""
    seen: list[tuple[str, HookRecord | None]] = []
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ClaudeHook,
        "push",
        classmethod(
            lambda _cls, payload, *, daemon_url: seen.append(
                (daemon_url, ClaudeHook.read(payload["session_id"], sidecar_dir=tmp_path))
            )
        ),
    )
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "p-1"}))
    )

    assert run_hook_from_stdin(["--daemon-url", "http://127.0.0.1:9999"]) == 0

    assert len(seen) == 1
    url, record = seen[0]
    assert url == "http://127.0.0.1:9999"
    assert record is not None and record.state is AgentActivityState.WAITING


def test_no_daemon_url_means_no_push_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The container shape: the flag is never rendered where nothing is reachable."""
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path)
    monkeypatch.setattr(
        ClaudeHook,
        "push",
        classmethod(lambda *_a, **_k: pytest.fail("pushed with no --daemon-url")),
    )
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "p-2"}))
    )

    assert run_hook_from_stdin([]) == 0


def test_a_daemon_that_is_not_listening_is_not_an_error() -> None:
    """The ordinary case, not a failure: the sidecar is already on disk. Raising
    here would fail the hook and put a banner in the agent's UI — which is the
    exact damage the registered http handler used to do on every event."""
    ClaudeHook.push({"session_id": "p-3"}, daemon_url="http://127.0.0.1:1")


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


# ─── adoption evidence inside a container (#242, documented degradation) ─────


def _workspace(tmp_path: Path, *, created_at: datetime) -> WorkspaceState:
    return WorkspaceState(
        id="ws-1",
        title="w",
        repo_root=str(tmp_path),
        branch="feat/x",
        base_branch="main",
        status=WorkspaceStatus.RUNNING,
        worktree_path=str(tmp_path / "wt"),
        tmux_session="grove-w",
        agent_name="claude",
        created_at=created_at,
        updated_at=created_at,
    )


def _sidecar(cwd: Path, *, ts: datetime, tmux_pane: str | None) -> HookRecord:
    return HookRecord(
        session_id="s-live",
        state=AgentActivityState.WORKING,
        event="UserPromptSubmit",
        cwd=str(cwd),
        transcript_path=None,
        tmux_pane=tmux_pane,
        ts=ts,
    )


def test_adoption_falls_back_to_birth_only_without_a_pane(tmp_path: Path) -> None:
    """A containerized agent has no ``$TMUX_PANE`` (the pane lives on the host,
    the agent does not), so its sidecar records none and the pane-verified
    live-here arm cannot fire — adoption degrades to birth-only. Correct rather
    than a bug worth fixing: the cross-tenant hole the pane check closes needs
    two workspaces sharing a cwd, and a container's cwd is its own.

    A session born BEFORE the workspace is therefore not adopted even though
    its sidecar says it was live here after creation..."""
    created = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    state = _workspace(tmp_path, created_at=created)
    cwd = Path(state.worktree_path)
    live_after = _sidecar(cwd, ts=created + timedelta(minutes=5), tmux_pane=None)

    assert not ClaudeHook.adopts(
        state,
        created - timedelta(hours=1),
        candidate=live_after,
        reference_pane=None,
        cwd=cwd,
    )


def test_adoption_still_works_on_birth_without_a_pane(tmp_path: Path) -> None:
    """...and the axis that survives keeps a containerized workspace working:
    a session born after the workspace is adopted with no pane evidence at
    all, which is every session a container launch actually mints."""
    created = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    state = _workspace(tmp_path, created_at=created)
    cwd = Path(state.worktree_path)

    assert ClaudeHook.adopts(
        state,
        created + timedelta(minutes=1),
        candidate=_sidecar(cwd, ts=created + timedelta(minutes=5), tmux_pane=None),
        reference_pane=None,
        cwd=cwd,
    )


# ─── the container arm: spool in, fold on the host (#269) ───────────────────


def _sh(script: str, *, stdin: str = "", env: dict[str, str] | None = None) -> int:
    """Run *script* through a REAL `/bin/sh`, the way Claude Code runs a hook.

    Asserting on the composed text would pin the string and prove nothing about
    the thing that actually has to work — the same lesson `AgentExit` learned by
    discovering its shell suffix wrote nothing at all.
    """
    return subprocess.run(
        ["/bin/sh", "-c", script],
        input=stdin,
        text=True,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
        check=False,
    ).returncode


def test_the_default_command_spools_when_the_entry_point_is_absent(tmp_path: Path) -> None:
    """The bug: a container has no `grove-agent-hook` — it is a console script
    of a package the project's image never installed — so every hook fired
    `/bin/sh: 1: grove-agent-hook: not found` and the whole status axis was
    dead. The fallback moves bytes, which POSIX sh can do without a JSON parser,
    a runtime, or a copy of any rule that would drift from this module.
    """
    spool = tmp_path / "spool"
    spool.mkdir()
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "s-1"})

    assert _sh(ClaudeHook.hook_command(spool), stdin=payload) == 0

    spooled = list(spool.glob("*.json"))
    assert len(spooled) == 1
    assert json.loads(spooled[0].read_text(encoding="utf-8"))["session_id"] == "s-1"


def test_the_default_command_execs_the_entry_point_when_it_exists(tmp_path: Path) -> None:
    """A capability probe, not a runtime branch — nothing here asks whether it is
    in a container, which is what lets ONE rendered settings file serve both."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "ran"
    hook = bindir / ClaudeHook.COMMAND
    hook.write_text(f"#!/bin/sh\ncat > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    spool = tmp_path / "spool"
    spool.mkdir()

    code = _sh(
        ClaudeHook.hook_command(spool),
        stdin='{"hook_event_name": "Stop"}',
        env={"PATH": f"{bindir}:/usr/bin:/bin"},
    )

    assert code == 0
    assert marker.read_text(encoding="utf-8") == '{"hook_event_name": "Stop"}'
    # `exec` replaces the shell, so the real hook's outcome can never fall
    # through to the `||` arm and spool a duplicate.
    assert list(spool.glob("*.json")) == []


def test_a_missing_spool_mount_fails_loudly_instead_of_writing_nowhere(tmp_path: Path) -> None:
    """No `mkdir -p`, deliberately: the spool is a bind mount the create path
    establishes, so an absent one means the mount is gone. Creating it would
    turn that into events written to a container-local directory nothing ever
    reads — a recorder that cannot record, reporting itself healthy."""
    assert _sh(ClaudeHook.hook_command(tmp_path / "never-created"), stdin="{}") != 0


def test_the_settings_default_command_names_the_real_spool_directory() -> None:
    """One rendered file, both namespaces: the path is resolved on the host and
    the container reaches the very same directory through the bind mount."""
    handler = ClaudeHook.settings()["hooks"]["Stop"][0]["hooks"][0]

    assert handler["type"] == "command"
    assert ClaudeHook.COMMAND in handler["command"]
    assert str(paths.agent_hook_spool_dir()) in handler["command"]


def _spool(spool_dir: Path, payload: dict[str, object], *, at: datetime) -> Path:
    path = spool_dir / f"{at.timestamp()}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (at.timestamp(), at.timestamp()))
    return path


def test_drain_folds_a_spooled_payload_through_the_same_state_map(tmp_path: Path) -> None:
    """The fold stays in Python, run once on the host: a containerized session's
    status is the map this module already defines, never a second copy of it."""
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    _spool(spool, {"hook_event_name": "Notification", "session_id": "s-1"}, at=NOW)

    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 1

    record = ClaudeHook.read("s-1", sidecar_dir=tmp_path)
    assert record is not None
    assert record.state is AgentActivityState.BLOCKED
    # The event's own clock, not the drain's: stamping it with the reader's
    # would age every event by however long the reader took to notice it, which
    # `supersedes_poll` reads as staleness.
    assert record.ts == NOW
    assert list(spool.glob("*")) == []


def test_drain_folds_in_event_order_so_the_question_machine_holds(tmp_path: Path) -> None:
    """The pending-question lifecycle is a state machine over the prior sidecar,
    so folding out of order would leave a question standing that a later event
    already cleared."""
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    ask = {
        "hook_event_name": "PreToolUse",
        "session_id": "s-1",
        "tool_name": "AskUserQuestion",
        "tool_use_id": "t-1",
        "tool_input": _ASK_INPUT,
    }
    _spool(spool, ask, at=NOW)
    _spool(
        spool,
        {"hook_event_name": "PostToolUse", "session_id": "s-1"},
        at=NOW + timedelta(seconds=2),
    )

    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 2

    record = ClaudeHook.read("s-1", sidecar_dir=tmp_path)
    assert record is not None
    assert record.question is None  # answered, not still pending


def test_read_drains_so_no_consumer_has_to_remember_to(tmp_path: Path) -> None:
    """Four independent readers (the blend, its question cross-check, session
    adoption, `answer_question`) already call `read`; this tree has watched the
    same two-line convention get missed one site at a time until a workspace lit
    up only partially. Owning it here leaves a reader nothing to get wrong."""
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    _spool(spool, {"hook_event_name": "SessionStart", "session_id": "s-1"}, at=NOW)

    record = ClaudeHook.read("s-1", sidecar_dir=tmp_path)

    assert record is not None
    assert record.state is AgentActivityState.WORKING


def test_drain_is_a_no_op_with_no_spool_directory(tmp_path: Path) -> None:
    """The host case, on the hot read path: one directory-listing syscall."""
    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 0


def test_drain_drops_a_malformed_entry_without_blocking_the_queue(tmp_path: Path) -> None:
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    (spool / "1.json").write_text("{not json", encoding="utf-8")
    _spool(spool, {"hook_event_name": "Stop", "session_id": "s-1"}, at=NOW)

    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 1
    assert ClaudeHook.read("s-1", sidecar_dir=tmp_path) is not None
    assert list(spool.glob("*")) == []


def test_drain_ignores_a_partially_written_payload(tmp_path: Path) -> None:
    """The shim writes `.tmp` then renames, so a drain racing the redirect never
    reads half a payload."""
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    (spool / "1.tmp").write_text('{"hook_event_name": "Sto', encoding="utf-8")

    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 0
    assert (spool / "1.tmp").exists()


# ─── sub-agent fleet: keyed by (session_id, agent_id), never the main sidecar ─


def _subagent_event(event: str, agent_id: str = "a-1", **extra: object) -> dict:
    return {
        "hook_event_name": event,
        "session_id": "s",
        "agent_id": agent_id,
        "agent_type": "general-purpose",
        **extra,
    }


def test_subagent_event_never_touches_the_main_thread_sidecar(tmp_path: Path) -> None:
    """The bug this whole record type exists to close: before the split, a
    sub-agent's PreToolUse was keyed identically to the main thread's and
    silently overwrote the top-level session's pushed status while the
    sub-agent ran."""
    # A settled main-thread state first (Stop → WAITING).
    ClaudeHook.record_event(_event("Stop"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW)
    main_before = ClaudeHook.read("s", sidecar_dir=tmp_path)
    assert main_before is not None and main_before.state is AgentActivityState.WAITING

    # A sub-agent PreToolUse under the SAME session_id must not flip it.
    ClaudeHook.record_event(
        _subagent_event("PreToolUse", tool_name="Bash"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW + timedelta(seconds=1),
    )

    main_after = ClaudeHook.read("s", sidecar_dir=tmp_path)
    assert main_after is not None
    assert main_after.state is AgentActivityState.WAITING
    assert main_after.event == "Stop"


def test_record_event_returns_none_for_a_subagent_scoped_payload(tmp_path: Path) -> None:
    """`record_event`'s return is `HookRecord | None` — a sub-agent event is
    written through the OTHER seam (`list_subagents`/`read_subagent`)."""
    rec = ClaudeHook.record_event(
        _subagent_event("SubagentStart"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    assert rec is None


def test_subagent_start_creates_a_record_and_pins_started_at(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )

    rec = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert rec is not None
    assert rec.session_id == "s"
    assert rec.agent_id == "a-1"
    assert rec.agent_type == "general-purpose"
    assert rec.state is AgentActivityState.WORKING
    assert rec.started_at == NOW
    assert rec.last_event_at == NOW
    assert rec.current_tool is None


def test_subagent_pretooluse_sets_current_tool_and_posttooluse_clears_it(
    tmp_path: Path,
) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    ClaudeHook.record_event(
        _subagent_event("PreToolUse", tool_name="Bash"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW + timedelta(seconds=1),
    )

    running = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert running is not None
    assert running.state is AgentActivityState.WORKING
    assert running.current_tool == "Bash"
    # `started_at` is carried forward from the SubagentStart write, not reset.
    assert running.started_at == NOW

    ClaudeHook.record_event(
        _subagent_event("PostToolUse", tool_name="Bash"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW + timedelta(seconds=2),
    )
    resolved = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert resolved is not None
    assert resolved.current_tool is None
    assert resolved.state is AgentActivityState.WORKING  # still running, between calls


def test_subagent_stop_settles_to_waiting_and_captures_the_final_message(
    tmp_path: Path,
) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    ClaudeHook.record_event(
        _subagent_event(
            "SubagentStop",
            last_assistant_message="Found the bug in _blend and fixed it.",
        ),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW + timedelta(seconds=30),
    )

    rec = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert rec is not None
    assert rec.state is AgentActivityState.WAITING
    assert rec.last_message == "Found the bug in _blend and fixed it."
    assert rec.current_tool is None


def test_subagent_stop_message_is_capped(tmp_path: Path) -> None:
    long_message = "x" * 1000
    ClaudeHook.record_event(
        _subagent_event("SubagentStop", last_assistant_message=long_message),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )
    rec = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert rec is not None
    assert rec.last_message is not None
    assert len(rec.last_message) <= 500


def test_subagent_notification_maps_to_blocked(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        _subagent_event("Notification"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    rec = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert rec is not None and rec.state is AgentActivityState.BLOCKED


def test_subagent_unmapped_event_is_ignored(tmp_path: Path) -> None:
    """Defensive: an event this map doesn't recognize (a main-thread-only
    event that should never carry an `agent_id`) writes nothing."""
    ClaudeHook.record_event(
        _subagent_event("UserPromptSubmit"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    assert ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path) is None


def test_read_subagent_missing_returns_none(tmp_path: Path) -> None:
    assert ClaudeHook.read_subagent("s", "nope", sidecar_dir=tmp_path) is None


def test_list_subagents_returns_every_agent_oldest_started_first(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart", agent_id="a-2"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW + timedelta(seconds=5),
    )
    ClaudeHook.record_event(
        _subagent_event("SubagentStart", agent_id="a-1"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )

    records = ClaudeHook.list_subagents("s", sidecar_dir=tmp_path)

    assert [r.agent_id for r in records] == ["a-1", "a-2"]
    assert all(r.session_id == "s" for r in records)


def test_list_subagents_is_scoped_to_one_session(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart"), sidecar_dir=tmp_path, tmux_pane=None, now=NOW
    )
    ClaudeHook.record_event(
        {
            "hook_event_name": "SubagentStart",
            "session_id": "other-session",
            "agent_id": "a-1",
            "agent_type": "general-purpose",
        },
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )

    assert len(ClaudeHook.list_subagents("s", sidecar_dir=tmp_path)) == 1
    assert len(ClaudeHook.list_subagents("other-session", sidecar_dir=tmp_path)) == 1


def test_list_subagents_with_no_fleet_is_empty(tmp_path: Path) -> None:
    """The overwhelming common case: a session that never spawned a
    sub-agent costs one missing-directory stat."""
    assert ClaudeHook.list_subagents("s", sidecar_dir=tmp_path) == ()


def test_list_subagents_drops_a_malformed_entry(tmp_path: Path) -> None:
    ClaudeHook.record_event(
        _subagent_event("SubagentStart", agent_id="a-1"),
        sidecar_dir=tmp_path,
        tmux_pane=None,
        now=NOW,
    )
    bad_dir = tmp_path / "subagents" / "s"
    (bad_dir / "a-2.json").write_text("{not json", encoding="utf-8")

    records = ClaudeHook.list_subagents("s", sidecar_dir=tmp_path)

    assert [r.agent_id for r in records] == ["a-1"]


def test_subagent_hook_record_from_json_rejects_naive_timestamp() -> None:
    assert (
        SubagentHookRecord.from_json(
            {
                "session_id": "s",
                "agent_id": "a-1",
                "agent_type": None,
                "state": "working",
                "event": "SubagentStart",
                "started_at": "2026-06-01T12:00:00",  # no offset → naive
                "last_event_at": "2026-06-01T12:00:00",
            }
        )
        is None
    )


def test_subagent_hook_record_round_trips_through_json() -> None:
    rec = SubagentHookRecord(
        session_id="s",
        agent_id="a-1",
        agent_type="general-purpose",
        state=AgentActivityState.WORKING,
        event="PreToolUse",
        started_at=NOW,
        last_event_at=NOW + timedelta(seconds=1),
        current_tool="Bash",
        last_message=None,
    )
    assert SubagentHookRecord.from_json(rec.to_json()) == rec


def test_drain_folds_a_spooled_subagent_payload_into_its_own_sidecar(tmp_path: Path) -> None:
    """The container arm: a containerized sub-agent's hook can only spool, and
    the fold routes it through the SAME `record_event` a direct host write
    would use — no second sub-agent shape to keep in step."""
    spool = paths.agent_hook_spool_dir(tmp_path)
    spool.mkdir(parents=True)
    _spool(spool, _subagent_event("SubagentStart"), at=NOW)

    assert ClaudeHook.drain(sidecar_dir=tmp_path) == 1

    rec = ClaudeHook.read_subagent("s", "a-1", sidecar_dir=tmp_path)
    assert rec is not None
    assert rec.state is AgentActivityState.WORKING
    assert rec.started_at == NOW
