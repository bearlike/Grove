"""ActivityService: cross-project snapshot, the status-blend policy, delta bus.

Real tmp git repos + the FakeTmux seam + a shared store/registry. The blend
truth table is tested directly against the pure staticmethod (the single policy
site); the rest goes through the real snapshot/poll paths with in-memory fakes.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.activity import ActivityService, DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.activity import DashboardEvent, DashboardSnapshotView
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


def _iso(dt: datetime) -> str:
    """A transcript-record timestamp string for a given birth instant.

    Several fixtures below need a session born *after* the workspace's own
    ``created_at`` (real wall-clock time at test run) to pass the created_at
    adoption gate (`WorkspaceState.adopts_session`) — a timestamp hard-coded to
    a fixed past date would predate it and get filtered as stale.
    """
    return dt.isoformat().replace("+00:00", "Z")


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
    ("ws_status", "transcript_state", "has_transcript", "provenance", "remote", "expected"),
    [
        # Nothing materialized (no file AND nothing parseable — remote adapters
        # count via parsed state instead of files): STARTING only for a
        # grove_launched session (awaiting its first turn). An fs_discovered
        # file that vanished between discover and read → UNKNOWN, never a
        # false "starting".
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "grove_launched",
            False,
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WAITING,
            False,
            "grove_launched",
            False,
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "fs_discovered",
            False,
            AgentActivityState.UNKNOWN,
        ),
        # An ended turn stays WAITING even when tmux is fresh.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WAITING,
            True,
            "grove_launched",
            False,
            AgentActivityState.WAITING,
        ),
        # tool_use tail: ACTIVE tmux confirms WORKING; quiet tmux → IDLE. Provenance
        # is irrelevant once a file exists.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            False,
            AgentActivityState.WORKING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            False,
            AgentActivityState.IDLE,
        ),
        (
            WorkspaceStatus.OFFLINE,
            AgentActivityState.WORKING,
            True,
            "fs_discovered",
            False,
            AgentActivityState.IDLE,
        ),
        # Definitive transcript signals pass through.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.UNKNOWN,
            True,
            "grove_launched",
            False,
            AgentActivityState.UNKNOWN,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.ERROR,
            True,
            "fs_discovered",
            False,
            AgentActivityState.ERROR,
        ),
        # BLOCKED is definitive too — erasing a needs-input prompt to IDLE on a
        # quiet pane was exactly the wrong signal to drop.
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.BLOCKED,
            True,
            "grove_launched",
            False,
            AgentActivityState.BLOCKED,
        ),
        # Remote adapter: the backend's WORKING is authoritative — the local pane
        # runs a bare shell, so tmux-quiet must not demote it to IDLE.
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            True,
            AgentActivityState.WORKING,
        ),
    ],
)
def test_blend_truth_table(
    ws_status: WorkspaceStatus,
    transcript_state: AgentActivityState,
    has_transcript: bool,
    provenance: str,
    remote: bool,
    expected: AgentActivityState,
) -> None:
    transcript = AgentActivity(state=transcript_state)
    assert (
        ActivityService._blend(
            ws_status,
            transcript,
            has_transcript=has_transcript,
            provenance=provenance,
            remote=remote,
            now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        )
        is expected
    )


def test_blend_fresh_transcript_outranks_quiet_pane() -> None:
    """The adapter's abstraction wins over the tmux heuristic: a WORKING
    transcript that advanced recently stays WORKING through a quiet pane (a
    thinking/long-tool agent emits no output — demoting on the pane alone was
    the flaky WORKING→IDLE flapping). Only when the transcript itself has gone
    stale does the quiet pane demote to IDLE (the killed-mid-tool case)."""
    now = datetime(2026, 6, 1, 10, 10, 0, tzinfo=UTC)

    def blend(last_event_at: datetime | None) -> AgentActivityState:
        return ActivityService._blend(
            WorkspaceStatus.IDLE,
            AgentActivity(state=AgentActivityState.WORKING, last_event_at=last_event_at),
            has_transcript=True,
            provenance="grove_launched",
            remote=False,
            now=now,
        )

    assert blend(now - timedelta(seconds=30)) is AgentActivityState.WORKING
    assert blend(now - timedelta(seconds=3600)) is AgentActivityState.IDLE
    assert blend(None) is AgentActivityState.IDLE


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
    # has_transcript True + transcript WAITING (end_turn) → WAITING.
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


# ─── #109 live pending question ──────────────────────────────────────────────


def _ask_capture(sidecar_dir: Path, session_id: str | None, *, now: datetime) -> None:
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "questions": [
                    {
                        "question": "Pick a color",
                        "header": "Color",
                        "multiSelect": False,
                        "options": [{"label": "Blue"}, {"label": "Green"}],
                    }
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=now,
    )


def test_live_question_surfaces_on_activity_view(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A question captured at ask-time rides the activity view immediately —
    before Claude Code flushes anything to the transcript (#109)."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    _ask_capture(sidecar_dir, state.agent_session_id, now=datetime.now(tz=UTC))

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert len(primary.questions) == 1
    q = primary.questions[0]
    assert q.prompt == "Pick a color"
    assert q.group_id == "toolu_1"  # the answer-back tool_use_id
    assert [o.label for o in q.options] == ["Blue", "Green"]


def test_live_question_surfaces_whole_batch_in_order(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One AskUserQuestion call carries up to four questions answered atomically,
    so the whole group rides together, ordered as asked (#109 contract)."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": state.agent_session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "questions": [
                    {"question": "Color?", "options": [{"label": "Blue"}]},
                    {"question": "Toppings?", "multiSelect": True, "options": [{"label": "A"}]},
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert [q.prompt for q in primary.questions] == ["Color?", "Toppings?"]
    # Same batch → shared group_id (the answer-back tool_use_id); distinct ids.
    assert {q.group_id for q in primary.questions} == {"toolu_1"}
    assert [q.id for q in primary.questions] == ["toolu_1#0", "toolu_1#1"]


def test_live_question_suppressed_once_transcript_resolves_it(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Once the transcript carries the resolving tool_result for the captured
    tool_use_id (the flush after an answer/cancel), the pending question is not
    exposed — even though the sidecar still holds the capture."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    # Capture asked at 10:00:00; the transcript then flushes the tool_use + its
    # resolving tool_result at 10:00:05/06 (after the ask) — so the cross-check
    # fires and finds the resolution.
    _ask_capture(
        sidecar_dir, state.agent_session_id, now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    )
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:05.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"tool_use",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"tool_use",'
        '"id":"toolu_1","name":"AskUserQuestion","input":{"questions":[{"question":"Pick a color",'
        '"options":[{"label":"Blue"}]}]}}]}}\n'
        '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:06.000Z","isSidechain":false,'
        '"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1",'
        '"content":"Your questions have been answered"}]}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.questions == ()


def test_sidecar_superseded_by_newer_transcript(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Stop sidecar (WAITING) written BEFORE the transcript's last record is
    stale — the steered agent's polled state must win, not pin WAITING."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="steer"))

    # Push (Stop → WAITING) at 10:00:00; transcript then advances at 10:00:05.
    ClaudeHook.record_event(
        {"hook_event_name": "Stop", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:05.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"follow-up"}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    # Human-turn tail → transcript WORKING; tmux quiet (FakeTmux) → polled IDLE.
    # The point: NOT the sidecar's WAITING.
    assert primary.state is not AgentActivityState.WAITING


def test_degraded_read_keeps_last_definitive_state(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hysteresis: once a session has shown a definitive state, a transient
    collapsed read (vanished/unreadable transcript → would-be STARTING) keeps
    the last definitive state instead of flashing the card back to starting."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="hys"))
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    transcript = folder / f"{state.agent_session_id}.jsonl"
    transcript.write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n',
        encoding="utf-8",
    )

    first = service.snapshot().projects[0].workspaces[0].primary
    assert first is not None and first.state is AgentActivityState.WAITING

    transcript.unlink()  # the transient collapse (mid-rotation / racing read)
    second = service.snapshot().projects[0].workspaces[0].primary
    assert second is not None
    assert second.state is AgentActivityState.WAITING  # not STARTING


def test_settled_working_expires_into_honest_starting(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A settled WORKING ages out on the sidecar window (the dead-agent guard).

    With hooks on, SessionStart settles WORKING before any transcript exists; a
    workspace whose agent dies at boot — or is never prompted — must fall back
    to the honest degraded state once the window passes, not read WORKING
    forever from the hysteresis cache.
    """
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="dead"))

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=t0,
    )
    sid = state.agent_session_id
    assert sid is not None
    mgr = registry.get(repo)

    def state_at(now: datetime) -> AgentActivityState:
        return service._session_activity(
            mgr, state, "claude_code", sid, "grove_launched", now
        ).activity.state

    # Tick 1 (within the sidecar window): the push wins, WORKING settles.
    assert state_at(t0) is AgentActivityState.WORKING
    # Tick 2 (past the window, still no transcript): the cached WORKING must
    # expire rather than answer the degraded read forever.
    assert state_at(t0 + timedelta(seconds=600)) is AgentActivityState.STARTING


