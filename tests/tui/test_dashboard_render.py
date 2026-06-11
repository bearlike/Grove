"""Pure-function tests for the Activity Dashboard tile render (`_render_card_body`).

Mirrors ``test_card_render.py`` for the list card: the tile body is the dashboard's
visual contract, and pinning its plain-text content + shape (compact vs. promoted,
the root-placement tag, the live-pane fill) is reachable without a Pilot — which
keeps these fast and free of Textual app construction. Focus chrome is TCSS-only
and lives in the Pilot tests under ``test_dashboard.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grove.core.activity import SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.workspace import Placement, WorkspaceState, WorkspaceStatus
from grove.tui.widgets.dashboard_grid import _render_card_body, is_promoted

_NOW = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def _state(**overrides: object) -> WorkspaceState:
    base = {
        "id": "wid-1",
        "title": "fix-auth",
        "repo_root": "/tmp/repo",
        "branch": "feat/auth",
        "base_branch": "main",
        "worktree_path": "/tmp/wt",
        "tmux_session": "test-fix-auth",
        "agent_name": "claude",
        "status": WorkspaceStatus.ACTIVE,
        "created_at": _NOW - timedelta(minutes=10),
        "updated_at": _NOW - timedelta(minutes=4),
    }
    base.update(overrides)
    return WorkspaceState(**base)  # type: ignore[arg-type]


def _activity(
    state: WorkspaceState | None = None,
    *,
    agent_state: AgentActivityState | None = None,
    pane_target: str | None = "test-fix-auth:agent.0",
    **act: object,
) -> WorkspaceActivity:
    """A WorkspaceActivity with an optional single agent session.

    ``agent_state=None`` → an untracked workspace (no sessions, ``primary`` None,
    always compact). Otherwise one Grove-launched session carrying an
    ``AgentActivity`` in ``agent_state`` plus whatever fields ``act`` overrides.
    """
    state = state or _state()
    sessions: tuple[SessionActivity, ...] = ()
    if agent_state is not None:
        activity = AgentActivity(state=agent_state, **act)  # type: ignore[arg-type]
        session = AgentSession(
            session_id="s1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        )
        sessions = (SessionActivity(session=session, activity=activity),)
    return WorkspaceActivity(
        state=state,
        sessions=sessions,
        base_ahead=2,
        base_behind=0,
        diff_added=12,
        diff_removed=3,
        dirty_files=0,
        pane_target=pane_target,
        recent_commits=(),
        observed_at=_NOW,
    )


def _lines(activity: WorkspaceActivity, **kw: object) -> list[str]:
    return _render_card_body(activity, dark=True, now=_NOW, **kw).plain.split("\n")  # type: ignore[arg-type]


# ─── compact (idle) tiles ────────────────────────────────────────────────────


def test_compact_tile_is_exactly_three_rows() -> None:
    # Idle → compact: title / identity / stats, an exact fit for a one-track
    # cell. No wasted rows (the redesign's whole point) and no pane placeholder.
    lines = _lines(_activity(agent_state=AgentActivityState.IDLE))
    assert len(lines) == 3
    assert "fix-auth" in lines[0]
    assert "feat/auth" in lines[1] and "claude" in lines[1] and "idle" in lines[1]
    assert "+12" in lines[2] and "-3" in lines[2]
    assert "· · ·" not in "\n".join(lines)  # compact tiles never carry a pane


def test_untracked_workspace_is_compact() -> None:
    # No agent session at all → not promoted, three compact rows.
    assert not is_promoted(_activity(agent_state=None))
    assert len(_lines(_activity(agent_state=None))) == 3


# ─── promoted (live) tiles ───────────────────────────────────────────────────


def test_promoted_working_tile_shows_task_and_fills_with_pane() -> None:
    activity = _activity(
        agent_state=AgentActivityState.WORKING,
        title="Refactoring the auth module",
        human_turns=8,
        assistant_replies=14,
        tool_calls=31,
        model="sonnet",
        tokens_in=12000,
        tokens_out=3000,
    )
    plain = _render_card_body(activity, dark=True, now=_NOW).plain
    assert "working" in plain  # promoted state label on row 1
    assert "Refactoring the auth module" in plain  # the agent's own summary
    assert "sonnet" in plain and "8t 14r 31⚒" in plain  # model + counts
    assert "12.0k↑" in plain  # token usage only on promoted tiles
    # Not yet captured → a quiet placeholder keeps the tile a framed container.
    assert "· · ·" in plain


def test_promoted_tile_renders_fit_to_cell_pane_tail() -> None:
    snap = "\n".join(f"line{i}" for i in range(1, 7))  # 6 lines of pane output
    activity = _activity(agent_state=AgentActivityState.WORKING, title="t")
    plain = _render_card_body(activity, dark=True, now=_NOW, pane_snapshot=snap).plain
    # The tail is fit to the promoted cell (last rows win); earlier lines crop.
    assert "line6" in plain and "line5" in plain
    assert "line1" not in plain


def test_interpreted_status_wins_over_raw_task() -> None:
    # The reserved #20 seam: an LLM interpreter's one-liner is preferred as the
    # summary over the raw ai-title / current task.
    activity = _activity(
        agent_state=AgentActivityState.WAITING,
        title="raw ai title",
        current_task="raw current task",
        interpreted_status="Waiting for you to approve the migration",
    )
    plain = _render_card_body(activity, dark=True, now=_NOW).plain
    assert "Waiting for you to approve the migration" in plain
    assert "raw ai title" not in plain


# ─── placement metadata ──────────────────────────────────────────────────────


def test_root_placement_carries_a_tag() -> None:
    plain = _render_card_body(
        _activity(state=_state(placement=Placement.ROOT), agent_state=AgentActivityState.IDLE),
        dark=True,
        now=_NOW,
    ).plain
    assert "root" in plain


def test_worktree_placement_has_no_tag() -> None:
    plain = _render_card_body(
        _activity(state=_state(placement=Placement.WORKTREE), agent_state=AgentActivityState.IDLE),
        dark=True,
        now=_NOW,
    ).plain
    assert "root" not in plain


# ─── the promotion predicate ─────────────────────────────────────────────────


def test_is_promoted_tracks_live_states() -> None:
    assert is_promoted(_activity(agent_state=AgentActivityState.WORKING))
    assert is_promoted(_activity(agent_state=AgentActivityState.WAITING))
    assert is_promoted(_activity(agent_state=AgentActivityState.BLOCKED))
    assert is_promoted(_activity(agent_state=AgentActivityState.ERROR))
    assert not is_promoted(_activity(agent_state=AgentActivityState.IDLE))
    assert not is_promoted(_activity(agent_state=AgentActivityState.STARTING))
    assert not is_promoted(_activity(agent_state=None))
