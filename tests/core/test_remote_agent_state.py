"""A REMOTE workspace's agent axis comes from what its worker pushed (#1030).

The workload's transcript never reaches this disk, so every fixture here has
none: a session, a state and an event time can only come from the sidecar the
worker's spool drops fold into. Before this a remote row had no clock, the idle
rule could never pass, and freeze and hibernate were unreachable for the one
runtime they were built for.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core import paths
from grove.core.activity import ActivityService, WorkspaceActivity
from grove.core.agents.hook import ClaudeHook
from grove.core.agents.model import AgentActivityState
from grove.core.config import GroveConfig
from grove.core.idle import IdleRule
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.runtimes import RemoteHandle
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

_WS = "e" * 32
_SESSION = "11111111-2222-3333-4444-555555555555"


def _state(tmp_path: Path, status: WorkspaceStatus = WorkspaceStatus.ACTIVE) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=_WS,
        title="remote",
        repo_root=str(tmp_path),
        branch="grove/r",
        base_branch="main",
        worktree_path=str(tmp_path / "wt"),
        tmux_session="",
        agent_name="claude",
        status=status,
        created_at=now - timedelta(hours=1),
        updated_at=now,
        agent_kind="claude_code",
        agent_session_id=_SESSION,
        native=True,
        runtime=Runtime.REMOTE,
        remote=RemoteHandle("sandbox", "sbx-1"),
    )


def _manager(tmp_path: Path) -> WorkspaceManager:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(replace(_state(tmp_path), status=WorkspaceStatus.RUNNING))
    return WorkspaceManager(repo_root=tmp_path, cfg=GroveConfig(), store=store)


def _push(event: str, at: datetime) -> None:
    """What the daemon's spool route leaves behind for one worker turn edge."""
    ClaudeHook.record_event(
        {"hook_event_name": event, "session_id": _SESSION},
        sidecar_dir=paths.agent_sidecar_dir(),
        tmux_pane=None,
        now=at,
    )


def _row(tmp_path: Path, state: WorkspaceState) -> WorkspaceActivity:
    mgr = _manager(tmp_path)
    service = ActivityService(registry=RepoRegistry(cfg=GroveConfig(), store=mgr._store))
    return replace(_bare_row(state), sessions=tuple(service.sessions_for(mgr, state)))


def _bare_row(state: WorkspaceState) -> WorkspaceActivity:
    return WorkspaceActivity(
        state=state,
        sessions=(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=datetime.now(UTC),
    )


def test_a_pushed_turn_end_gives_the_remote_row_a_state_and_a_clock(tmp_path: Path) -> None:
    ended = datetime.now(UTC) - timedelta(minutes=30)
    _push("Stop", ended)
    row = _row(tmp_path, _state(tmp_path))
    [primary] = row.sessions
    assert primary.session.session_id == _SESSION
    assert primary.activity.state is AgentActivityState.WAITING
    assert primary.activity.last_event_at == ended
    # The point of the ticket: a resting remote past its cooldown is idle.
    assert IdleRule(cooldown=timedelta(minutes=15)).idle(row, datetime.now(UTC))


def test_a_remote_that_never_pushed_is_never_idle(tmp_path: Path) -> None:
    row = _row(tmp_path, _state(tmp_path))
    assert all(s.activity.last_event_at is None for s in row.sessions)
    assert not IdleRule(cooldown=timedelta(minutes=15)).idle(row, datetime.now(UTC))


def test_a_running_turn_keeps_the_remote_working(tmp_path: Path) -> None:
    _push("UserPromptSubmit", datetime.now(UTC) - timedelta(seconds=5))
    row = _row(tmp_path, _state(tmp_path))
    assert row.sessions[0].activity.state is AgentActivityState.WORKING
    assert not IdleRule(cooldown=timedelta(seconds=1)).idle(row, datetime.now(UTC))


def test_an_unreachable_worker_never_reads_idle_off_its_last_push(tmp_path: Path) -> None:
    """The worker's last word was a turn end, and then it went away: the
    workspace reads OFFLINE, and only the guard keeps that stale WAITING from
    handing the idle watcher a clock to freeze it on."""
    _push("Stop", datetime.now(UTC) - timedelta(hours=1))
    row = _row(tmp_path, _state(tmp_path, WorkspaceStatus.OFFLINE))
    assert row.sessions[0].activity.last_event_at is None
    assert row.sessions[0].activity.state is not AgentActivityState.WAITING


def test_the_sessions_listing_names_the_remote_session_without_a_file(tmp_path: Path) -> None:
    ended = datetime.now(UTC) - timedelta(minutes=2)
    _push("Stop", ended)
    [listing] = SessionExplorer(_manager(tmp_path)).for_workspace(_WS)
    assert listing.summary.session_id == _SESSION
    assert listing.provenance == "grove_launched"
    assert listing.summary.modified_at == ended
    assert listing.summary.transcript_path is None
    assert listing.summary.size_bytes is None


def test_a_remote_with_no_push_lists_no_session(tmp_path: Path) -> None:
    assert SessionExplorer(_manager(tmp_path)).for_workspace(_WS) == ()
