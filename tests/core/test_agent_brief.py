"""The first-turn brief: delivered ONCE, by whichever channel the workspace has.

Three contracts, and each one has a failure mode that costs more than the
feature is worth. Emitting twice spends the user's context on every prompt of
every session. Raising — or exiting non-zero — from the hook BLOCKS the prompt
the human just typed. And an option that silently does nothing for codex or for
a containerized agent is worse than no option, because the operator believes
their fleet is briefed.

The hook half drives the real ``run_hook_from_stdin`` entry point; the engine
half drives the real ``WorkspaceManager`` against a real git repo, exactly like
``test_phase_per_agent.py``.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.brief import BRIEF_SKILL, AgentBrief
from grove.core.agents.hook import run_hook_from_stdin
from grove.core.config import GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus
from tests.conftest import (
    DOCKER_INSPECT_STARTED_AT,
    FAKE_REMOTE_FOLDER,
    FakeCli,
    FakePreflight,
    FakeTmux,
)

FULL_ID = "d" * 64


# ─── the hook edge ──────────────────────────────────────────────────────────


@pytest.fixture
def brief_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A rendered brief the hook is pointed at, plus a sandboxed sidecar dir."""
    path = AgentBrief.render(tmp_path / "config" / "agent-brief.md")
    assert path is not None
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path / "sidecars")
    monkeypatch.setenv(AgentBrief.PATH_ENV, str(path))
    return path


def _submit(monkeypatch: pytest.MonkeyPatch, session_id: str, event: str) -> int:
    payload = {"hook_event_name": event, "session_id": session_id}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return run_hook_from_stdin([])


def test_the_brief_is_emitted_once_per_session(
    brief_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point: ``UserPromptSubmit`` fires on EVERY prompt, so the second
    one must print nothing at all."""
    del brief_file
    assert _submit(monkeypatch, "s-1", "UserPromptSubmit") == 0
    first = capsys.readouterr().out
    assert BRIEF_SKILL in first

    assert _submit(monkeypatch, "s-1", "UserPromptSubmit") == 0
    assert capsys.readouterr().out == ""


def test_a_second_session_is_briefed_on_its_own_first_turn(
    brief_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Once-per-session, not once-per-host: the marker is keyed by session."""
    del brief_file
    _submit(monkeypatch, "s-1", "UserPromptSubmit")
    capsys.readouterr()
    assert _submit(monkeypatch, "s-2", "UserPromptSubmit") == 0
    assert BRIEF_SKILL in capsys.readouterr().out


def test_only_user_prompt_submit_prints(
    brief_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every other event's stdout is ignored by Claude Code, so printing there
    is noise at best — and the sidecar write is the only job those events have."""
    del brief_file
    for event in ("Stop", "PreToolUse", "SessionStart", "Notification"):
        assert _submit(monkeypatch, f"e-{event}", event) == 0
        assert capsys.readouterr().out == ""


def test_no_env_var_means_no_brief_and_no_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The opted-out workspace, and every hand-run ``claude`` that never had the
    variable in the first place."""
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path / "sidecars")
    monkeypatch.delenv(AgentBrief.PATH_ENV, raising=False)
    assert _submit(monkeypatch, "s-3", "UserPromptSubmit") == 0
    assert capsys.readouterr().out == ""


def test_an_unreadable_brief_still_exits_zero_and_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**Exit 2 blocks the user's prompt.** A brief that was never rendered — or
    was deleted under a running fleet — must cost nothing but the brief."""
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: tmp_path / "sidecars")
    monkeypatch.setenv(AgentBrief.PATH_ENV, str(tmp_path / "nothing-here.md"))
    assert _submit(monkeypatch, "s-4", "UserPromptSubmit") == 0
    assert capsys.readouterr().out == ""


def test_an_unclaimable_marker_withholds_the_brief(
    brief_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claim first, print second. If the marker cannot be written the brief is
    withheld: repeating it on every prompt forever is the worse failure."""
    del brief_file
    monkeypatch.setattr(
        Path, "touch", lambda *_a, **_k: (_ for _ in ()).throw(OSError("read-only"))
    )
    assert _submit(monkeypatch, "s-5", "UserPromptSubmit") == 0
    assert capsys.readouterr().out == ""


def test_the_brief_points_and_does_not_restate() -> None:
    """It names the skill and stays short — the skill carries the six phases,
    the file contract and the PR rule, and duplicating any of it here would
    spend context every session and drift the day either copy is edited."""
    assert BRIEF_SKILL in AgentBrief.TEXT
    assert len(AgentBrief.TEXT.split()) < 120
    lowered = AgentBrief.TEXT.lower()
    assert "implementing" not in lowered  # the phase vocabulary
    assert "grove_phase_file" not in lowered  # the file contract


# ─── the engine: who gets the env var, who gets the prompt ──────────────────


def _cfg(tmp_path: Path, **over: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
            **over,
        }
    )


@pytest.fixture
def manager(tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux) -> WorkspaceManager:
    del fake_tmux  # installed via monkeypatch
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )


def _env_of(
    manager: WorkspaceManager, state: WorkspaceState, agent: str = "claude"
) -> dict[str, str]:
    return manager._launch_env(state, manager._agent_spec(agent))


