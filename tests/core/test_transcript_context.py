"""Transcript context override — reads across a container runtime boundary,
and its production WRITER at every launch.

A container-launched agent's own transcript records a cwd (and lives under a
config dir) that can never equal the host's `worktree_path`/ambient
`CLAUDE_CONFIG_DIR` — so a host-side read with no override searches the wrong
folder entirely. `WorkspaceState.transcript_context` is the optional per-
workspace override; these tests pin the default (no override = byte-for-byte
current behavior) and the override path across the pure state helper, the
store round-trip, and the manager/`SessionExplorer` read call sites.

`create`/`resume`/`respawn` derive the override from the composed launch env via
`TranscriptContext.for_launch` (the hermetic-profile pin —
`AgentSpec.env = {"CLAUDE_CONFIG_DIR": ...}` — sends an agent's transcripts
somewhere the READING process's ambient env never looks, which would otherwise
pin the workspace at STARTING forever). The "writer" section below exercises
that derivation through the public `create`/`resume`/`respawn` verbs; it never
calls the private `_launch` seam directly.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from grove.core import process as process_mod
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.container_policy import CONTAINER_CONFIG_ROOT, AgentSharePlan
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.launch import (
    DevcontainerLaunchBackend,
    HeadlessLaunchBackend,
    HostNamespaceBackend,
    LaunchSpec,
    TmuxLaunchBackend,
)
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, TranscriptContext, WorkspaceState, WorkspaceStatus
from tests.conftest import FAKE_REMOTE_FOLDER, FakeCli, FakePreflight, FakeTmux

#: The identity a provisioned container carries into a launch — the CLI's own
#: reported workspace folder, which is what makes the recorded `agent_cwd` the
#: container's answer rather than one Grove guessed.
_PROVISIONED = ContainerRuntimeState(
    container_id="c" * 64,
    remote_user="vscode",
    remote_workspace_folder=FAKE_REMOTE_FOLDER,
    provisioned=True,
)

# ─── shared helpers ──────────────────────────────────────────────────────────


def _state(**overrides: object) -> WorkspaceState:
    """A minimal, otherwise-valid `WorkspaceState` for the pure-helper tests."""
    now = datetime.now(tz=UTC)
    base: dict[str, object] = {
        "id": "w1",
        "title": "t",
        "repo_root": "/repo",
        "branch": "b",
        "base_branch": "main",
        "worktree_path": "/repo/.worktrees/w1",
        "tmux_session": "grove-w1",
        "agent_name": "claude",
        "status": WorkspaceStatus.RUNNING,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return WorkspaceState(**base)  # type: ignore[arg-type]


def _write_transcript_at(config_dir: Path, sid: str, cwd: str, *, mtime: int, prompt: str) -> Path:
    """A real-shaped Claude transcript under ``config_dir/projects/<encoded cwd>``,
    with ``cwd`` recorded VERBATIM — deliberately a plain string, since the whole
    point is that it may be a container-internal path that never exists on this
    host (mirrors ``_write_transcript`` in test_session_explorer.py)."""
    folder = config_dir / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"2026-07-08T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _manager_with_agents(
    tmp_repo: Path, tmp_path: Path, agents: list[dict[str, object]]
) -> WorkspaceManager:
    """A Manager whose cascade overrides one or more agents' `env` — the
    hermetic-profile mechanism `TranscriptContext.for_launch` reads back off
    the composed launch env. `JsonWorkspaceStore` is stateless (every call
    reads/writes `tmp_path / "state.json"` fresh, no in-memory cache), so
    calling this twice with the SAME `tmp_path` and a different `agents`
    stands in for "the operator edited config, then relaunched" — the
    respawn/resume re-derivation tests below do exactly that."""
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": agents,
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _claude_spec(**overrides: object) -> dict[str, object]:
    """A COMPLETE `claude` `AgentSpec` dict. `GroveConfig.model_validate` (unlike
    `load_config`) has no built-in seed layer to merge-refine a bare
    `{"name": "claude"}` against, so every field a test cares about — here,
    `kind` — must be spelled out explicitly or it silently defaults away."""
    return {"name": "claude", "command": "claude", "kind": "claude_code", **overrides}


def _codex_spec(**overrides: object) -> dict[str, object]:
    return {"name": "codex", "command": "codex", "kind": "codex", **overrides}


def _write_todo_transcript(config_dir: Path, sid: str, cwd: str) -> Path:
    """A transcript ending on a `TodoWrite` tool_use, so `latest_todo` and
    `current_model` both have something real to read — mirrors
    `test_steering.py::test_latest_todo_reads_through_the_resolved_adapter`'s
    shape. Callers plant this ONLY under the pinned dir, never the ambient
    one — that is what actually forces a read to go through the override (see
    the module docstring on the section below for why the config-dir cascade
    can't rescue this)."""
    folder = config_dir / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h1","timestamp":"2026-07-25T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"do it"}}}}\n'
        f'{{"type":"assistant","uuid":"a1","requestId":"r1",'
        f'"timestamp":"2026-07-25T08:00:05.000Z","isSidechain":false,"cwd":"{cwd}",'
        f'"message":{{"id":"m1","role":"assistant","model":"claude-opus-5",'
        f'"stop_reason":"tool_use","content":[{{"type":"tool_use","id":"t1",'
        f'"name":"TodoWrite","input":{{"todos":['
        f'{{"content":"first task","status":"completed","activeForm":"doing first"}},'
        f'{{"content":"second task","status":"in_progress","activeForm":"doing second"}}'
        f"]}}}}]}}}}\n",
        encoding="utf-8",
    )
    return path


def _write_user_skill(config_dir: Path, name: str) -> None:
    """A user-level skill — what the TIER 1 `session_controls` scan reads out
    of the adapter's config-dir cascade, and exactly the surface that can
    silently list the WRONG profile: wrong content, not missing content, the
    harder failure to notice."""
    skill_dir = config_dir / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: pinned-profile skill\n---\n\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture
