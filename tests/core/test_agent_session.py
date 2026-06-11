"""Deterministic agent-session correlation: minting, persistence, adapter wiring.

Exercises the manager against the in-memory FakeTmux seam — asserting the
composed agent command (the ``--session-id <uuid>`` decoration), the persisted
``agent_session_id``, legacy-load tolerance, the resume-vs-respawn id policy, and
``primary_transcript`` resolution. No real tmux.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig, _merge_agents
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # applied via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _last_decoration(fake: FakeTmux, session: str) -> list[str]:
    for name, decoration in reversed(fake.launch_decorations):
        if name == session:
            return decoration
    raise AssertionError(f"no launch decoration recorded for {session}")


# ─── AgentSpec.kind ─────────────────────────────────────────────────────────


def test_default_claude_is_claude_code_shell_is_generic() -> None:
    cfg = GroveConfig()
    assert cfg.find_agent("claude").kind == "claude_code"  # type: ignore[union-attr]
    assert cfg.find_agent("shell").kind == "generic"  # type: ignore[union-attr]


def test_field_merge_preserves_kind_when_overriding_command() -> None:
    """The footgun guard: tweaking only ``command`` must keep ``kind``."""
    base = [{"name": "claude", "command": "claude", "kind": "claude_code"}]
    overlay = [{"name": "claude", "command": "claude --model sonnet"}]
    merged = _merge_agents(base, overlay)
    assert merged == [{"name": "claude", "command": "claude --model sonnet", "kind": "claude_code"}]


# ─── create: mint + persist + decorate ──────────────────────────────────────


def test_create_claude_mints_session_id_and_decorates(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="dash"))

    assert state.agent_session_id is not None
    uuid.UUID(state.agent_session_id)  # canonical UUID, or this raises
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
    ]


def test_create_shell_tracks_no_session(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="plain"))

    assert state.agent_session_id is None
    assert _last_decoration(fake_tmux, state.tmux_session) == []


# ─── persistence / legacy ───────────────────────────────────────────────────


def test_session_id_round_trips_through_store(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="rt"))
    reloaded = manager.store.get(state.id)
    assert reloaded.agent_session_id == state.agent_session_id


def test_legacy_state_without_session_id_loads(tmp_path: Path) -> None:
    """A record written before this field existed loads with ``agent_session_id=None``."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    now = datetime.now(tz=UTC)
    legacy = WorkspaceState(
        id="legacy-1",
        title="old",
        repo_root=str(tmp_path),
        branch="main",
        base_branch="main",
        worktree_path=str(tmp_path),
        tmux_session="grove-old",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    store.save(legacy)
    # Simulate a pre-field on-disk record by stripping the key from the JSON.
    raw = json.loads(store.path.read_text())
    del raw["workspaces"]["legacy-1"]["agent_session_id"]
    store.path.write_text(json.dumps(raw))

    assert store.get("legacy-1").agent_session_id is None


# ─── resume keeps id, respawn mints a fresh one ─────────────────────────────


def test_resume_keeps_session_id(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="res"))
    original = state.agent_session_id
    manager.pause(state.id)
    resumed = manager.resume(state.id)

    assert resumed.agent_session_id == original
    assert _last_decoration(fake_tmux, state.tmux_session) == ["--session-id", original]


def test_respawn_mints_fresh_session_id(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="rsp"))
    original = state.agent_session_id
    # Simulate the session vanishing externally → OFFLINE, the respawn precondition.
    fake_tmux.kill_session(state.tmux_session)
    respawned = manager.respawn(state.id)

    assert respawned.agent_session_id is not None
    assert respawned.agent_session_id != original  # a new session, not a continuation
    uuid.UUID(respawned.agent_session_id)
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        respawned.agent_session_id,
    ]


# ─── primary_transcript ─────────────────────────────────────────────────────


def test_primary_transcript_resolves_when_file_exists(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pt"))
    assert state.agent_session_id is not None

    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    transcript = folder / f"{state.agent_session_id}.jsonl"
    transcript.write_text(f'{{"type":"user","cwd":"{worktree}"}}\n', encoding="utf-8")

    assert transcript in manager.primary_transcript(state.id)


def test_primary_transcript_empty_for_shell(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="sh"))
    assert manager.primary_transcript(state.id) == ()


# ─── #18 hook install ───────────────────────────────────────────────────────


def test_hooks_enabled_appends_settings_flag_and_writes_file(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "hooks-settings.json"
    monkeypatch.setattr("grove.core.paths.agent_hooks_settings_path", lambda: settings)
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "t/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": True},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="h"))
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--settings",
        str(settings),
    ]
    assert settings.exists()  # Grove's own hook-only settings file, never the user's
