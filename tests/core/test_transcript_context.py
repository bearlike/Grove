"""Transcript context override — reads across a container runtime boundary (#147).

A container-launched agent's own transcript records a cwd (and lives under a
config dir) that can never equal the host's `worktree_path`/ambient
`CLAUDE_CONFIG_DIR` — so a host-side read with no override searches the wrong
folder entirely. `WorkspaceState.transcript_context` is the optional per-
workspace override; these tests pin the default (no override = byte-for-byte
current behavior) and the override path across the pure state helper, the
store round-trip, and the manager/`SessionExplorer` read call sites.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import TranscriptContext, WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux

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


# ─── pure state helper: transcript_scan_cwds ────────────────────────────────


def test_transcript_scan_cwds_defaults_to_scan_cwds() -> None:
    """No override: byte-for-byte the existing `scan_cwds` union."""
    state = _state(project_subpath="sub")
    assert state.transcript_context is None
    assert state.transcript_scan_cwds == state.scan_cwds
    assert len(state.transcript_scan_cwds) == 2  # nested: agent_cwd != worktree root


def test_transcript_scan_cwds_override_replaces_the_union() -> None:
    """An override REPLACES the host union — there is exactly one true cwd for
    a session recorded under a different runtime context."""
    ctx = TranscriptContext(config_dir="/mnt/host-config", agent_cwd="/workspace/sub")
    state = _state(project_subpath="sub", transcript_context=ctx)
    assert state.transcript_scan_cwds == (Path("/workspace/sub"),)


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
    """The exact #147 shape: a container-launched agent's transcript, recorded
    under its own config dir + cwd, resolves from the host once the override
    is set — and the ambient env is restored afterward."""
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
