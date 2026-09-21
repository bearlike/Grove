"""A NATIVE workspace: Grove owns the session and every steer takes the control channel.

The create-time roster decision is persisted on the record (`WorkspaceState.native`)
and read back by the launch composer and every steer verb, so a roster edit after
create never re-decides for a running workspace. The steer client is the
constructor's ``native_steer`` seam — never a patched private.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import paths, tmux
from grove.core.agents.hook import ClaudeHook, PendingQuestion
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.questions import QuestionAnswerItem, QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceStateView
from grove.core.errors import (
    ResumeNotSupported,
    SteeringUnsupported,
    TmuxError,
    WorkspaceStateError,
)
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import (
    LIVE_STATUSES,
    Runtime,
    WorkspaceState,
    WorkspaceStatus,
    ensure_can_respawn,
)
from tests.conftest import FakeTmux

pytestmark = pytest.mark.usefixtures("native_roster", "fake_tmux")


class FakeNativeSteer:
    # `connected` is the optional `owner_connected` probe the manager reads
    # through `getattr`: False is a worker that died, which is what the revive
    # path exists for. Default True so every pre-existing test is unaffected.
    connected = True

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.interrupts: list[str] = []
        self.models: list[tuple[str, str]] = []
        self.answers: list[tuple[str, dict[str, object]]] = []
        self.compactions: list[str] = []
        self.controls: list[tuple[str, str]] = []

    def owner_connected(self, state: WorkspaceState) -> bool:
        del state
        return self.connected

    def send_message(self, state: WorkspaceState, text: str) -> None:
        self.messages.append((state.id, text))

    def interrupt(self, state: WorkspaceState) -> None:
        self.interrupts.append(state.id)

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self.models.append((state.id, model))

    def answer(self, state: WorkspaceState, plan: str) -> None:
        self.answers.append((state.id, json.loads(plan)))

    def compact(self, state: WorkspaceState) -> None:
        self.compactions.append(state.id)

    def invoke_control(self, state: WorkspaceState, name: str) -> None:
        self.controls.append((state.id, name))


@pytest.fixture
def steer() -> FakeNativeSteer:
    return FakeNativeSteer()


@pytest.fixture
def manager(tmp_repo: Path, tmp_path: Path, steer: FakeNativeSteer) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        native_steer=steer,
    )


def test_the_builtin_claude_creates_a_native_workspace_and_launches_the_worker(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The persisted flag comes from the roster; the pane runs Grove's worker,
    not `claude`, and its config carries the task rather than the argv."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="native"))
    assert state.native is True
    assert manager.store.get(state.id).native is True
    assert WorkspaceStateView.from_state(state).native is True
    [(session, cmd)] = fake_tmux.layouts
    [(_, decoration)] = fake_tmux.launch_decorations
    assert session == state.tmux_session
    assert "grove.core.native_worker" in cmd
    assert decoration[0] == "--config"
    assert cmd.endswith(" -m grove.core.native_worker")  # provider argv stays in config


def test_the_terminal_twin_creates_an_ordinary_pane_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude-terminal", title="tui"))
    assert state.native is False
    [(_, cmd)] = fake_tmux.layouts
    assert cmd == "claude"


def test_steer_verbs_take_the_control_channel_and_never_the_pane(
    manager: WorkspaceManager, fake_tmux: FakeTmux, steer: FakeNativeSteer
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="native"))
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.send_message(state.id, "hello")
    manager.interrupt(state.id)
    manager.switch_model(state.id, " opus ")

    assert steer.messages == [(state.id, "hello")]
    assert steer.interrupts == [state.id]
    assert steer.models == [(state.id, "opus")]
    assert fake_tmux.sent_texts == []
    assert fake_tmux.escapes == []
    kinds = [(e.kind, e.detail.get("control")) for e in events if e.kind != "created"]
    assert ("message_sent", None) in kinds
    assert ("control_invoked", "model") in kinds
    # A named key is terminal input, and a native pane shows the worker's log.
    with pytest.raises(SteeringUnsupported, match="live terminal"):
        manager.send_keys(state.id, tmux.SendKey.TAB)


def test_native_invoke_control_uses_the_provider_operation_not_a_slash_message(
    manager: WorkspaceManager, fake_tmux: FakeTmux, steer: FakeNativeSteer
) -> None:
    """OpenCode commands/compaction have their own HTTP routes; native dispatch
    must not type a plausible `/command` string into the session."""
    state = manager.create(CreateWorkspaceRequest(agent_name="opencode", title="native"))

    manager.invoke_control(state.id, "review --staged")
    manager.invoke_control(state.id, "compact")

    assert steer.controls == [(state.id, "review --staged")]
    assert steer.compactions == [state.id]
    assert steer.messages == []
    assert fake_tmux.sent_texts == []


def test_the_record_decides_not_the_roster(
    manager: WorkspaceManager, fake_tmux: FakeTmux, steer: FakeNativeSteer
) -> None:
    """A workspace created native stays native when the roster flips — and the
    reverse: a terminal workspace never starts taking the control channel."""
    native = manager.create(CreateWorkspaceRequest(agent_name="claude", title="a"))
    terminal = manager.create(CreateWorkspaceRequest(agent_name="claude-terminal", title="b"))
    # Swap the two records' agent names so each now points at the OTHER entry.
    manager.store.save(replace(manager.store.get(native.id), agent_name="claude-terminal"))
    manager.store.save(replace(manager.store.get(terminal.id), agent_name="claude"))

    manager.interrupt(native.id)
    manager.interrupt(terminal.id)

    assert steer.interrupts == [native.id]
    assert fake_tmux.escapes == [f"{terminal.tmux_session}:agent"]


def test_native_refuses_pause_and_resume_by_id(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="native"))
    with pytest.raises(WorkspaceStateError, match="cannot be paused"):
        manager.pause(state.id)
    with pytest.raises(ResumeNotSupported):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude", title="again", resume_session_id=str(state.agent_session_id)
            )
        )


def test_a_pre_native_record_loads_as_a_terminal_workspace(
    manager: WorkspaceManager, tmp_path: Path
) -> None:
    """A record written before the field existed carries no `native` key and
    must decode as what it was: the interactive terminal."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude-terminal", title="old"))
    path = tmp_path / "state.json"
    raw = path.read_text()
    assert '"native": false' in raw
    path.write_text(raw.replace('"native": false, ', "").replace(', "native": false', ""))
    assert JsonWorkspaceStore(path=path).get(state.id).native is False