def pinned(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[WorkspaceManager, WorkspaceState]:
    """A workspace whose agent pins `CLAUDE_CONFIG_DIR` at a dir the READING
    process's own ambient env does not name, with a real transcript AND a
    user-level skill planted ONLY under the pinned dir. Built from this
    file's own `_manager_with_agents`/`_claude_spec` helpers rather than a
    new harness.

    Deliberately a different shape than `test_activity_service.py`'s
    `pinned_asymmetry`: that fixture wraps a `RepoRegistry`/`ActivityService`
    neither `WorkspaceManager.latest_todo`/`session_controls` nor
    `SessionExplorer.subagent_turns` need, and that file always builds its
    manager through `RepoRegistry.get(repo)` where this one always builds one
    directly — two established, divergent conventions a single shared fixture
    would have to fight. What's actually shared (pin one dir, point the
    reader's ambient env at a different one, plant fixture data only in the
    pinned dir) already lives in this file's own local helpers reused here,
    which is the file-scoped version of "factor once."
    """
    del fake_tmux
    pinned_dir = tmp_path / "profiles" / "work"
    ambient = tmp_path / "reader-home" / ".claude"
    ambient.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ambient))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "reader-home")

    mgr = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": str(pinned_dir)})]
    )
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="pinned"))
    assert state.agent_session_id is not None
    assert state.transcript_context is not None
    _write_todo_transcript(pinned_dir, state.agent_session_id, str(state.agent_cwd))
    _write_user_skill(pinned_dir, "pinned-only-skill")
    return mgr, state


# ─── TranscriptContext.for_launch is pure ───────────────────────────────────


def test_for_launch_derives_context_from_the_supplied_env_mapping() -> None:
    ctx = TranscriptContext.for_launch(
        kind="claude_code",
        env={"CLAUDE_CONFIG_DIR": "/mnt/host-config"},
        agent_cwd=Path("/repo/.worktrees/w1"),
    )
    assert ctx == TranscriptContext(config_dir="/mnt/host-config", agent_cwd="/repo/.worktrees/w1")


def test_for_launch_never_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that sets a REAL env var and expects `for_launch` to see it
    would FAIL by design — the whole point is deriving from the caller's
    already-*composed* launch env, never the reading process's own ambient
    environment (that confusion is the bug this exists to close)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/leaked-from-os-environ")
    ctx = TranscriptContext.for_launch(kind="claude_code", env={}, agent_cwd=Path("/x"))
    assert ctx is None


def test_for_launch_returns_none_when_the_env_pins_nothing() -> None:
    """The overwhelmingly common case: an unpinned agent's launch env carries
    no config-dir var at all."""
    assert TranscriptContext.for_launch(kind="claude_code", env={}, agent_cwd=Path("/x")) is None


def test_for_launch_returns_none_for_kinds_with_no_config_dir_concept() -> None:
    """`generic`/`mewbo` are absent from `CONFIG_DIR_ENV` — even an env that
    HAPPENS to carry `CLAUDE_CONFIG_DIR` (a copy-pasted agent config) must
    never be misread as a pin for a kind with no config-dir concept at all."""
    decoy_env = {"CLAUDE_CONFIG_DIR": "/decoy", "CODEX_HOME": "/decoy-2"}
    assert TranscriptContext.for_launch(kind="mewbo", env=decoy_env, agent_cwd=Path("/x")) is None
    assert TranscriptContext.for_launch(kind="generic", env=decoy_env, agent_cwd=Path("/x")) is None


# ─── pure state helper: transcript_scan_cwds ────────────────────────────────


def test_transcript_scan_cwds_defaults_to_scan_cwds() -> None:
    """No override: byte-for-byte the existing `scan_cwds` union."""
    state = _state(project_subpath="sub")
    assert state.transcript_context is None
    assert state.transcript_scan_cwds == state.scan_cwds
    assert len(state.transcript_scan_cwds) == 2  # nested: agent_cwd != worktree root


def test_transcript_scan_cwds_override_dedupes_when_it_equals_agent_cwd() -> None:
    """The ordinary HOST-launch case: `for_launch` records the launch cwd,
    which for a FLAT (non-nested) workspace is the exact same path
    `scan_cwds` already scans on its own. The union must dedupe it away
    rather than scan the same directory twice, so a pinned host workspace
    reads byte-for-byte the same scan set as an unpinned one."""
    state = _state()  # project_subpath="" (flat): scan_cwds is the lone worktree root
    ctx = TranscriptContext(config_dir="/mnt/host-config", agent_cwd=str(state.agent_cwd))
    state = replace(state, transcript_context=ctx)
    assert state.transcript_scan_cwds == state.scan_cwds
    assert len(state.transcript_scan_cwds) == 1


def test_transcript_scan_cwds_override_still_scans_worktree_root_for_nested_project() -> None:
    """For a NESTED project (`project_subpath` set), `scan_cwds` is the union
    of `agent_cwd` and the worktree root — an ordinary host launch records
    `agent_cwd` as its context, and REPLACING the union with just that one
    cwd would silently drop a root-recorded session. The recorded cwd still
    sorts first (most specific); the worktree root survives right behind
    it."""
    state = _state(project_subpath="sub")
    ctx = TranscriptContext(config_dir="/mnt/host-config", agent_cwd=str(state.agent_cwd))
    state = replace(state, transcript_context=ctx)
    worktree_root = Path(state.worktree_path)
    assert state.transcript_scan_cwds == (state.agent_cwd, worktree_root)


def test_transcript_scan_cwds_override_unions_a_foreign_cwd_in_front() -> None:
    """The container case: a runtime whose recorded cwd matches
    NEITHER host path is still scanned FIRST (the most specific answer),
    with the ordinary host union appended after it — union, never a
    wholesale replacement, so a foreign cwd can only ADD a (possibly empty)
    scan, never drop a host one."""
    ctx = TranscriptContext(config_dir="/mnt/host-config", agent_cwd="/workspace/sub")
    state = _state(project_subpath="sub", transcript_context=ctx)
    worktree_root = Path(state.worktree_path)
    assert state.transcript_scan_cwds == (Path("/workspace/sub"), state.agent_cwd, worktree_root)


# ─── store round-trip ────────────────────────────────────────────────────────