def test_fingerprint_covers_every_session_not_just_primary() -> None:
    """A secondary session's state change — and the sessions set going empty —
    must change the fingerprint, or hand-started sessions and discovery misses
    never stream."""
    t0 = datetime(2026, 6, 1, tzinfo=UTC)

    def row(sessions: tuple[SessionActivity, ...]) -> WorkspaceActivity:
        return WorkspaceActivity(
            state=WorkspaceState(
                id="w1",
                title="t",
                repo_root="/r",
                branch="b",
                base_branch="main",
                worktree_path="/w",
                tmux_session="s",
                agent_name="claude",
                status=WorkspaceStatus.RUNNING,
                created_at=t0,
                updated_at=t0,
            ),
            sessions=sessions,
            base_ahead=0,
            base_behind=0,
            diff_added=0,
            diff_removed=0,
            dirty_files=0,
            pane_target=None,
            recent_commits=(),
            observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        )

    def sess(sid: str, st: AgentActivityState) -> SessionActivity:
        return SessionActivity(
            session=AgentSession(
                session_id=sid,
                transcript_path=None,
                adapter_kind="claude_code",
                provenance="fs_discovered",
            ),
            activity=AgentActivity(state=st),
        )

    primary = sess("a", AgentActivityState.WORKING)
    two = row((primary, sess("b", AgentActivityState.WORKING)))
    two_changed = row((primary, sess("b", AgentActivityState.WAITING)))
    assert two.fingerprint != two_changed.fingerprint  # secondary streams
    assert row(()).fingerprint != two.fingerprint  # emptied set streams


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
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / "11111111-1111-4111-8111-111111111111.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"recover me"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.primary is not None
    assert row.sessions[0].session.provenance == "fs_discovered"
    assert row.primary.current_task == "recover me"


