"""Manager steering policy: send_message / interrupt.

Pins the policy seams: injection goes to the `pane_target`-resolved target
via `tmux.send_text`, every refusal is a typed error, and — critically —
no refusal path ever reaches the injection seam (FakeTmux.sent_texts is
the spy). The mewbo arm is exercised through the constructor's
`mewbo_client` injection seam — never a patched private.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import (
    AgentSessionNotFound,
    CapabilityUnavailable,
    PaneNotFound,
    SteeringUnsupported,
    WorkspaceStateError,
)
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FakeTmux


class FakeMewboClient:
    """Call-recording stand-in for the steering slice of ``MewboClient``."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.interrupts: list[str] = []

    def send_message(self, session_id: str, text: str) -> None:
        self.messages.append((session_id, text))

    def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)


@pytest.fixture
def fake_mewbo() -> FakeMewboClient:
    return FakeMewboClient()


@pytest.fixture
def manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    fake_mewbo: FakeMewboClient,
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=store,
        mewbo_client=fake_mewbo,  # type: ignore[arg-type]  # steering-slice duck type
    )


def _create(manager: WorkspaceManager, title: str = "steer me") -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _as_mewbo(manager: WorkspaceManager, state: WorkspaceState) -> None:
    """Persist the record re-kinded to mewbo, keeping the minted session id.

    Cheaper than a full mewbo create (covered by test_mewbo_launch) while
    still exercising the real dispatch: steering keys on the persisted
    ``agent_kind`` + ``agent_session_id``, both present after this.
    """
    manager.store.save(replace(state, agent_kind="mewbo"))


# ─── send_message ────────────────────────────────────────────────────────────


def test_send_message_injects_at_resolved_pane_and_emits_event(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.send_message(state.id, "please run the tests")

    # Injection lands at the pane_target-resolved agent window, never a
    # hard-coded target.
    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "please run the tests")]
    sent = [e for e in events if e.kind == "message_sent"]
    assert len(sent) == 1
    assert sent[0].workspace_id == state.id
    assert sent[0].detail["text_length"] == str(len("please run the tests"))
    assert sent[0].detail["target"] == f"{state.tmux_session}:agent"
    # Audit trail carries length, never content — steering text can hold secrets.
    assert "please run the tests" not in sent[0].detail.values()


