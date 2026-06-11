"""ActivityService: cross-project snapshot, the status-blend policy, delta bus.

Real tmp git repos + the FakeTmux seam + a shared store/registry. The blend
truth table is tested directly against the pure staticmethod (the single policy
site); the rest goes through the real snapshot/poll paths with in-memory fakes.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.agents import AgentActivity, AgentActivityState
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.activity import DashboardEvent, DashboardSnapshotView
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceStatus
from tests.conftest import FakeTmux


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@grove.local"],
        ["config", "user.name", "Grove Test"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


@pytest.fixture
def env(fake_tmux: FakeTmux, tmp_path: Path) -> tuple[ActivityService, RepoRegistry]:
    cfg = GroveConfig.model_validate({"tmux": {"session_prefix": "test-"}})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    return ActivityService(registry=registry), registry


# ─── blend truth table (the single policy site) ─────────────────────────────


@pytest.mark.parametrize(
    ("ws_status", "transcript_state", "has_file", "provenance", "expected"),
    [
        # No file: STARTING only for a grove_launched session (awaiting its first
        # turn). An fs_discovered file that vanished between discover and read →
        # UNKNOWN, never a false "starting".
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "grove_launched",
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WAITING,
            False,
            "grove_launched",
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "fs_discovered",
            AgentActivityState.UNKNOWN,
        ),
        # An ended turn stays WAITING even when tmux is fresh.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WAITING,
            True,
            "grove_launched",
            AgentActivityState.WAITING,
        ),
        # tool_use tail: ACTIVE tmux confirms WORKING; quiet tmux → IDLE. Provenance
        # is irrelevant once a file exists.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            AgentActivityState.WORKING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            AgentActivityState.IDLE,
        ),
        (
            WorkspaceStatus.OFFLINE,
            AgentActivityState.WORKING,
            True,
            "fs_discovered",
            AgentActivityState.IDLE,
        ),
        # Definitive transcript signals pass through.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.UNKNOWN,
            True,
            "grove_launched",
            AgentActivityState.UNKNOWN,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.ERROR,
            True,
            "fs_discovered",
            AgentActivityState.ERROR,
        ),
    ],
)
def test_blend_truth_table(
    ws_status: WorkspaceStatus,
    transcript_state: AgentActivityState,
    has_file: bool,
    provenance: str,
    expected: AgentActivityState,
) -> None:
    transcript = AgentActivity(state=transcript_state)
    assert (
        ActivityService._blend(ws_status, transcript, has_file=has_file, provenance=provenance)
        is expected
    )


# ─── snapshot ───────────────────────────────────────────────────────────────


def test_snapshot_groups_across_repos(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo_a = _init_repo(tmp_path / "repo_a")
    repo_b = _init_repo(tmp_path / "repo_b")
    registry.get(repo_a).create(CreateWorkspaceRequest(agent_name="claude", title="a-task"))
    registry.get(repo_b).create(CreateWorkspaceRequest(agent_name="shell", title="b-task"))

    snap = service.snapshot()

    assert snap.total_workspaces == 2
    names = {g.repo_name for g in snap.projects}
    assert names == {"repo_a", "repo_b"}
    # The shell workspace tracks no session; the claude one has one (STARTING — no
    # transcript on disk yet).
    by_name = {g.repo_name: g for g in snap.projects}
    claude_row = by_name["repo_a"].workspaces[0]
    shell_row = by_name["repo_b"].workspaces[0]
    assert len(claude_row.sessions) == 1
    assert claude_row.primary is not None
    assert claude_row.primary.state is AgentActivityState.STARTING
    assert shell_row.sessions == ()


def test_snapshot_parses_real_transcript(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="track"))
    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"do the thing"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","model":"claude-opus-4-8",'
        '"stop_reason":"end_turn","usage":{"input_tokens":10,"output_tokens":2},'
        '"content":[{"type":"text","text":"done"}]}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.human_turns == 1
    assert primary.current_task == "do the thing"
    # has_file True + transcript WAITING (end_turn) → WAITING.
    assert primary.state is AgentActivityState.WAITING


def test_snapshot_never_calls_peek(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The list path uses the cheap diff_stats, never the expensive peek/full-diff."""
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="cheap"))
    monkeypatch.setattr(mgr, "peek", lambda *a, **k: pytest.fail("snapshot must not call peek()"))

    row = service.snapshot().projects[0].workspaces[0]
    assert isinstance(row.diff_added, int)
    assert isinstance(row.diff_removed, int)