def test_a_native_ask_is_answered_as_structure_never_as_prose(
    manager: WorkspaceManager, fake_tmux: FakeTmux, steer: FakeNativeSteer
) -> None:
    """The owner holds the provider's own ask, so the human's answer goes back as
    the answer frame (indexes + text per question) rather than a restated
    message — and the plan is validated against the captured questions first."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    sid = str(state.agent_session_id)
    ClaudeHook.record_native_question(
        sid,
        PendingQuestion(
            tool_use_id="toolu_q",
            tool_name="AskUserQuestion",
            tool_input={
                "questions": [
                    {
                        "question": "Pick a color",
                        "header": "Color",
                        "options": [{"label": "Red"}, {"label": "Blue"}],
                    }
                ]
            },
            asked_at=datetime.now(UTC),
        ),
        sidecar_dir=paths.agent_sidecar_dir(),
    )

    manager.answer_question(
        state.id,
        QuestionAnswerRequest(
            session_id=sid,
            tool_use_id="toolu_q",
            answers=[QuestionAnswerItem(selected_indexes=[1], text="and make it dark")],
        ),
    )

    [(ws, plan)] = steer.answers
    assert ws == state.id
    assert plan == {
        "session_id": sid,
        "tool_use_id": "toolu_q",
        "answers": [{"indexes": [1], "text": "and make it dark"}],
    }
    assert steer.messages == []  # never restated as a message
    assert fake_tmux.escapes == [] and fake_tmux.sent_texts == []


@pytest.mark.parametrize(
    ("agent", "choice", "expected"),
    [
        ("claude", None, True),  # entry default: native
        ("claude", False, False),  # per-create override to the terminal
        ("claude-terminal", None, False),  # entry default: terminal
        ("claude-terminal", True, True),  # per-create override to native
        ("shell", True, False),  # no protocol to own: the kind gate wins
    ],
)
def test_the_request_overrides_the_entry_default_within_the_kind_gate(
    manager: WorkspaceManager, agent: str, choice: bool | None, expected: bool
) -> None:
    """`CreateWorkspaceRequest.native` is a per-launch choice over the roster
    entry's default — any profile runs either way with no config edit — and a
    kind with no native protocol ignores it."""
    state = manager.create(CreateWorkspaceRequest(agent_name=agent, title="t", native=choice))
    assert state.native is expected


def test_attach_to_a_native_workspace_is_a_read_only_viewer(manager: WorkspaceManager) -> None:
    """The pane is the worker's protocol log, so a stray Ctrl-C must never reach it.

    `attach -r` on the host arm; the switch-client arm stays as it is because
    `switch-client -r` TOGGLES rather than sets, and the instruction says so
    (`read_only`) for every client that renders a command.
    """
    native = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    terminal = manager.create(CreateWorkspaceRequest(agent_name="claude-terminal", title="t"))

    viewer = manager.attach(native.id)
    assert viewer.read_only is True
    assert viewer.terminal_argv() == ["tmux", "attach", "-r", "-t", native.tmux_session]
    writer = manager.attach(terminal.id)
    assert writer.read_only is False
    assert writer.terminal_argv() == ["tmux", "attach", "-t", terminal.tmux_session]
    assert replace(viewer, inside_outer_tmux=True).terminal_argv() == [
        "tmux",
        "switch-client",
        "-t",
        native.tmux_session,
    ]


def test_respawn_continues_the_native_session_rather_than_minting_a_new_one(
    manager: WorkspaceManager, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed worker must not cost the conversation.

    The session lives in the transcript, not in the dead child, and both
    providers continue it (measured: `claude -p --resume <id>` recalls a
    codeword set before its owner exited). So respawn keeps the pinned id and
    composes the provider's resume form; the amnesiac relaunch it used to do
    was the whole reason a dead native workspace was unrecoverable.

    Materialization is faked at the same seam `resume()` gates on, because the
    predicate — not the transcript's bytes — is what decides continue-vs-mint.
    """
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="n", initial_prompt="go")
    )
    pinned = state.agent_session_id
    assert pinned
    monkeypatch.setattr(
        WorkspaceManager, "_pinned_session_materialized", lambda self, agent, state: True
    )
    fake_tmux.sessions.discard(state.tmux_session)
    fake_tmux.launch_decorations.clear()

    respawned = manager.respawn(state.id)

    assert respawned.agent_session_id == pinned, "respawn must continue, not mint"
    [(_, decoration)] = fake_tmux.launch_decorations
    worker = json.loads(Path(decoration[1]).read_text(encoding="utf-8"))
    assert "--resume" in worker["command"] and pinned in worker["command"]
    # Nothing to re-deliver: the resumed conversation already holds the task.
    assert worker["initial_prompt"] == ""