def test_store_round_trips_transcript_context(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    ctx = TranscriptContext(config_dir="/mnt/host-config", agent_cwd="/workspace")
    state = _state(transcript_context=ctx)
    store.save(state)

    reloaded = store.get(state.id)

    assert reloaded.transcript_context == ctx


def test_store_loads_legacy_record_without_transcript_context(tmp_path: Path) -> None:
    """A record written before this field existed loads with `None` — the same
    `.get()`-legacy-default precedent as `agent_kind`/`placement`."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    state = _state()
    store.save(state)
    raw = json.loads(store.path.read_text())
    del raw["workspaces"][state.id]["transcript_context"]
    store.path.write_text(json.dumps(raw))

    assert store.get(state.id).transcript_context is None


def test_store_ignores_malformed_transcript_context(tmp_path: Path) -> None:
    """A corrupt on-disk override degrades to `None` rather than raising —
    losing an override falls back to the still-correct default read."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    state = _state()
    store.save(state)
    raw = json.loads(store.path.read_text())
    raw["workspaces"][state.id]["transcript_context"] = {"config_dir": "/only-one-key"}
    store.path.write_text(json.dumps(raw))

    assert store.get(state.id).transcript_context is None


def test_store_decodes_blank_config_dir_to_none(tmp_path: Path) -> None:
    """A blank ``config_dir`` decodes to `None`, matching what the only
    OTHER way into this type — `TranscriptContext.for_launch` — already
    normalizes away (a falsy pin never gets recorded). The two entry points
    must agree on what "falsy" means: an empty string is NOT inert on the
    read side — it slips past `transcript_config_dir_scope`'s `config_dir is
    None` guard and actively CLEARS a legitimate ambient env var for the
    duration of a read. Only reachable via a hand-edited state file."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    state = _state()
    store.save(state)
    raw = json.loads(store.path.read_text())
    raw["workspaces"][state.id]["transcript_context"] = {"config_dir": "", "agent_cwd": "/x"}
    store.path.write_text(json.dumps(raw))

    assert store.get(state.id).transcript_context is None


def test_store_decodes_whitespace_only_config_dir_to_none(tmp_path: Path) -> None:
    """The whitespace sibling of the blank-string case above: an unstripped
    ``"   "`` would install an active-but-USELESS scope (truthy, so it passes
    the `is None` guard, but points nowhere real) rather than being caught as
    empty."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    state = _state()
    store.save(state)
    raw = json.loads(store.path.read_text())
    raw["workspaces"][state.id]["transcript_context"] = {"config_dir": "   ", "agent_cwd": "/x"}
    store.path.write_text(json.dumps(raw))

    assert store.get(state.id).transcript_context is None


# ─── transcript_config_dir_scope (env boundary) ─────────────────────────────


def test_transcript_config_dir_scope_sets_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    with WorkspaceManager.transcript_config_dir_scope("claude_code", "/mnt/host"):
        assert os.environ["CLAUDE_CONFIG_DIR"] == "/mnt/host"

    assert "CLAUDE_CONFIG_DIR" not in os.environ


def test_transcript_config_dir_scope_restores_prior_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/original")

    with WorkspaceManager.transcript_config_dir_scope("claude_code", "/mnt/host"):
        assert os.environ["CLAUDE_CONFIG_DIR"] == "/mnt/host"

    assert os.environ["CLAUDE_CONFIG_DIR"] == "/original"


def test_transcript_config_dir_scope_noop_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/original")

    with WorkspaceManager.transcript_config_dir_scope("claude_code", None):
        assert os.environ["CLAUDE_CONFIG_DIR"] == "/original"

    assert os.environ["CLAUDE_CONFIG_DIR"] == "/original"


def test_transcript_config_dir_scope_noop_for_kind_without_config_dir_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mewbo/generic have no config-dir env concept — the override is a no-op."""
    monkeypatch.delenv("SOME_UNRELATED_VAR", raising=False)

    with WorkspaceManager.transcript_config_dir_scope("mewbo", "/mnt/host"):
        assert "SOME_UNRELATED_VAR" not in os.environ

    assert "SOME_UNRELATED_VAR" not in os.environ


def test_transcript_config_dir_scope_uses_codex_home_for_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)

    with WorkspaceManager.transcript_config_dir_scope("codex", "/mnt/host-codex"):
        assert os.environ["CODEX_HOME"] == "/mnt/host-codex"

    assert "CODEX_HOME" not in os.environ


# ─── manager.transcript_scope: the seam every read routes through ──────────


def test_transcript_scope_is_nullcontext_when_unpinned(manager: WorkspaceManager) -> None:
    """The hot path stays a bare `nullcontext` for the overwhelmingly common
    unpinned case — every call site (and every future one) pays nothing
    extra on the path a workspace without a pin takes."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="plain"))
    assert state.transcript_context is None
    assert isinstance(manager.transcript_scope(state), contextlib.nullcontext)


def test_transcript_scope_resolves_the_kind_and_restores_env(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """`transcript_scope` resolves the workspace's own effective kind rather
    than taking one from the caller (its whole reason to exist as a public
    seam instead of four bespoke `with` blocks): no caller can scope a read
    with a kind that disagrees with the workspace's."""
    mgr, state = pinned
    ctx = state.transcript_context
    assert ctx is not None
    before = os.environ["CLAUDE_CONFIG_DIR"]

    with mgr.transcript_scope(state):
        assert os.environ["CLAUDE_CONFIG_DIR"] == ctx.config_dir

    assert os.environ["CLAUDE_CONFIG_DIR"] == before  # restored, never the pinned dir


# ─── writer: create/resume/respawn populate transcript_context ─────────────


def test_create_persists_transcript_context_for_a_pinned_claude_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The headline case: a `claude_code` agent pinned with `env:
    {"CLAUDE_CONFIG_DIR": ...}` (the documented hermetic-profile mechanism)
    gets its `transcript_context` populated at create — this is what closes
    the STARTING-forever bug, since the reader can now scope itself to the
    same dir the agent actually wrote to."""
    del fake_tmux
    pinned_dir = str(tmp_path / "claude-profile")
    mgr = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})]
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="pinned"))

    assert state.transcript_context is not None
    assert state.transcript_context.config_dir == pinned_dir
    assert state.transcript_context.agent_cwd == str(state.agent_cwd)
    # Persisted, not just returned — round-trips through the store like every
    # other field `create` sets.
    assert mgr.store.get(state.id).transcript_context == state.transcript_context


def test_create_persists_transcript_context_for_a_pinned_codex_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The per-kind map is honored, not hardcoded to Claude: a `codex` agent
    pinned with `CODEX_HOME` gets a context keyed off the SAME env var its
    own adapter reads (`TranscriptContext.CONFIG_DIR_ENV["codex"]`)."""
    del fake_tmux
    pinned_dir = str(tmp_path / "codex-home")
    mgr = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_codex_spec(env={"CODEX_HOME": pinned_dir})]
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="codex", title="pinned-codex"))

    assert state.transcript_context is not None
    assert state.transcript_context.config_dir == pinned_dir


