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
from typing import Any

import pytest

from grove.core.agents import all_adapters
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig, _merge_agents
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound, ResumeNotSupported
from grove.core.manager import _RESUMABLE_KINDS, WorkspaceManager
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
            # This file asserts exact launch decorations for session-id/resume
            # composition — hooks (on by default) would append an unrelated
            # ``--settings`` flag to every claude_code decoration. The two
            # dedicated hook-install tests build their own cfg with hooks
            # explicitly on instead of using this fixture.
            "hooks": {"enabled": False},
            # Same reason, one feature over: with no hook to inject it, the
            # first-turn brief rides the initial prompt, which would prefix
            # every expected positional here. Its own coverage is
            # `test_agent_brief.py`.
            "brief": {"enabled": False},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _last_decoration(fake: FakeTmux, session: str) -> list[str]:
    for name, decoration in reversed(fake.launch_decorations):
        if name == session:
            return decoration
    raise AssertionError(f"no launch decoration recorded for {session}")


def _materialize_claude(cfg_home: Path, cwd: Path, session_id: str) -> None:
    """A claude transcript recorded at ``cwd`` so SessionExplorer.resolve finds
    it (create-time resume-ref resolution scans the repo root)."""
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(
        f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8"
    )


def _materialize_codex(codex_home: Path, cwd: Path, session_id: str) -> None:
    """A codex rollout recorded at ``cwd`` (session_meta head) so resolve finds it."""
    folder = codex_home / "sessions" / "2026" / "04" / "28"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"rollout-2026-04-28T13-43-44-{session_id}.jsonl").write_text(
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + session_id + '","cwd":"' + str(cwd) + '"}}\n',
        encoding="utf-8",
    )


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


# ─── initial_prompt rides the launch argv (claude_code) ─────────────────────


def test_create_claude_appends_initial_prompt_as_trailing_positional(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The prompt rides the launch argv as the LAST token — after `--session-id`
    — so claude boots already working on it. It goes through the same shell-quote
    path in build_workspace_layout as the rest of the decoration (no second
    quoting site), so the decoration list carries the raw prompt string."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="task", initial_prompt="do X please")
    )

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "do X please",
    ]


def test_create_claude_without_initial_prompt_is_byte_identical(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """No initial_prompt → launch decoration carries only the session-id pair."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="noprompt"))

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
    ]


def test_create_shell_ignores_initial_prompt(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A bare shell (generic) has no prompt concept — the decoration stays empty,
    the prompt is silently dropped (not typed into the pane)."""
    state = manager.create(
        CreateWorkspaceRequest(agent_name="shell", title="plain", initial_prompt="ignored")
    )

    assert state.agent_session_id is None
    assert _last_decoration(fake_tmux, state.tmux_session) == []


# ─── model rides the launch argv (--model) ───────────────────────────────────


def test_create_claude_appends_model_flag(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    """A per-create model forwards to ``claude --model <id>`` after --session-id."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="task", model="opus"))

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--model",
        "opus",
    ]


def test_create_claude_model_precedes_initial_prompt_positional(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The prompt stays the trailing positional — --model is a flag, so it comes
    before the prompt token."""
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="task", model="claude-opus-4-8", initial_prompt="do X"
        )
    )

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--model",
        "claude-opus-4-8",
        "do X",
    ]


def test_create_shell_ignores_model(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    """A generic shell has no launch-time model flag — the model is dropped, the
    decoration stays empty."""
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="plain", model="opus"))

    assert _last_decoration(fake_tmux, state.tmux_session) == []


# ─── tools_offline rides the launch argv, per adapter ────────────────────────


@pytest.fixture
def offline_manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    """A manager whose agents all opt into `tools_offline`, with the hook
    `--settings` decoration disabled so the asserted argv stays a fixed,
    non-temp-path shape."""
    del fake_tmux  # applied via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            # And with no hook, the first-turn brief would ride the prompt
            # positional this file asserts verbatim (see `test_agent_brief.py`).
            "brief": {"enabled": False},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "tools_offline": True,
                },
                {"name": "codex", "command": "codex", "kind": "codex", "tools_offline": True},
                {
                    "name": "shell",
                    "command": "$SHELL",
                    "description": "Plain shell",
                    "tools_offline": True,
                },
            ],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_create_claude_offline_appends_disallowed_tools(
    offline_manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = offline_manager.create(CreateWorkspaceRequest(agent_name="claude", title="task"))

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--disallowedTools",
        "WebFetch,WebSearch",
    ]