def test_respawn_mints_when_the_pinned_session_never_materialized(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A pinned id with no transcript is a DEAD POINTER, and asking a provider
    to resume one fails the launch where minting simply starts.

    The mirror of the test above, and the reason the predicate is there at all:
    "continue if you can" has to answer "no" for a session that never wrote a
    byte, or respawn stops being a recovery verb for the case it was built for.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    fake_tmux.sessions.discard(state.tmux_session)
    fake_tmux.launch_decorations.clear()

    respawned = manager.respawn(state.id)

    assert respawned.agent_session_id != state.agent_session_id
    [(_, decoration)] = fake_tmux.launch_decorations
    worker = json.loads(Path(decoration[1]).read_text(encoding="utf-8"))
    assert "--resume" not in worker["command"]
    assert "--session-id" in worker["command"]


def test_a_message_to_a_dead_native_session_revives_it(
    manager: WorkspaceManager, steer: FakeNativeSteer, fake_tmux: FakeTmux
) -> None:
    """The remedy for "no connected native owner" is mechanical, so Grove takes it.

    A native owner is an ordinary process: a reboot or an OOM kill takes it and
    every steer then 409s until somebody respawns by hand — a dead end for the
    one action a person wants, now that respawn CONTINUES the conversation.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    steer.connected = False
    fake_tmux.sessions.discard(state.tmux_session)

    manager.send_message(state.id, "still there?")

    assert steer.messages == [(state.id, "still there?")]
    assert manager.store.get(state.id).status is WorkspaceStatus.RUNNING
    # Relaunched: the revive is a real respawn, not a retry against the corpse.
    assert any("grove.core.native_worker" in cmd for _, cmd in fake_tmux.layouts[1:])


def test_a_live_native_session_is_never_restarted_under_the_agent(
    manager: WorkspaceManager, steer: FakeNativeSteer, fake_tmux: FakeTmux
) -> None:
    """The gate is "no owner", never "native" — reviving a working agent
    mid-turn would destroy the very work the message is steering."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    launches = len(fake_tmux.layouts)

    manager.send_message(state.id, "carry on")

    assert steer.messages == [(state.id, "carry on")]
    assert len(fake_tmux.layouts) == launches, "a connected owner must not be respawned"


@pytest.mark.parametrize("promotable", [False, True])
def test_a_reconnecting_native_worker_is_not_respawned(
    manager: WorkspaceManager,
    steer: FakeNativeSteer,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    promotable: bool,
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    steer.connected = False
    launches = len(fake_tmux.layouts)
    monkeypatch.setattr(WorkspaceManager, "_is_promotable", lambda self, state: promotable)

    manager.send_message(state.id, "carry on when reconnected")

    assert len(fake_tmux.layouts) == launches
    assert manager.peek(state.id).state.status in LIVE_STATUSES
    assert manager.store.get(state.id).agent_session_id == state.agent_session_id


@pytest.mark.parametrize("root", [False, True])
def test_explicit_respawn_restarts_a_live_native_owner_without_removing_work(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    root: bool,
) -> None:
    request = CreateWorkspaceRequest(agent_name="claude", title="recover", runtime="host")
    if root:
        request = request.model_copy(update={"branch_plan": RootBranch()})
    state = manager.create(request)
    worktree = Path(state.worktree_path)
    unfinished = worktree / "unfinished.txt"
    unfinished.write_text("keep this uncommitted work", encoding="utf-8")
    monkeypatch.setattr(
        WorkspaceManager, "_pinned_session_materialized", lambda self, agent, state: True
    )
    launches = len(fake_tmux.layouts)

    recovered = manager.respawn(state.id)

    assert len(fake_tmux.layouts) == launches + 1
    assert recovered.agent_session_id == state.agent_session_id
    assert recovered.branch == state.branch
    assert recovered.worktree_path == state.worktree_path
    assert unfinished.read_text(encoding="utf-8") == "keep this uncommitted work"
    _, decoration = fake_tmux.launch_decorations[-1]
    config = json.loads(Path(decoration[1]).read_text(encoding="utf-8"))
    assert "--resume" in config["command"]
    assert config["initial_prompt"] == ""


def test_explicit_native_recovery_refuses_failed_runtime_teardown(
    manager: WorkspaceManager, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="recover", runtime="host")
    )
    launches = len(fake_tmux.layouts)

    def failed_stop(name: str, *, wait_for_exit: bool = False) -> None:
        assert wait_for_exit, "native recovery must wait for the old provider to exit"
        raise TmuxError(f"could not stop {name}")

    monkeypatch.setattr(tmux, "kill_session", failed_stop)
    with pytest.raises(TmuxError, match="could not stop"):
        manager.respawn(state.id)
    assert len(fake_tmux.layouts) == launches
    assert manager.store.get(state.id).agent_session_id == state.agent_session_id


def test_explicit_respawn_does_not_restart_a_live_terminal_owner(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude-terminal", title="terminal", runtime="host")
    )
    launches = len(fake_tmux.layouts)
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        manager.respawn(state.id)
    assert len(fake_tmux.layouts) == launches


@pytest.mark.parametrize(
    "status",
    [WorkspaceStatus.PAUSED, WorkspaceStatus.ERROR, WorkspaceStatus.ORPHANED],
)
def test_native_recovery_does_not_bypass_non_live_state_guards(
    manager: WorkspaceManager, status: WorkspaceStatus
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="recover", runtime="host")
    )
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        ensure_can_respawn(replace(state, status=status))