def test_a_host_claude_workspace_is_pointed_at_the_brief(manager: WorkspaceManager) -> None:
    """Default ON, and the file exists by the time the variable names it — the
    two are rendered by one call so they cannot disagree."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="briefed"))
    assert state.brief is True

    env = _env_of(manager, state)
    published = env.get(AgentBrief.PATH_ENV)
    assert published is not None
    assert BRIEF_SKILL in Path(published).read_text(encoding="utf-8")


def test_opting_out_publishes_no_variable(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet", brief=False))
    assert state.brief is False
    assert AgentBrief.PATH_ENV not in _env_of(manager, state)


def test_the_config_default_can_be_flipped(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """A deployment turns it off without touching a call site; an explicit
    request flag still wins over the cascade."""
    del fake_tmux
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, brief={"enabled": False}),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )
    default_off = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="off"))
    asked_for = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="on", brief=True))

    assert default_off.brief is False
    assert asked_for.brief is True
    assert AgentBrief.PATH_ENV not in _env_of(mgr, default_off)
    assert AgentBrief.PATH_ENV in _env_of(mgr, asked_for)


def test_disabling_hooks_disables_the_hook_channel(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """No settings file, no hook, no injection — so naming a brief the hook can
    never read would be a variable nothing reads."""
    del fake_tmux
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, hooks={"enabled": False}),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="hookless"))
    assert state.brief is True
    assert AgentBrief.PATH_ENV not in _env_of(mgr, state)


def test_the_hook_road_leaves_the_prompt_untouched(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """One channel per workspace: a host claude agent gets the brief from its
    hook, so prepending it here as well would deliver it twice."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="host", initial_prompt="fix the parser")
    )
    launched = [
        deco for session, deco in fake_tmux.launch_decorations if session == state.tmux_session
    ]
    assert launched and launched[0][-1] == "fix the parser"


def test_codex_takes_the_prompt_road_instead(manager: WorkspaceManager) -> None:
    """codex installs no hook of any kind, so the env var would be inert: the
    brief goes onto the create-time prompt instead, on turn one, at the cost of
    no extra turn.

    NOTE the pre-existing hole one layer down: ``_compose_launch`` appends the
    initial prompt for ``claude_code`` ONLY, so a codex workspace's prompt —
    briefed or not — never reaches the launch today. The seam is right here; the
    delivery is a separate defect.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="codex", title="codex"))
    assert AgentBrief.PATH_ENV not in _env_of(manager, state, agent="codex")

    briefed = manager._brief_prompt(state, manager._agent_spec("codex"), "fix the parser")
    assert briefed is not None
    assert BRIEF_SKILL in briefed
    assert briefed.endswith("fix the parser")


def test_a_remote_agent_is_deliberately_not_briefed(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """mewbo's ``/message`` would carry the brief perfectly — and must not. The
    agent runs on a backend with no worktree, no phase file and no
    ``working-in-grove`` skill, so the brief would point it at a contract it
    cannot keep."""
    del fake_tmux
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, agents=[{"name": "mewbo", "command": "$SHELL", "kind": "mewbo"}]),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )
    state = WorkspaceState(
        id="w1",
        title="remote",
        repo_root=str(tmp_repo),
        branch="test/remote",
        base_branch="main",
        worktree_path=str(tmp_path / "wt"),
        tmux_session="test-remote",
        agent_name="mewbo",
        status=WorkspaceStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        brief=True,
    )
    assert mgr._brief_prompt(state, mgr._agent_spec("mewbo"), "start the task") == "start the task"


def test_a_create_with_no_prompt_is_left_alone(manager: WorkspaceManager) -> None:
    """Inventing a prompt would start a turn about nothing, so a create with no
    prompt on a hookless kind is honestly unbriefed rather than handed a task."""
    state = manager.create(CreateWorkspaceRequest(agent_name="codex", title="codex"))
    assert manager._brief_prompt(state, manager._agent_spec("codex"), None) is None


def test_a_container_workspace_takes_the_prompt_road_too(
    manager: WorkspaceManager, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """A container has no ``grove-agent-hook`` on PATH, so the hook command
    spools the payload and prints nothing — the injection channel is absent even
    though the kind is claude_code and hooks are on."""
    del fake_tmux
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="containered", initial_prompt="do it")
    )
    stored = manager.store.get(state.id)
    stored.runtime = Runtime.CONTAINER
    stored.container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref="ghcr.io/example/dev:1",
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels=ContainerRuntimeState.labels_for(
            state.id, project_slug=slugify_project(tmp_repo)
        ),
        provisioned=True,
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
    )
    manager.store.save(stored)

    assert AgentBrief.PATH_ENV not in _env_of(manager, stored)
    assert BRIEF_SKILL in (
        manager._brief_prompt(stored, manager._agent_spec("claude"), "do it") or ""
    )


# ─── persistence ────────────────────────────────────────────────────────────


def test_a_record_written_before_the_brief_existed_loads(tmp_path: Path) -> None:
    """The `placement`/`branch_provenance` precedent: no migration, and the
    default says what is true of such a record — it was never briefed."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    payload = {
        "version": 1,
        "workspaces": {
            "old1": {
                "id": "old1",
                "title": "legacy",
                "repo_root": str(tmp_path),
                "branch": "test/legacy",
                "base_branch": "main",
                "worktree_path": str(tmp_path / "wt"),
                "tmux_session": "test-legacy",
                "agent_name": "claude",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.get("old1")
    assert loaded.brief is False