def test_unmaterialized_minted_id_yields_primary_to_discovered_session(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A minted id whose transcript never materialized must not pin the
    workspace on STARTING forever: an in-process rotation (``/clear`` mints a
    NEW session id inside the same claude) leaves the live session under a
    different id in the same cwd. The newest discovered session takes the
    primary slot (ungated by hooks); the minted entry stays behind it so it
    takes back over if it ever materializes."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="rotated"))
    worktree = Path(state.worktree_path)
    # The minted id exists on the record but no transcript was ever written
    # for it; the post-/clear session lives under a different id in this cwd.
    live_sid = "deadbeef-0000-4000-8000-000000000000"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / f"{live_sid}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"the live session"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert [s.session.provenance for s in row.sessions] == ["fs_discovered", "grove_launched"]
    assert row.sessions[0].session.session_id == live_sid
    assert row.primary is not None
    assert row.primary.current_task == "the live session"
    assert row.primary.state is not AgentActivityState.STARTING


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
    This is what lets the 'Work' profile live only in private-repos while
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
            agent_name="Claude Code (Work)",
            agent_session_id=None,
            agent_kind="claude_code",
        )
    )

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / "abcdef00-0000-4000-8000-000000000000.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
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
    session (the most recently active, among those adopted) is surfaced —
    never the worktree's whole history. Discovery orders newest-first; the
    null-id path walks that order for the first one `adopts_session` accepts."""
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
    born = _iso(state.created_at + timedelta(seconds=1))
    newest = "33333333-3333-4333-8333-333333333333"
    for sid, mtime in (("22222222-2222-4222-8222-222222222222", 1000), (newest, 2000)):
        path = folder / f"{sid}.jsonl"
        path.write_text(
            f'{{"type":"user","uuid":"u-{sid[:4]}","cwd":"{worktree}",'
            f'"timestamp":"{born}","isSidechain":false,'
            f'"message":{{"role":"user","content":"task {sid[:4]}"}}}}\n',
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.sessions[0].session.session_id == newest


def test_root_workspace_never_promotes_stale_repo_root_session_to_primary(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The bug report's exact shape: a ROOT-placement workspace's cwd IS the
    shared repo root, which may already hold a much older session's transcript
    from before this workspace existed. The dashboard must not present that
    stale transcript as this brand-new workspace's live session (honest
    STARTING instead) — it still rides along as a non-primary member so it
    isn't silently dropped from the fingerprint."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")

    stale_sid = "88888888-8888-4888-8888-888888888888"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(repo)
    folder.mkdir(parents=True)
    path = folder / f"{stale_sid}.jsonl"
    path.write_text(
        f'{{"type":"user","uuid":"u","cwd":"{repo}",'
        '"timestamp":"2020-01-01T00:00:00.000Z","isSidechain":false,'
        '"message":{"role":"user","content":"leftover from a prior life"}}\n',
        encoding="utf-8",
    )
    os.utime(path, (99_999_999, 99_999_999))  # newest mtime around — the bug's trigger

    state = registry.get(repo).create(
        CreateWorkspaceRequest(agent_name="claude", title="fresh root", branch_plan=RootBranch())
    )
    assert Path(state.worktree_path).resolve() == repo.resolve()

    row = service.snapshot().projects[0].workspaces[0]
    assert row.primary is not None
    assert row.primary.state is AgentActivityState.STARTING
    assert row.primary.current_task != "leftover from a prior life"
    assert row.sessions[0].session.session_id != stale_sid


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


def test_snapshot_includes_config_declared_empty_project(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A config-declared repo with zero workspaces surfaces as a ProjectGroup, so
    the webapp new-workspace dialog (which reads `/activity`) can target it (#95)."""
    empty_repo = _init_repo(tmp_path / "empty")
    cfg = GroveConfig.model_validate({"projects": [str(empty_repo)]})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    service = ActivityService(registry=RepoRegistry(cfg=cfg, store=store))

    snap = service.snapshot()
    by_root = {g.repo_root: g for g in snap.projects}
    assert str(empty_repo) in by_root
    assert by_root[str(empty_repo)].workspaces == ()


def test_snapshot_groups_nested_projects_distinctly(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """Two declared subdirs of one repo each surface as a distinct ProjectGroup,
    with each workspace attributed to its cwd group while sharing the repo (#101)."""
    repo = _init_repo(tmp_path / "mono")
    homelab = repo / "homelab"
    homelab.mkdir()
    (homelab / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "homelab", "--no-verify"], cwd=repo, check=True, capture_output=True
    )

    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "projects": [str(repo), str(homelab)]}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    service = ActivityService(registry=registry)
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="shell", title="root-task"))
    mgr.create(CreateWorkspaceRequest(agent_name="shell", title="nested-task", project_cwd=homelab))

    snap = service.snapshot()

    by_cwd = {g.cwd: g for g in snap.projects}
    assert set(by_cwd) == {str(repo.resolve()), str(homelab.resolve())}
    # Both anchor at the same repo root; the workspaces split by cwd.
    assert all(g.repo_root == str(repo.resolve()) for g in snap.projects)
    assert [w.state.title for w in by_cwd[str(repo.resolve())].workspaces] == ["root-task"]
    assert [w.state.title for w in by_cwd[str(homelab.resolve())].workspaces] == ["nested-task"]