def test_live_container_native_owner_is_not_claimed_as_restartable(
    manager: WorkspaceManager,
) -> None:
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="recover", runtime="host")
    )
    with pytest.raises(WorkspaceStateError, match="expected offline"):
        ensure_can_respawn(replace(state, status=WorkspaceStatus.ACTIVE, runtime=Runtime.CONTAINER))


def test_a_dead_native_worker_reads_offline_so_respawn_is_offered(
    manager: WorkspaceManager, tmp_path: Path
) -> None:
    """A live tmux session says nothing about the WORKER inside it.

    The worker is the pane's own command, so when it exits tmux keeps the
    session and the pane falls back to a shell — the workspace read ACTIVE and
    decayed to IDLE while its session was gone. That hid `respawn`, which is
    gated on OFFLINE, so the one verb that rebuilds the session was
    unreachable through every surface: the Controls card offered Pause and
    Kill for a workspace that was already dead.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    assert manager.peek(state.id).state.status in LIVE_STATUSES
    # The recorder the launch installs; a record means the command returned.
    exit_path = paths.agent_exit_path(state.id)
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text("1\n", encoding="utf-8")

    assert manager.peek(state.id).state.status is WorkspaceStatus.OFFLINE


def test_a_clean_native_exit_is_also_offline(manager: WorkspaceManager) -> None:
    """Zero is still a dead owner, unlike the ERROR blend one layer up.

    `AgentExit.failed` distinguishes "died" from "quit" for the AGENT-ACTIVITY
    axis, where a clean quit is not an error. Here the question is different:
    the workspace has no process holding its control channel either way, and
    respawn is the remedy for both.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="n"))
    exit_path = paths.agent_exit_path(state.id)
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text("0\n", encoding="utf-8")

    assert manager.peek(state.id).state.status is WorkspaceStatus.OFFLINE


def test_a_terminal_workspace_is_untouched_by_the_owner_check(
    manager: WorkspaceManager,
) -> None:
    """The gate is `native`: a terminal agent that exits leaves a live pane the
    user can still read and re-launch in, which is what IDLE has always meant."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude-terminal", title="t"))
    exit_path = paths.agent_exit_path(state.id)
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text("1\n", encoding="utf-8")

    assert manager.peek(state.id).state.status in LIVE_STATUSES