def test_create_codex_offline_appends_sandbox_flags(
    offline_manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Codex mints no session id (empty base decoration), but `tools_offline`
    still rides the launch — independent of correlation, like `model`."""
    state = offline_manager.create(CreateWorkspaceRequest(agent_name="codex", title="task"))

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--sandbox",
        "workspace-write",
        "-c",
        "sandbox_workspace_write.network_access=false",
    ]


def test_create_shell_offline_is_noop(
    offline_manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A generic shell has no tool concept — `tools_offline` is a no-op."""
    state = offline_manager.create(CreateWorkspaceRequest(agent_name="shell", title="plain"))

    assert _last_decoration(fake_tmux, state.tmux_session) == []


def test_create_claude_offline_precedes_initial_prompt_positional(
    offline_manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The prompt stays the trailing positional after every flag, including the
    offline-tools decoration."""
    state = offline_manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="task", initial_prompt="do X")
    )

    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--disallowedTools",
        "WebFetch,WebSearch",
        "do X",
    ]


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


# ─── resume-into-workspace ───────────────────────────────────────────────────


def test_create_claude_resume_emits_resume_flag_and_pins_id(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``resume_session_id`` adopts the resolved id (no mint) and the claude
    launch carries ``--resume <id>`` INSTEAD of ``--session-id <id>`` — plain
    ``--resume`` keeps the same session id/file, so pinning is correct."""
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    chosen = "11111111-2222-3333-4444-555555555555"
    _materialize_claude(cfg_home, manager.repo_root, chosen)  # must resolve first
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="resume me", resume_session_id=chosen)
    )

    assert state.agent_session_id == chosen
    assert _last_decoration(fake_tmux, state.tmux_session) == ["--resume", chosen]