def test_send_message_falls_back_to_non_shell_window(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """pane_target policy reuse: a renamed agent window still receives the steer."""
    state = _create(manager)
    fake_tmux.windows[state.tmux_session] = ["shell", "renamed-agent"]

    manager.send_message(state.id, "hello")

    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:renamed-agent", "hello")]


def test_send_message_refuses_paused_and_never_injects(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    manager.pause(state.id)

    with pytest.raises(WorkspaceStateError, match="cannot send message"):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_refuses_offline_and_never_injects(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    fake_tmux.sessions.clear()  # session vanished externally → OFFLINE

    with pytest.raises(WorkspaceStateError, match="cannot send message"):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_refuses_when_no_pane_resolves(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    # Session is up but reports zero windows (reorganized/emptied externally).
    fake_tmux.windows[state.tmux_session] = []

    with pytest.raises(PaneNotFound):
        manager.send_message(state.id, "hello")
    assert fake_tmux.sent_texts == []


def test_send_message_mewbo_kind_steers_via_api(
    manager: WorkspaceManager, fake_tmux: FakeTmux, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    _as_mewbo(manager, state)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.send_message(state.id, "hello")

    # The remote arm posts to the session API and never touches tmux.
    assert fake_mewbo.messages == [(str(state.agent_session_id), "hello")]
    assert fake_tmux.sent_texts == []
    sent = [e for e in events if e.kind == "message_sent"]
    assert len(sent) == 1
    assert sent[0].detail["target"] == f"mewbo:{state.agent_session_id}"
    assert sent[0].detail["text_length"] == str(len("hello"))


def test_send_message_mewbo_without_session_id_is_typed_error(
    manager: WorkspaceManager, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    manager.store.save(replace(state, agent_kind="mewbo", agent_session_id=None))

    with pytest.raises(AgentSessionNotFound):
        manager.send_message(state.id, "hello")
    assert fake_mewbo.messages == []


# ─── interrupt ───────────────────────────────────────────────────────────────


def test_interrupt_refuses_tmux_hosted_agents(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)

    # No safe generic interrupt: a cancel keystroke into an arbitrary CLI
    # is not a contract, so claude_code/generic always refuse.
    with pytest.raises(SteeringUnsupported, match="interrupt"):
        manager.interrupt(state.id)
    assert fake_tmux.sent_texts == []


def test_interrupt_mewbo_kind_interrupts_via_api(
    manager: WorkspaceManager, fake_tmux: FakeTmux, fake_mewbo: FakeMewboClient
) -> None:
    state = _create(manager)
    _as_mewbo(manager, state)

    manager.interrupt(state.id)

    assert fake_mewbo.interrupts == [str(state.agent_session_id)]
    assert fake_tmux.sent_texts == []


# ─── control triggers: invoke_control / switch_model ────────────────────────


def test_invoke_control_delivers_slash_command_and_emits_event(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A command/skill trigger composes ``/name`` and reuses the send_message
    pane path — one delivery mechanism, plus a distinct ``control_invoked`` event
    carrying only the control NAME (never trailing args)."""
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.invoke_control(state.id, "review")

    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "/review")]
    invoked = [e for e in events if e.kind == "control_invoked"]
    assert len(invoked) == 1
    assert invoked[0].detail["control"] == "review"


def test_invoke_control_strips_leading_slash(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    manager.invoke_control(state.id, "/brainstorming")
    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "/brainstorming")]


def test_switch_model_delivers_model_command(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.switch_model(state.id, "opus")

    assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "/model opus")]
    invoked = [e for e in events if e.kind == "control_invoked"]
    # Only the control name ("model"), never the model id, rides the audit event.
    assert invoked[0].detail["control"] == "model"


def test_switch_model_empty_id_is_typed_capability_error(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = _create(manager)
    with pytest.raises(CapabilityUnavailable):
        manager.switch_model(state.id, "   ")
    assert fake_tmux.sent_texts == []


def test_invoke_control_unsupported_kind_refuses_before_delivery(
    manager: WorkspaceManager, fake_tmux: FakeTmux, fake_mewbo: FakeMewboClient
) -> None:
    """A remote/shell kind has no slash-control surface: CapabilityUnavailable,
    and — critically — the gate fires BEFORE any delivery (neither tmux nor the
    remote arm is touched)."""
    state = _create(manager)
    _as_mewbo(manager, state)

    with pytest.raises(CapabilityUnavailable):
        manager.invoke_control(state.id, "review")
    with pytest.raises(CapabilityUnavailable):
        manager.switch_model(state.id, "opus")
    assert fake_tmux.sent_texts == []
    assert fake_mewbo.messages == []


# ─── session controls enumeration: manager composition ──────────────────────


def test_session_controls_composes_scan_with_model_catalog(
    manager: WorkspaceManager,
) -> None:
    """The manager folds the adapter's fs scan together with the shared model
    catalog (resolve_models) and the permission posture — one composed surface."""
    state = _create(manager)
    cmd_dir = state.agent_cwd / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "review.md").write_text("body", encoding="utf-8")

    controls = manager.session_controls(state.id)

    assert "review" in {c.name for c in controls.commands}
    # Model catalog is the manager's contribution (claude's stable aliases),
    # never the adapter's fs scan.
    assert controls.models == ("fable", "opus", "sonnet", "haiku")
    assert controls.current_model is None  # no transcript minted yet
    assert controls.permission_mode is None  # permission answering off by default


# ─── latest-todo projection: manager resolution ─────────────────────────────


def test_latest_todo_raises_agent_session_not_found_when_sessionless(
    manager: WorkspaceManager,
) -> None:
    state = _create(manager)
    manager.store.save(replace(state, agent_session_id=None))
    with pytest.raises(AgentSessionNotFound):
        manager.latest_todo(state.id)


def test_latest_todo_returns_none_with_no_transcript_yet(manager: WorkspaceManager) -> None:
    """A minted session id with nothing written for it yet (the STARTING
    window) degrades to ``None`` — never raises."""
    state = _create(manager)
    assert manager.latest_todo(state.id) is None


def test_latest_todo_reads_through_the_resolved_adapter(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The manager resolves the workspace's session id + cwd and hands off to
    the SAME projection the daemon route and the issueops publisher read —
    proven end to end through a real ``ClaudeCodeAdapter`` transcript."""
    claude_home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = _create(manager)
    assert state.agent_session_id is not None
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(state.agent_cwd)
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        "\n".join(
            [
                '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
                '"isSidechain":false,"message":{"role":"user","content":"track it"}}',
                '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
                '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1",'
                '"role":"assistant","stop_reason":"tool_use","content":['
                '{"type":"tool_use","id":"tw1","name":"TodoWrite","input":{"todos":['
                '{"content":"Ship it","status":"in_progress"}]}}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    todo = manager.latest_todo(state.id)

    assert todo is not None
    assert [(i.content, i.status) for i in todo.items] == [("Ship it", "in_progress")]