def test_create_leaves_transcript_context_none_for_an_unpinned_agent(
    manager: WorkspaceManager,
) -> None:
    """The overwhelmingly common case, and the exact historical behavior: no
    `CLAUDE_CONFIG_DIR` pin in the launch env means nothing to record."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="plain"))
    assert state.transcript_context is None


def test_create_never_sets_transcript_context_for_a_generic_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A `generic` agent (the built-in `shell`) has no config-dir concept at
    all — even an env that HAPPENS to carry `CLAUDE_CONFIG_DIR` (a
    copy-pasted agent config, say) must never be misread as a pin. The
    `mewbo` half of this same `CONFIG_DIR_ENV.get(kind) is None` branch is
    pinned directly against `TranscriptContext.for_launch` above, avoiding a
    live Mewbo client fake for what is otherwise the identical code path."""
    del fake_tmux
    decoy_dir = str(tmp_path / "decoy")
    mgr = _manager_with_agents(
        tmp_repo,
        tmp_path,
        agents=[{"name": "shell", "command": "$SHELL", "env": {"CLAUDE_CONFIG_DIR": decoy_dir}}],
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="shell", title="generic"))

    assert state.transcript_context is None


def test_respawn_picks_up_a_newly_added_config_pin(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Persisting on every launch (not just create) is the point: an operator
    who adds an `env` pin after the workspace already exists has it picked up
    at the next relaunch, here via `respawn` (the vanished-tmux-session
    recovery path)."""
    unpinned = _manager_with_agents(tmp_repo, tmp_path, agents=[_claude_spec()])
    state = unpinned.create(CreateWorkspaceRequest(agent_name="claude", title="respawn-add"))
    assert state.transcript_context is None
    fake_tmux.kill_session(state.tmux_session)  # tmux session vanished externally → OFFLINE

    # A second Manager pointed at the SAME on-disk store (stateless — every
    # call reads/writes the file fresh, no in-memory cache to fall out of
    # sync) stands in for "the operator edited config, then relaunched".
    pinned_dir = str(tmp_path / "claude-profile")
    pinned = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})]
    )

    respawned = pinned.respawn(state.id)

    assert respawned.transcript_context is not None
    assert respawned.transcript_context.config_dir == pinned_dir


def test_respawn_clears_a_removed_config_pin(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The case most likely to regress: an operator REMOVES a config-dir pin
    (reverts to the tool's own default profile) and relaunches. If the writer
    only ran at create, the stale context would keep scoping every future
    read to a directory the agent no longer writes to — silently reopening
    the STARTING-forever bug in the opposite direction. `respawn` must
    re-derive from the CURRENT config and clear it back to `None`."""
    pinned_dir = str(tmp_path / "claude-profile")
    pinned = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})]
    )
    state = pinned.create(CreateWorkspaceRequest(agent_name="claude", title="respawn-remove"))
    assert state.transcript_context is not None
    fake_tmux.kill_session(state.tmux_session)  # tmux session vanished externally → OFFLINE

    unpinned = _manager_with_agents(tmp_repo, tmp_path, agents=[_claude_spec()])

    respawned = unpinned.respawn(state.id)

    assert respawned.transcript_context is None


def test_resume_picks_up_a_newly_added_config_pin(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The `resume` (pause → recreate worktree+session) side of the same
    re-derivation contract `respawn` gets above."""
    del fake_tmux
    unpinned = _manager_with_agents(tmp_repo, tmp_path, agents=[_claude_spec()])
    state = unpinned.create(CreateWorkspaceRequest(agent_name="claude", title="resume-add"))
    assert state.transcript_context is None
    unpinned.pause(state.id)

    pinned_dir = str(tmp_path / "claude-profile")
    pinned = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})]
    )

    resumed = pinned.resume(state.id)

    assert resumed.transcript_context is not None
    assert resumed.transcript_context.config_dir == pinned_dir


def test_resume_clears_a_removed_config_pin(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The `resume` side of the clear-back regression guard: removing the
    pin and resuming must not leave a stale context scoping reads to a
    directory the agent no longer uses."""
    del fake_tmux
    pinned_dir = str(tmp_path / "claude-profile")
    pinned = _manager_with_agents(
        tmp_repo, tmp_path, agents=[_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})]
    )
    state = pinned.create(CreateWorkspaceRequest(agent_name="claude", title="resume-remove"))
    assert state.transcript_context is not None
    pinned.pause(state.id)

    unpinned = _manager_with_agents(tmp_repo, tmp_path, agents=[_claude_spec()])

    resumed = unpinned.resume(state.id)

    assert resumed.transcript_context is None


class _RaisingLaunchBackend(HostNamespaceBackend):
    """A launch backend that always fails at the exact seam `_launch` calls
    right before deriving the context to persist — the rollback case below.

    Inherits the host-namespace bridge so this actually conforms to the
    `LaunchBackend` Protocol rather than satisfying it only by accident of
    which lines happen to execute on this failure path — `.launch()` raises
    before `_launch` ever calls `transcript_context`, so today's test would
    pass without it too, which is exactly the trap: `make lint`'s mypy pass
    doesn't check `tests/`, so nothing catches a fake that quietly isn't the
    real contract until the Protocol grows a member a passing test happens to
    touch."""

    provides_pane: ClassVar[bool] = True

    def launch(self, spec: LaunchSpec) -> None:
        raise RuntimeError("boom")