def test_create_claude_resume_keeps_settings_model_and_prompt(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hooks ``--settings`` decoration, ``--model``, and the trailing
    ``initial_prompt`` positional all still apply on a resume launch."""
    settings = tmp_path / "hooks-settings.json"
    monkeypatch.setattr("grove.core.paths.agent_hooks_settings_path", lambda: settings)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "t/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": True},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)

    chosen = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _materialize_claude(cfg_home, tmp_repo, chosen)  # must resolve first
    state = mgr.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="resume rich",
            resume_session_id=chosen,
            model="opus",
            initial_prompt="continue",
        )
    )
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--resume",
        chosen,
        "--settings",
        str(settings),
        "--model",
        "opus",
        "continue",
    ]


def test_create_codex_resume_emits_resume_subcommand_and_pins_id(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex normally mints nothing; on resume the chosen uuid is persisted and
    the launch carries the ``resume <uuid>`` SUBCOMMAND (codex's grammar is
    ``codex [OPTIONS] <COMMAND> [ARGS]`` so it rides after the command)."""
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    chosen = "99999999-8888-7777-6666-555555555555"
    _materialize_codex(codex_home, manager.repo_root, chosen)  # must resolve first
    state = manager.create(
        CreateWorkspaceRequest(agent_name="codex", title="codex resume", resume_session_id=chosen)
    )

    assert state.agent_session_id == chosen
    assert _last_decoration(fake_tmux, state.tmux_session) == ["resume", chosen]


def test_create_shell_resume_rejected_before_side_effects(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """A generic/shell agent has no resume-by-id handle → clear GroveError,
    raised before any worktree side effect (no session ever created)."""
    before = set(fake_tmux.sessions)
    with pytest.raises(ResumeNotSupported, match="generic"):
        manager.create(
            CreateWorkspaceRequest(agent_name="shell", title="nope", resume_session_id="x-y-z")
        )
    assert set(fake_tmux.sessions) == before  # no side effect


def test_create_without_resume_is_byte_identical(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """No resume_session_id → the launch takes the ordinary mint path."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fresh"))
    assert _last_decoration(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
    ]


def test_create_resume_unknown_ref_rejected_before_side_effects(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable resume ref fails (AgentSessionNotFound) BEFORE any
    worktree/tmux side effect — never a fully-provisioned workspace pinned to
    a bogus id."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    before = set(fake_tmux.sessions)
    with pytest.raises(AgentSessionNotFound, match="no session matches"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="bogus",
                resume_session_id="deadbeef-1111-2222-3333-444455556666",
            )
        )
    assert set(fake_tmux.sessions) == before  # no side effect


def test_create_resume_wrong_kind_rejected_before_side_effects(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resuming a codex session under a claude agent is rejected — its
    adapter could never read the transcript — and no side effect runs."""
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    codex_sid = "77776666-5555-4444-3333-222211110000"
    _materialize_codex(codex_home, manager.repo_root, codex_sid)
    before = set(fake_tmux.sessions)
    with pytest.raises(AgentSessionNotFound, match="cannot read a codex transcript"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="claude", title="mismatch", resume_session_id=codex_sid
            )
        )
    assert set(fake_tmux.sessions) == before  # no side effect


def test_resume_materialized_session_uses_resume_flag(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unpause CONTINUES a session that has already materialized — the launch
    carries the tool's ``--resume`` flag, not a fresh ``--session-id`` mint, so a
    paused-then-resumed workspace re-opens its real transcript."""
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="mat"))
    minted = state.agent_session_id
    assert minted is not None
    manager.pause(state.id)
    # The session materialized while it ran (transcripts outlive the worktree).
    _materialize_claude(cfg_home, Path(state.worktree_path), minted)
    resumed = manager.resume(state.id)
    assert _last_decoration(fake_tmux, resumed.tmux_session)[:2] == ["--resume", minted]


def test_resume_unmaterialized_session_keeps_mint_flag(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-materialized minted id keeps ``--session-id`` on unpause, so
    it can still mint fresh — continue what exists, mint what doesn't."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="unmat"))
    minted = state.agent_session_id
    assert minted is not None
    manager.pause(state.id)
    resumed = manager.resume(state.id)
    assert _last_decoration(fake_tmux, resumed.tmux_session)[:2] == ["--session-id", minted]


def test_resumable_kinds_derived_from_adapter_layer() -> None:
    """The resumable-kinds set is DERIVED from each adapter's ``resumable``
    flag, not hand-listed — so a future resumable adapter can't be missed."""
    derived = frozenset(a.kind for a in all_adapters() if a.resumable)
    expected = {"claude_code", "codex"}
    assert derived == _RESUMABLE_KINDS
    assert set(_RESUMABLE_KINDS) == expected


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


# ─── hook install ────────────────────────────────────────────────────────────


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


def _hook_settings_written(
    tmp_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hooks: dict[str, object]
) -> dict[str, Any]:
    """Drive a real `create()` and return the hook settings file it rendered."""
    settings = tmp_path / "hooks-settings.json"
    monkeypatch.setattr("grove.core.paths.agent_hooks_settings_path", lambda: settings)
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "t/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": True, **hooks},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="h"))
    loaded: dict[str, Any] = json.loads(settings.read_text(encoding="utf-8"))
    return loaded


def test_hook_settings_are_byte_identical_when_daemon_url_is_unset(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon address is config, not a hardcoded module constant, but an
    unset knob must not move the default: it renders exactly what the module
    constant renders, so no existing install sees a changed settings file."""
    del fake_tmux
    written = _hook_settings_written(tmp_repo, tmp_path, monkeypatch, {})
    assert written == ClaudeHook.settings()


def test_hook_settings_honor_a_configured_daemon_url(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the knob exists: an agent launched into its own network
    namespace cannot reach the daemon at 127.0.0.1, and an address a runtime
    can't resolve is policy that does not belong in code.

    The address reaches the hook as an argv on the rendered command rather
    than as a registered `http` handler — the push is made by the entry
    point, which is exactly the thing that does not exist where it could not
    work anyway."""
    del fake_tmux
    written = _hook_settings_written(
        tmp_repo, tmp_path, monkeypatch, {"daemon_url": "http://gateway.example:7421"}
    )
    commands = {
        handler["command"]
        for matchers in written["hooks"].values()
        for matcher in matchers
        for handler in matcher["hooks"]
    }
    assert len(commands) == 1  # one rendered command, shared by every event
    assert "--daemon-url http://gateway.example:7421" in commands.pop()
