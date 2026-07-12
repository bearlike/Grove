"""Headless provisioning (#146): a workspace whose agent runs as a detached
process, tracked purely from the transcript/adapter with no tmux pane.

Three surfaces:

* ``HeadlessLaunchBackend`` + ``grove.core.process.spawn_detached`` — the paneless
  runtime spawns the assembled command as a detached OS process with the hermetic
  env applied, and reports ``provides_pane = False``.
* ``WorkspaceManager`` gates every tmux-only path on that capability: create does
  no tmux work, reconciliation marks the workspace ACTIVE (never OFFLINE for a
  missing session), respawn relaunches without the OFFLINE gate, and the pane
  snapshot degrades to empty rather than reaching for a pane that isn't there.
* ``send_message`` / ``interrupt`` / ``answer_question`` route over the native
  channel (#172) instead of raising — the paneless twin of the mewbo remote arm.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import process as process_mod
from grove.core.activity import ActivityService
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.questions import QuestionAnswerItem, QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import ProcessError, WorkspaceStateError
from grove.core.launch import HeadlessLaunchBackend, LaunchSpec, TmuxLaunchBackend
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceStatus
from tests.conftest import FakeTmux


class FakeNativeSteer:
    """Records native-steer deliveries instead of POSTing to a channel (the DI seam)."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.interrupts: list[str] = []

    def send_message(self, session_id: str, text: str) -> None:
        self.messages.append((session_id, text))

    def interrupt(self, session_id: str) -> None:
        self.interrupts.append(session_id)


class FakeHeadlessBackend:
    """A paneless backend that records specs instead of spawning (the DI seam).

    Mirrors ``HeadlessLaunchBackend``'s capability sentinel so the manager takes
    every no-pane branch, without a real detached process in the test."""

    provides_pane = False

    def __init__(self) -> None:
        self.specs: list[LaunchSpec] = []

    def launch(self, spec: LaunchSpec) -> None:
        self.specs.append(spec)


def _cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "env": {"FOO": "bar"},
                    "env_unset": ["CLAUDE_CONFIG_DIR"],
                }
            ],
        }
    )


def _headless_manager(
    tmp_repo: Path, tmp_path: Path, *, native: FakeNativeSteer | None = None
) -> tuple[WorkspaceManager, FakeHeadlessBackend]:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    backend = FakeHeadlessBackend()
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=store,
        launch_backend=backend,
        native_steer=native,
    )
    return mgr, backend


# ─── capability sentinel ────────────────────────────────────────────────────


def test_provides_pane_capability_flags() -> None:
    """The classvar sentinel the manager reads: tmux hosts a pane, headless doesn't."""
    assert TmuxLaunchBackend.provides_pane is True
    assert HeadlessLaunchBackend.provides_pane is False


# ─── the detached-process side-effect surface ───────────────────────────────


def test_spawn_detached_builds_hermetic_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """command + decoration → shell-free argv; env drops env_unset then overlays env
    (a key in both ends up from env, #82); detached into its own session, stdio /dev/null."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/leaked/should/be/dropped")
    calls: list[dict[str, object]] = []

    class _Proc:
        pid = 4321

    def _fake_popen(argv: list[str], **kwargs: object) -> _Proc:
        calls.append({"argv": argv, **kwargs})
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    pid = process_mod.spawn_detached(
        "claude",
        decoration=("--session-id", "abc", "--model", "opus"),
        cwd=tmp_path,
        env={"FOO": "bar"},
        env_unset=("CLAUDE_CONFIG_DIR",),
    )

    assert pid == 4321
    (call,) = calls
    assert call["argv"] == ["claude", "--session-id", "abc", "--model", "opus"]
    assert call["cwd"] == str(tmp_path)
    assert call["start_new_session"] is True
    assert call["stdout"] == subprocess.DEVNULL
    env = call["env"]
    assert isinstance(env, dict)
    assert env["FOO"] == "bar"
    assert "CLAUDE_CONFIG_DIR" not in env  # dropped before the child inherits it


def test_spawn_detached_wraps_os_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing binary / OS refusal is narrowed to ProcessError at the boundary,
    like TmuxError for the tmux surface — the manager handles one type."""

    def _boom(argv: list[str], **kwargs: object) -> object:
        raise FileNotFoundError("no such file: claude")

    monkeypatch.setattr(subprocess, "Popen", _boom)

    with pytest.raises(ProcessError):
        process_mod.spawn_detached("claude", cwd=tmp_path)