def test_failed_launch_rolls_back_and_persists_no_context(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A launch failure never gets far enough to derive a `transcript_context`
    at all — `_rollback_create` deletes the whole record transactionally (the
    same guarantee `test_workspace_lifecycle.py`'s init-failure tests pin for
    a fail_fast init), so there is no window where a half-written context
    could survive to be read."""
    del fake_tmux  # the injected backend bypasses tmux entirely; kept for isolation
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=_RaisingLaunchBackend()
    )

    with pytest.raises(GroveError, match="failed to set up tmux session"):
        mgr.create(CreateWorkspaceRequest(agent_name="claude", title="doomed"))

    assert store.load_all() == []


# ─── the namespace bridge: who computes the context ─────────────────────────
#
# A container backend folds the composed `spec.env` into the container's own
# env, so a `CLAUDE_CONFIG_DIR`/`CODEX_HOME` value in that env is a path
# meaningful only INSIDE the container. Recording it verbatim into a field
# documented as a HOST directory would point the host process's own env at a
# container-internal path for the duration of every transcript read — worse
# than recording nothing, because it's an ACTIVE wrong answer, not just a
# missing one. The BACKEND answers `transcript_context` and the manager
# stores that answer verbatim, so "I can't" is simply `None`.


class _UnreachableNamespaceBackend:
    """A backend whose launched process's filesystem paths mean nothing to
    THIS process — the container shape, without actually touching a
    container: it answers `None` because nothing it launched into is
    reachable from here. Proves the MANAGER persists that refusal, independent
    of which concrete backend (today only `DevcontainerLaunchBackend`) produces
    it."""

    provides_pane: ClassVar[bool] = True

    def launch(self, spec: LaunchSpec) -> None:
        del spec

    def transcript_context(self, spec: LaunchSpec) -> TranscriptContext | None:
        del spec
        return None

    def control_path(
        self, host_path: Path, *, share_plan: AgentSharePlan | None = None
    ) -> str | None:
        del share_plan
        return str(host_path)


class _FixedContextBackend:
    """A backend that answers with a context the manager could never derive
    itself — neither field is anything `for_launch` would produce from this
    launch's env or cwd. Any post-processing in `_launch` shows up as a
    mismatch, which is the whole point."""

    provides_pane: ClassVar[bool] = True
    CONTEXT: ClassVar[TranscriptContext] = TranscriptContext(
        config_dir="/host/side/of/the/mount", agent_cwd="/workspace/inside/the/container"
    )

    def launch(self, spec: LaunchSpec) -> None:
        del spec

    def transcript_context(self, spec: LaunchSpec) -> TranscriptContext | None:
        del spec
        return self.CONTEXT

    def control_path(
        self, host_path: Path, *, share_plan: AgentSharePlan | None = None
    ) -> str | None:
        del share_plan
        return str(host_path)


def test_container_launch_records_no_transcript_context(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The most important case: a backend that answers `None` must leave
    `transcript_context` `None` even though the agent pins
    `CLAUDE_CONFIG_DIR` — that path is only meaningful inside the foreign
    namespace the backend launched into."""
    del fake_tmux
    pinned_dir = str(tmp_path / "container-internal-profile")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=_UnreachableNamespaceBackend()
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))

    assert state.transcript_context is None


def test_container_launch_records_no_context_for_codex(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The kind that actually BREAKS without this refusal, so it earns its own
    test despite sharing the same branch as Claude above:
    `_ClaudeHome.config_dirs()` appends the real `~/.claude` after any env
    entries, so Claude survives a wrongly-recorded container path by luck.
    `_CodexHome.base_dir()` is a single directory with NO cascade — a
    recorded container-internal `CODEX_HOME` resolves to a nonexistent host
    path, the rollout glob returns empty, and a working agent axis goes
    blank."""
    del fake_tmux
    pinned_dir = str(tmp_path / "container-internal-codex-home")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [_codex_spec(env={"CODEX_HOME": pinned_dir})],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=_UnreachableNamespaceBackend()
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="codex", title="containerized-codex"))

    assert state.transcript_context is None


def test_manager_persists_the_backends_context_verbatim(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Pins WHERE the policy lives: the manager stores exactly what the
    backend returned, with no derivation, filtering, or repair of its own —
    including an `agent_cwd` that is not a host path at all and a `config_dir`
    the launch env never mentioned. Only the backend knows how its namespace
    maps onto this host, so any manager-side post-processing is a bug."""
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [_claude_spec(env={"CLAUDE_CONFIG_DIR": str(tmp_path / "ignored")})],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=_FixedContextBackend()
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="bridged"))

    assert state.transcript_context == _FixedContextBackend.CONTEXT
    # And it survives the store round-trip, so the read side sees the same pair.
    assert store.load_all()[0].transcript_context == _FixedContextBackend.CONTEXT


def _container_spec(
    tmp_path: Path,
    *,
    kind: str = "claude_code",
    share: str = "full",
    subpath: str = "services/api",
    container: ContainerRuntimeState | None = _PROVISIONED,
) -> LaunchSpec:
    """A container `LaunchSpec` carrying the plan its own provision would have.

    The env below is deliberately populated exactly as the manager composes
    it: the config-dir var holds the CONTAINER path, so a backend that
    answered off `spec.env` would record a directory that does not exist on
    this host and aim every read at it."""
    worktree = tmp_path / "wt"
    plan = AgentSharePlan.plan(
        kind=kind,  # type: ignore[arg-type]
        share=share,  # type: ignore[arg-type]
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "agent-config" / "w1",
        exists=lambda _path: True,
    )
    return LaunchSpec(
        session_name="grove-x",
        cwd=worktree / subpath if subpath else worktree,
        command="claude",
        decoration=(),
        env=dict(plan.env),
        env_unset=(),
        cfg=GroveConfig.model_validate({"container": {"enabled": True}}),
        worktree=worktree,
        kind=kind,  # type: ignore[arg-type]
        container=container,
        share_plan=plan,
    )


def test_container_backend_bridges_the_host_config_dir_to_the_container_cwd(
    tmp_path: Path,
) -> None:
    """The asymmetric pair, and the reason only a backend can produce it:
    `config_dir` crosses the boundary to the HOST side of the agent-config mount
    (the read side scopes its own `CLAUDE_CONFIG_DIR` to it, so it must exist
    here), while `agent_cwd` stays the CONTAINER string — it is matched opaquely
    against the `cwd` each transcript record wrote for itself, and translating it
    to a host path would match nothing, ever."""
    spec = _container_spec(tmp_path)

    ctx = DevcontainerLaunchBackend().transcript_context(spec)

    assert ctx is not None
    assert ctx.config_dir == str(tmp_path / "state" / "agent-config" / "w1")
    # The nested offset is preserved from the CLI's own reported root.
    assert ctx.agent_cwd == "/workspaces/repo/services/api"
    assert Path(ctx.config_dir).is_absolute()