# ─── delta bus ──────────────────────────────────────────────────────────────


def test_poll_emits_session_activity_delta_on_change(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="poll"))
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    transcript = folder / f"{state.agent_session_id}.jsonl"

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)

    service.poll_once()  # primes fingerprints (STARTING, no file)
    deltas.clear()

    # Activity changes: a transcript appears → STARTING → WAITING.
    transcript.write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n',
        encoding="utf-8",
    )
    service.poll_once()

    activity_deltas = [d for d in deltas if d.kind == "session_activity"]
    assert len(activity_deltas) == 1
    assert activity_deltas[0].workspace_id == state.id
    assert activity_deltas[0].workspace is not None
    assert activity_deltas[0].workspace.primary is not None
    assert activity_deltas[0].workspace.primary.state is AgentActivityState.WAITING

    # No further change → no new delta.
    deltas.clear()
    service.poll_once()
    assert [d for d in deltas if d.kind == "session_activity"] == []


def test_lifecycle_event_bridged_to_workspace_changed(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="bridge"))

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)  # bridges the now-known repo's manager bus

    mgr.update(state.id, title="renamed")

    changed = [d for d in deltas if d.kind == "workspace_changed"]
    assert any(d.workspace_id == state.id and d.detail.get("event") == "updated" for d in changed)


def test_unsubscribe_is_idempotent(env: tuple[ActivityService, RepoRegistry]) -> None:
    service, _ = env
    unsub = service.subscribe(lambda _d: None)
    unsub()
    unsub()  # second call must not raise


# ─── wire views ─────────────────────────────────────────────────────────────


def test_snapshot_view_serializes(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="view"))

    view = DashboardSnapshotView.from_snapshot(service.snapshot())
    payload = view.model_dump_json()
    assert '"total_workspaces":1' in payload
    assert view.projects[0].workspaces[0].state.title == "view"


def test_event_from_delta_round_trips(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="ev"))
    row = service.snapshot().projects[0].workspaces[0]
    delta = DashboardDelta(
        kind="session_activity", seq=7, workspace_id=state.id, repo_root=str(repo), workspace=row
    )
    event = DashboardEvent.from_delta(delta)
    assert event.kind == "session_activity"
    assert event.seq == 7
    assert event.workspace is not None
    assert event.workspace.state.id == state.id


# ─── #18 push-status sidecar + out-of-band discovery ────────────────────────


def test_sidecar_overrides_polled_state(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="blk"))

    # No transcript on disk → polled blend would be STARTING. A fresh hook sidecar
    # for a permission prompt wins: the dashboard shows BLOCKED (polling can't see it).
    ClaudeHook.record_event(
        {"hook_event_name": "Notification", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )
    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.state is AgentActivityState.BLOCKED


def test_fs_discovery_surfaces_handstarted_session(
    fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "hooks": {"enabled": True}}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    service = ActivityService(registry=registry)

    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="disc"))
    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    # A session the user started by hand in the worktree — same cwd, different id.
    (folder / "99999999-9999-4999-8999-999999999999.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        '"timestamp":"2026-06-01T10:00:00.000Z","isSidechain":false,'
        '"message":{"role":"user","content":"hand started"}}\n',
        encoding="utf-8",
    )

    sessions = service.snapshot().projects[0].workspaces[0].sessions
    provenances = {s.session.provenance for s in sessions}
    assert "grove_launched" in provenances
    assert "fs_discovered" in provenances
    assert len(sessions) == 2