def test_headless_backend_delegates_to_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HeadlessLaunchBackend unpacks the spec into spawn_detached and touches no tmux."""
    captured: dict[str, object] = {}

    def _fake_spawn(command: str, **kwargs: object) -> int:
        captured.update({"command": command, **kwargs})
        return 99

    monkeypatch.setattr(process_mod, "spawn_detached", _fake_spawn)

    spec = LaunchSpec(
        session_name="test-sess",
        cwd=tmp_path / "wt",
        command="claude",
        decoration=("--session-id", "abc"),
        env={"FOO": "bar"},
        env_unset=("CLAUDE_CONFIG_DIR",),
        cfg=GroveConfig(),
        worktree=tmp_path / "wt",
    )
    HeadlessLaunchBackend().launch(spec)

    assert captured["command"] == "claude"
    assert captured["decoration"] == ("--session-id", "abc")
    assert captured["cwd"] == tmp_path / "wt"
    assert captured["env"] == {"FOO": "bar"}
    assert captured["env_unset"] == ("CLAUDE_CONFIG_DIR",)


# ─── manager lifecycle without a tmux session ───────────────────────────────


def test_create_headless_does_no_tmux(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """create() routes the assembled command through the paneless backend and
    performs no tmux session work — the workspace is persisted RUNNING."""
    mgr, backend = _headless_manager(tmp_repo, tmp_path)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    assert mgr.provides_pane is False
    assert len(backend.specs) == 1
    assert backend.specs[0].command == "claude"
    assert state.status is WorkspaceStatus.RUNNING
    assert fake_tmux.sessions == set()  # no tmux session was ever created
    assert fake_tmux.layouts == []


def test_reconcile_marks_headless_active_never_offline(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """No tmux session exists, yet reconciliation must NOT force OFFLINE — the pane
    is not authoritative. The worktree/ORPHANED check still applies."""
    mgr, _ = _headless_manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    # A tmux backend with no session would read OFFLINE; headless reads ACTIVE.
    assert not fake_tmux.has_session(state.tmux_session)
    assert mgr.list()[0].status is WorkspaceStatus.ACTIVE

    # The worktree/ORPHANED arm is still live for a headless workspace.
    shutil.rmtree(state.worktree_path)
    assert mgr.list()[0].status is WorkspaceStatus.ORPHANED


def test_respawn_headless_relaunches_without_offline_gate(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Respawn on a headless workspace relaunches the detached process — there is
    no tmux session to have gone OFFLINE, so the OFFLINE gate does not apply."""
    mgr, backend = _headless_manager(tmp_repo, tmp_path)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    respawned = mgr.respawn(mgr.list()[0].id)

    assert respawned.status is WorkspaceStatus.RUNNING
    assert len(backend.specs) == 2  # create + respawn both went through the backend


def test_respawn_headless_refuses_paused_record(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A paused/errored headless record has no runtime to restart — respawn refuses,
    same spirit as ensure_can_respawn for the tmux path."""
    mgr, _ = _headless_manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))
    mgr.pause(state.id)

    with pytest.raises(WorkspaceStateError):
        mgr.respawn(state.id)


# ─── pane-bound ops route over the native channel (#172) ────────────────────


def test_send_message_headless_routes_native(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """No pane to type into, so the message rides the native steer client keyed by
    the workspace's agent session — and the tmux injection seam is never touched."""
    steer = FakeNativeSteer()
    mgr, _ = _headless_manager(tmp_repo, tmp_path, native=steer)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    mgr.send_message(state.id, "hello")

    assert steer.messages == [(str(state.agent_session_id), "hello")]
    assert fake_tmux.sent_texts == []  # never reached the tmux injection seam


def test_interrupt_headless_routes_native(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Interrupt no longer refuses a paneless runtime — it routes over the native
    channel (best-effort) instead of the old CapabilityUnavailable raise (#172)."""
    steer = FakeNativeSteer()
    mgr, _ = _headless_manager(tmp_repo, tmp_path, native=steer)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    mgr.interrupt(state.id)

    assert steer.interrupts == [str(state.agent_session_id)]


def test_answer_question_headless_routes_native(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paneless workspace answers a pending question by delivering the rendered
    answer over the native channel — never through the tmux keystroke picker."""
    sidecar = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar)
    steer = FakeNativeSteer()
    mgr, _ = _headless_manager(tmp_repo, tmp_path, native=steer)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))
    sid = str(state.agent_session_id)
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "questions": [
                    {"question": "Which color?", "options": [{"label": "Blue"}, {"label": "Green"}]}
                ]
            },
        },
        sidecar_dir=sidecar,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )

    mgr.answer_question(
        state.id,
        QuestionAnswerRequest(
            session_id=sid,
            tool_use_id="toolu_1",
            answers=[QuestionAnswerItem(selected_indexes=[1])],
        ),
    )

    assert len(steer.messages) == 1
    delivered_sid, text = steer.messages[0]
    assert delivered_sid == sid
    assert "Green" in text  # the chosen option label, rendered to deliverable text
    assert fake_tmux.sent_keys == []  # the keystroke picker path was never used


def test_peek_headless_empty_snapshot_never_raises(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """peek() stays best-effort: a headless workspace renders with no pane preview
    (empty snapshot) rather than raising — the loud typed error is reserved for
    the write path (send_message/interrupt)."""
    mgr, _ = _headless_manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    peek = mgr.peek(state.id)
    assert peek.agent_snapshot is None
    assert mgr.peek_pane(state.id) == (None, None)


# ─── activity blend treats the missing pane like a remote adapter ────────────


def test_headless_blend_treats_pane_as_non_authoritative(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blend's `remote` (pane-not-authoritative) arm is fed for a headless
    workspace even though claude_code is a local adapter — the transcript/adapter
    is the sole live-state authority when there is no pane."""
    cfg = _cfg(tmp_path)
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=FakeHeadlessBackend()
    )
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))
    service = ActivityService(registry=RepoRegistry(cfg=cfg, store=store))

    seen: list[object] = []
    real_blend = ActivityService._blend

    def _spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("remote"))
        return real_blend(*args, **kwargs)

    monkeypatch.setattr(ActivityService, "_blend", staticmethod(_spy))
    service.sessions_for(mgr, state)

    assert seen and all(flag is True for flag in seen)