def test_container_backend_never_records_the_container_side_config_dir(
    tmp_path: Path,
) -> None:
    """The launch env's config-dir value is meaningful only INSIDE the
    container, and recording it points the host reader at a path that does
    not exist here — worse than recording nothing, because the read side
    ACTS on what is stored."""
    spec = _container_spec(tmp_path)
    ctx = DevcontainerLaunchBackend().transcript_context(spec)

    assert ctx is not None
    assert ctx.config_dir != spec.env["CLAUDE_CONFIG_DIR"]
    assert not ctx.config_dir.startswith(str(CONTAINER_CONFIG_ROOT))


def test_container_backend_follows_the_mount_table_not_the_share_level(
    tmp_path: Path,
) -> None:
    """`share: projects` binds the real host transcript tree OVER the config
    root's own, so the transcript lands in `~/.claude` rather than the
    workspace's directory. The backend reads that off the mount table, which is
    the whole point of the seam — the workspace directory would be a real host
    path that simply never receives a transcript."""
    spec = _container_spec(tmp_path, share="projects")

    ctx = DevcontainerLaunchBackend().transcript_context(spec)

    assert ctx is not None
    assert ctx.config_dir == str(tmp_path / "home" / ".claude")


def test_container_backend_records_nothing_without_a_provisioned_container(
    tmp_path: Path,
) -> None:
    """No container means no in-container cwd to record, and half a pair is not
    a usable context — the read side would scan the host cwd under a config dir
    only the container writes into."""
    spec = _container_spec(tmp_path, container=None)

    assert DevcontainerLaunchBackend().transcript_context(spec) is None


def test_container_backend_records_nothing_for_a_kind_with_no_config_dir(
    tmp_path: Path,
) -> None:
    """`generic`/`mewbo` have no config-dir concept, so nothing is mounted and
    nothing is reachable — `None` keeps the reader on its own ambient env."""
    for kind in ("generic", "mewbo"):
        assert (
            DevcontainerLaunchBackend().transcript_context(_container_spec(tmp_path, kind=kind))
            is None
        )


def test_a_containerized_workspace_transcript_actually_resolves_on_the_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assertion that would have caught the gap: not that a backend returns
    a value, but that the READ side finds the transcript with it.

    Drives a real containerized `create` (fake devcontainer CLI, no Docker),
    then plants a transcript exactly where the mount implies the in-container
    agent's writes land — the HOST side of the agent-config mount, under the
    encoded CONTAINER cwd — and asks the manager's own read path for it. Every
    piece of the bridge is load-bearing here: a host-side `agent_cwd` would
    encode the wrong folder name, and a container-side `config_dir` would scope
    the read to a directory that does not exist on this host."""
    del fake_tmux
    # The reader's ambient profile is deliberately elsewhere and EMPTY, so a
    # resolution can only come from the recorded context, never from luck.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "reader-home" / ".claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "reader-home")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
            "agents": [_claude_spec()],
        }
    )
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))

    assert state.runtime is Runtime.CONTAINER
    ctx = state.transcript_context
    assert ctx is not None
    assert ctx.agent_cwd == FAKE_REMOTE_FOLDER  # flat project: the CLI's own root
    assert state.agent_session_id is not None
    planted = _write_transcript_at(
        Path(ctx.config_dir),
        state.agent_session_id,
        ctx.agent_cwd,
        mtime=1_760_000_000,
        prompt="hello from inside the container",
    )

    # The container cwd is scanned FIRST, ahead of the host paths (union).
    assert state.transcript_scan_cwds[0] == Path(ctx.agent_cwd)
    # …and the read path — the chokepoint every consumer routes through —
    # resolves the planted file through the recorded context.
    assert mgr.primary_transcript(state.id) == (planted,)
    with mgr.transcript_scope(state):
        assert os.environ["CLAUDE_CONFIG_DIR"] == ctx.config_dir


def test_killing_a_containerized_workspace_leaves_its_transcript_behind(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transcripts outlive worktrees — and containers.

    A host workspace's history survives `kill` because it was written under the
    user's own config dir, which teardown never touches. A containerized one is
    written under `paths.agent_workspace_config_dir` — Grove's OWN per-workspace
    directory, and therefore the one plausible future cleanup target. Teardown
    removes the tmux session, the container, and the worktree; deliberately NOT
    that directory, or killing a workspace would destroy history the equivalent
    host workspace keeps. This pins the asymmetry so a later "tidy up after
    ourselves" change has to argue with a test rather than silently win.
    """
    del fake_tmux
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "reader-home" / ".claude"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "reader-home")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
            "agents": [_claude_spec()],
        }
    )
    mgr = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    ctx = state.transcript_context
    assert ctx is not None
    assert state.agent_session_id is not None
    planted = _write_transcript_at(
        Path(ctx.config_dir),
        state.agent_session_id,
        ctx.agent_cwd,
        mtime=1_760_000_000,
        prompt="work worth keeping",
    )

    mgr.kill(state.id)

    assert not Path(state.worktree_path).exists()  # the worktree really did go
    assert planted.exists()
    # …and it is still READABLE, through the same scoped adapter lookup every
    # post-mortem read uses — not merely present as an orphaned file.
    with mgr.transcript_config_dir_scope("claude_code", ctx.config_dir):
        found = ClaudeCodeAdapter().locate_transcripts(Path(ctx.agent_cwd), state.agent_session_id)
    assert found == [planted]