def test_null_session_id_recovered_by_discovery(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A claude_code workspace with no minted ``agent_session_id`` (a legacy
    record, or an agent configured without ``kind="claude_code"`` so creation
    never minted one) still surfaces its live session via out-of-band discovery.

    This is the root cause of the dashboard's "unknown / no agent session"
    state: ``sessions_for`` used to early-return ``[]`` whenever the id was
    falsy, blanking the whole agent axis even though a real transcript existed
    on disk for the worktree.
    """
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="legacy"))
    worktree = Path(state.worktree_path)
    # The broken on-disk shape: persisted with no minted id (no hooks needed).
    mgr.store.save(replace(state, agent_session_id=None))

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / "11111111-1111-4111-8111-111111111111.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        '"timestamp":"2026-06-01T10:00:00.000Z","isSidechain":false,'
        '"message":{"role":"user","content":"recover me"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.primary is not None
    assert row.sessions[0].session.provenance == "fs_discovered"
    assert row.primary.current_task == "recover me"


def test_create_persists_agent_kind(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """create() captures the resolved agent's kind on the record, so the
    dashboard can resolve the adapter without re-reading (possibly repo-scoped)
    config."""
    _, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    assert mgr.create(CreateWorkspaceRequest(agent_name="claude", title="k")).agent_kind == (
        "claude_code"
    )
    assert mgr.create(CreateWorkspaceRequest(agent_name="shell", title="s")).agent_kind == (
        "generic"
    )


def test_persisted_agent_kind_resolves_when_name_absent_from_config(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dashboard resolves a workspace's adapter from the persisted
    ``agent_kind``, NOT the live config — so an agent scoped to a repo's project
    config (invisible to the daemon's global config) still surfaces its sessions.
    This is what lets the 'Simplify' profile live only in mifflin/dunder while
    the daemon dashboard keeps resolving those workspaces."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="scoped"))
    worktree = Path(state.worktree_path)
    # Agent name is NOT in the daemon's config; only the persisted kind says claude_code.
    mgr.store.save(
        replace(
            state,
            agent_name="Claude Code (Simplify)",
            agent_session_id=None,
            agent_kind="claude_code",
        )
    )

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / "abcdef00-0000-4000-8000-000000000000.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        '"timestamp":"2026-06-01T10:00:00.000Z","isSidechain":false,'
        '"message":{"role":"user","content":"scoped session"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.primary is not None
    assert row.primary.current_task == "scoped session"


def test_null_id_discovery_adopts_single_most_recent(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no minted id and several transcripts for the worktree, exactly one
    session (the most recently active) is surfaced — never the worktree's whole
    history. Discovery orders newest-first; the null-id path adopts only [:1]."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="multi"))
    worktree = Path(state.worktree_path)
    mgr.store.save(replace(state, agent_session_id=None))

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    newest = "33333333-3333-4333-8333-333333333333"
    for sid, mtime in (("22222222-2222-4222-8222-222222222222", 1000), (newest, 2000)):
        path = folder / f"{sid}.jsonl"
        path.write_text(
            f'{{"type":"user","uuid":"u-{sid[:4]}","cwd":"{worktree}",'
            '"timestamp":"2026-06-01T10:00:00.000Z","isSidechain":false,'
            f'"message":{{"role":"user","content":"task {sid[:4]}"}}}}\n',
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.sessions[0].session.session_id == newest


# ─── dirty_files (uncommitted churn streams before any commit) ───────────────


def test_dirty_files_change_emits_delta(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """An uncommitted file in the worktree changes the fingerprint and streams
    a delta — the agent-is-editing signal must not wait for a commit."""
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="dirty"))

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)
    service.poll_once()  # primes fingerprints
    deltas.clear()

    (Path(state.worktree_path) / "scratch.txt").write_text("wip\n", encoding="utf-8")
    service.poll_once()

    rows = [d for d in deltas if d.kind == "session_activity"]
    assert len(rows) == 1
    assert rows[0].workspace is not None
    assert rows[0].workspace.dirty_files == 1


def test_dirty_files_best_effort_zero_when_worktree_gone(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="gone"))
    registry.get(repo).pause(state.id)  # removes the worktree dir

    row = service.snapshot().projects[0].workspaces[0]
    assert row.dirty_files == 0