def test_headless_launch_still_records_transcript_context(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bridge does not disable the feature wholesale: `HeadlessLaunchBackend`
    is paneless (`provides_pane=False`) but still runs ON THIS HOST with
    `spec.env` applied directly, so it answers with the identity bridge — the
    pane and the namespace are orthogonal axes. Drives the REAL backend class
    (not a fake asserting the value) through `create()`, with
    `process.spawn_detached` monkeypatched so no real OS process is spawned —
    the same seam
    `test_headless_backend.py::test_headless_backend_delegates_to_process`
    patches, so this stays a paneless launch rather than a real subprocess."""
    del fake_tmux
    monkeypatch.setattr(process_mod, "spawn_detached", lambda command, **kwargs: 99)
    pinned_dir = str(tmp_path / "claude-profile")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [_claude_spec(env={"CLAUDE_CONFIG_DIR": pinned_dir})],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=store, launch_backend=HeadlessLaunchBackend()
    )

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="headless"))

    assert state.transcript_context is not None
    assert state.transcript_context.config_dir == pinned_dir


def test_every_shipped_backend_answers_the_namespace_bridge(tmp_path: Path) -> None:
    """`LaunchBackend` is a `Protocol` — it gives no runtime default, so a
    concrete backend that adds `launch()` but forgets a bridge method doesn't
    inherit one from anywhere; it raises `AttributeError` the moment `_launch`
    calls it (the same trap that can break `test_headless_backend.py`'s and
    `test_launch_backend.py`'s fakes). Enumerates every backend Grove ships
    today; add the new class to this list when a fourth backend lands, or
    this test stops being the thing that catches it silently answering
    nothing.

    Pins that each backend *answers* both bridge questions, plus the
    identity answer the two host backends owe for `control_path` (a
    rewritten path would name a file the agent cannot open)."""
    backends: list[type] = [TmuxLaunchBackend, HeadlessLaunchBackend, DevcontainerLaunchBackend]
    for backend in backends:
        for member in ("transcript_context", "control_path"):
            assert callable(getattr(backend, member, None)), (
                f"{backend.__name__} does not answer {member}"
            )
        assert isinstance(backend.provides_pane, bool)

    control_file = tmp_path / "grove" / "hooks-settings.json"
    assert TmuxLaunchBackend().control_path(control_file) == str(control_file)
    assert HeadlessLaunchBackend().control_path(control_file) == str(control_file)


# ─── manager.primary_transcript ──────────────────────────────────────────────


def test_primary_transcript_default_is_unaffected_by_a_foreign_config_dir(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    """No override: a transcript sitting under an unrelated directory (what a
    container mount would look like) is simply invisible — exactly today's
    behavior, proving the override is additive, not a behavior change."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="host"))
    assert state.agent_session_id is not None
    foreign_dir = tmp_path / "container-mount"
    _write_transcript_at(
        foreign_dir, state.agent_session_id, "/workspace", mtime=2_000, prompt="container work"
    )

    assert manager.primary_transcript(state.id) == ()


def test_primary_transcript_resolves_via_transcript_context_override(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    """A container-launched agent's transcript, recorded under its own config
    dir + cwd, resolves from the host once the override is set — and the
    ambient env is restored afterward."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    assert state.agent_session_id is not None
    container_cwd = "/workspace"
    mount_dir = tmp_path / "container-mount"
    _write_transcript_at(
        mount_dir, state.agent_session_id, container_cwd, mtime=2_000, prompt="container work"
    )
    ctx = TranscriptContext(config_dir=str(mount_dir), agent_cwd=container_cwd)
    manager.store.save(replace(state, transcript_context=ctx))

    found = manager.primary_transcript(state.id)

    assert len(found) == 1
    assert found[0].name == f"{state.agent_session_id}.jsonl"
    # The ambient env is untouched after the scoped read.
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(claude_home)


# ─── SessionExplorer ─────────────────────────────────────────────────────────


def test_for_workspace_resolves_via_transcript_context_override(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    assert state.agent_session_id is not None
    container_cwd = "/workspace"
    mount_dir = tmp_path / "container-mount"
    _write_transcript_at(
        mount_dir, state.agent_session_id, container_cwd, mtime=2_000, prompt="container work"
    )
    explorer = SessionExplorer(manager)
    assert explorer.for_workspace(state.id) == ()  # no override yet: nothing found

    ctx = TranscriptContext(config_dir=str(mount_dir), agent_cwd=container_cwd)
    manager.store.save(replace(state, transcript_context=ctx))

    listings = explorer.for_workspace(state.id)

    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id]
    assert listings[0].provenance == "grove_launched"


def test_candidates_for_resolves_via_transcript_context_override(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    assert state.agent_session_id is not None
    container_cwd = "/workspace"
    mount_dir = tmp_path / "container-mount"
    _write_transcript_at(
        mount_dir, state.agent_session_id, container_cwd, mtime=2_000, prompt="container work"
    )
    ctx = TranscriptContext(config_dir=str(mount_dir), agent_cwd=container_cwd)
    manager.store.save(replace(state, transcript_context=ctx))

    listings = SessionExplorer(manager).candidates_for(state.id)

    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id]


def test_transcripts_and_turns_for_resolve_via_transcript_context_override(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    """`_session_cwd` already carries the session's own RECORDED (container)
    cwd once discovered — only the config-dir env needs scoping for
    `transcripts`/`turns_for` to find the file at all."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    assert state.agent_session_id is not None
    container_cwd = "/workspace"
    mount_dir = tmp_path / "container-mount"
    _write_transcript_at(
        mount_dir, state.agent_session_id, container_cwd, mtime=2_000, prompt="container work"
    )
    ctx = TranscriptContext(config_dir=str(mount_dir), agent_cwd=container_cwd)
    manager.store.save(replace(state, transcript_context=ctx))
    explorer = SessionExplorer(manager)
    listing = explorer.for_workspace(state.id)[0]

    files = explorer.transcripts(listing)
    turns = explorer.turns_for(listing)

    assert len(files) == 1
    assert len(turns) == 1
    assert turns[0].user_text == "container work"


def test_list_resolves_via_transcript_context_override(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path
) -> None:
    """The project-wide browse (`list`/`scan_roots`) also picks up an override's
    recorded cwd, scoping the config-dir env per matching root."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="containerized"))
    assert state.agent_session_id is not None
    container_cwd = "/workspace"
    mount_dir = tmp_path / "container-mount"
    _write_transcript_at(
        mount_dir, state.agent_session_id, container_cwd, mtime=2_000, prompt="container work"
    )
    ctx = TranscriptContext(config_dir=str(mount_dir), agent_cwd=container_cwd)
    manager.store.save(replace(state, transcript_context=ctx))

    listings = SessionExplorer(manager).list()

    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id]
    assert listings[0].workspace_id == state.id
    assert listings[0].workspace_title == "containerized"


# ─── four reads that must resolve through the pinned config dir ────────────
#
# `WorkspaceManager.transcript_scope` closes the read/write asymmetry at the
# LAUNCH boundary and three read call sites, but four adapter reads can still
# resolve CLAUDE_CONFIG_DIR/CODEX_HOME off the reader's own ambient env
# instead: `SessionExplorer.subagent_turns` (hard 404 on the fleet drill-in —
# its enclosing `for_workspace` IS scoped, but returns before the loop body
# runs), `WorkspaceManager.latest_todo` (empty checklist posted into a Gitea
# issue by the issueops publisher), `session_controls` (the WRONG profile's
# slash commands/skills — wrong content, not missing content), and
# `_current_model` (a blank `current_model`).
#
# Fixture data below is planted ONLY under the pinned dir, never the ambient
# one: unlike the container case, where Claude's config-dir cascade happens
# to still find a bind-mounted transcript at the real `~/.claude`, a PINNED
# HOST PROFILE's transcript exists ONLY in the pinned dir — the cascade never
# reaches it, so nothing here can pass by accident of that fallback rescuing
# Claude specifically.


def test_subagent_turns_no_longer_dead_ends_on_a_pinned_workspace(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """The user-visible symptom: the fleet drill-in
    (`GET /workspaces/{id}/sessions/{thread_id}/turns`) 404'd for every
    pinned workspace. `for_workspace` finding the pinned session at all is
    the load-bearing precondition `subagent_turns` depends on; with no real
    sub-agent fleet the answer is still `None`, but the READ now reaches the
    transcript instead of the reader's empty ambient profile — see the next
    test for direct proof the loop body itself runs inside the scope."""
    mgr, state = pinned
    explorer = SessionExplorer(mgr)
    assert len(explorer.for_workspace(state.id)) == 1
    assert explorer.subagent_turns(state.id, "no-such-thread") is None


def test_subagent_turns_adapter_calls_run_inside_the_scope(
    pinned: tuple[WorkspaceManager, WorkspaceState], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precise proof for the fleet drill-in, not just its return value:
    capture `CLAUDE_CONFIG_DIR` AT CALL TIME inside both adapter projections
    (`subagent_turns`/`fleet_activity`). This is what actually proves the
    loop BODY runs inside the scope. Scoping only the loop HEADER would look
    right and fix nothing (the scope would already be closed by the time
    these calls run, since `for_workspace`'s own scope closes when THAT call
    returns), and a test that only checks the return value would pass even
    if a future change moved the scope back to the header."""
    mgr, state = pinned
    ctx = state.transcript_context
    assert ctx is not None
    seen: dict[str, str] = {}

    def fake_subagent_turns(
        self: ClaudeCodeAdapter,
        cwd: Path,
        session_id: str,
        thread_id: str,
        *,
        last: int | None = None,
    ) -> tuple[object, ...]:
        seen["subagent_turns"] = os.environ.get("CLAUDE_CONFIG_DIR", "")
        return ()

    def fake_fleet_activity(self: ClaudeCodeAdapter, cwd: Path, session_id: str) -> list[object]:
        seen["fleet_activity"] = os.environ.get("CLAUDE_CONFIG_DIR", "")
        return []

    monkeypatch.setattr(ClaudeCodeAdapter, "subagent_turns", fake_subagent_turns)
    monkeypatch.setattr(ClaudeCodeAdapter, "fleet_activity", fake_fleet_activity)

    SessionExplorer(mgr).subagent_turns(state.id, "thread-x")

    assert seen["subagent_turns"] == ctx.config_dir


def test_latest_todo_finds_the_pinned_transcript(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """Highest priority by blast radius: `latest_todo` feeds the issueops
    sticky-comment publisher, which posts the result into a REAL Gitea issue
    comment — an unscoped read posts an empty checklist into the issue, not
    just a blank panel that stays local to one render."""
    mgr, state = pinned
    todo = mgr.latest_todo(state.id)
    assert todo is not None
    assert [item.content for item in todo.items] == ["first task", "second task"]


def test_session_controls_lists_the_pinned_profiles_skill(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """The wrong-content failure, asserted POSITIVELY: an unscoped read lists
    the READER's ambient profile — which is not EMPTY, so a bare
    non-empty/truthy assertion would pass on the wrong data. Assert the
    PINNED-only skill is actually present, the only assertion shape that can
    catch "answered from the wrong profile"."""
    mgr, state = pinned
    controls = mgr.session_controls(state.id)
    assert "pinned-only-skill" in {s.name for s in controls.skills}


def test_current_model_reads_from_the_pinned_transcript(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """Cosmetic relative to the other three, but the same missed-scope risk:
    an unscoped read returns `None` for every pinned workspace regardless of
    what the running session's transcript actually recorded."""
    mgr, state = pinned
    assert mgr.session_controls(state.id).current_model == "claude-opus-5"


def test_session_controls_still_degrades_on_a_broken_worktree(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """`session_controls` keeps its best-effort contract with the scope now
    wrapped around the read: a broken worktree degrades the panel to empty
    controls, it must never raise into a render path — scope or no scope."""
    mgr, state = pinned
    broken = replace(state, worktree_path="/nonexistent/path")
    mgr.store.save(broken)
    controls = mgr.session_controls(broken.id)  # must not raise
    assert controls is not None


def test_env_is_byte_identical_before_and_after_all_four_reads(
    pinned: tuple[WorkspaceManager, WorkspaceState],
) -> None:
    """The env `transcript_scope` mutates is PROCESS-GLOBAL — a leak from any
    ONE of the four sites would corrupt every subsequent read the daemon
    makes for every OTHER workspace, not just this one."""
    mgr, state = pinned
    before = os.environ["CLAUDE_CONFIG_DIR"]

    mgr.latest_todo(state.id)
    mgr.session_controls(state.id)
    SessionExplorer(mgr).subagent_turns(state.id, "x")

    assert os.environ["CLAUDE_CONFIG_DIR"] == before
