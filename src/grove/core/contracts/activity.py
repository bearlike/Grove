"""Wire shapes for the Activity Dashboard — Pydantic mirrors of the engine IR.

The daemon serializes ``ActivityService`` output through these; the webapp
regenerates its TypeScript types from them via the OpenAPI schema. Same
``from_*`` + ``frozen=True`` pattern as ``contracts/views.py``, and the single
coupling point between the engine's activity dataclasses and any client.

Runtime imports are kept to leaf types only (the ``AgentActivityState`` enum a
field needs, plus the existing ``WorkspaceStateView``). The engine activity
dataclasses are imported under ``TYPE_CHECKING`` and referenced only as string
annotations on the ``from_*`` parameters — the methods duck-type attribute
access — so this module never pulls the manager/registry into a contracts import.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from grove.core.agents import AgentActivityState
from grove.core.contracts.views import CommitSummaryView, WorkspaceStateView

if TYPE_CHECKING:
    from grove.core.activity import (
        DashboardDelta,
        DashboardSnapshot,
        ProjectGroup,
        SessionActivity,
        WorkspaceActivity,
    )
    from grove.core.agents import AgentActivity, AgentSession


class AgentSessionView(BaseModel):
    """Wire mirror of ``grove.core.agents.AgentSession``.

    ``transcript_path`` is deliberately absent: it is a host-private path
    (views never expose those), and no client ever consumed it — sessions are
    identified by id on the wire.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    adapter_kind: str
    provenance: str
    tmux_window: str | None

    @classmethod
    def from_session(cls, s: AgentSession) -> AgentSessionView:
        return cls(
            session_id=s.session_id,
            adapter_kind=s.adapter_kind,
            provenance=s.provenance,
            tmux_window=s.tmux_window,
        )


class AgentActivityView(BaseModel):
    """Wire mirror of ``grove.core.agents.AgentActivity`` (``needs_attention`` materialized)."""

    model_config = ConfigDict(frozen=True)

    state: AgentActivityState
    title: str | None
    current_task: str | None
    human_turns: int
    assistant_replies: int
    replies_per_turn: list[int]
    tool_calls: int
    model: str | None
    tokens_in: int
    tokens_out: int
    last_event_at: datetime | None
    needs_attention: bool
    error_detail: str | None
    # Reserved for the future external-LLM interpreter (#20); always None today.
    interpreted_status: str | None = None

    @classmethod
    def from_activity(cls, a: AgentActivity) -> AgentActivityView:
        return cls(
            state=a.state,
            title=a.title,
            current_task=a.current_task,
            human_turns=a.human_turns,
            assistant_replies=a.assistant_replies,
            replies_per_turn=list(a.replies_per_turn),
            tool_calls=a.tool_calls,
            model=a.model,
            tokens_in=a.tokens_in,
            tokens_out=a.tokens_out,
            last_event_at=a.last_event_at,
            needs_attention=a.needs_attention,
            error_detail=a.error_detail,
            interpreted_status=a.interpreted_status,
        )


class SessionActivityView(BaseModel):
    """Wire mirror of ``grove.core.activity.SessionActivity``."""

    model_config = ConfigDict(frozen=True)

    session: AgentSessionView
    activity: AgentActivityView

    @classmethod
    def from_session_activity(cls, sa: SessionActivity) -> SessionActivityView:
        return cls(
            session=AgentSessionView.from_session(sa.session),
            activity=AgentActivityView.from_activity(sa.activity),
        )


class WorkspaceActivityView(BaseModel):
    """Wire mirror of ``grove.core.activity.WorkspaceActivity`` — one dashboard card.

    ``recent_commits`` is the durable latest-activity signal (newest first;
    ``recent_commits[0]`` is the card's "what was done, when committed" line).
    ``observed_at`` is the per-card "updated Xs ago"; the dashboard-wide refresh
    time stays on ``DashboardSnapshotView.generated_at``.
    """

    model_config = ConfigDict(frozen=True)

    state: WorkspaceStateView
    sessions: list[SessionActivityView]
    base_ahead: int
    base_behind: int
    diff_added: int
    diff_removed: int
    dirty_files: int
    pane_target: str | None
    needs_attention: bool
    recent_commits: list[CommitSummaryView]
    observed_at: datetime

    @classmethod
    def from_activity(cls, w: WorkspaceActivity) -> WorkspaceActivityView:
        return cls(
            state=WorkspaceStateView.from_state(w.state),
            sessions=[SessionActivityView.from_session_activity(s) for s in w.sessions],
            base_ahead=w.base_ahead,
            base_behind=w.base_behind,
            diff_added=w.diff_added,
            diff_removed=w.diff_removed,
            dirty_files=w.dirty_files,
            pane_target=w.pane_target,
            needs_attention=w.needs_attention,
            recent_commits=[CommitSummaryView.from_summary(c) for c in w.recent_commits],
            observed_at=w.observed_at,
        )


class ProjectGroupView(BaseModel):
    """Wire mirror of ``grove.core.activity.ProjectGroup``."""

    model_config = ConfigDict(frozen=True)

    repo_root: str
    repo_name: str
    workspaces: list[WorkspaceActivityView]

    @classmethod
    def from_group(cls, g: ProjectGroup) -> ProjectGroupView:
        return cls(
            repo_root=g.repo_root,
            repo_name=g.repo_name,
            workspaces=[WorkspaceActivityView.from_activity(w) for w in g.workspaces],
        )


class DashboardSnapshotView(BaseModel):
    """Wire mirror of ``grove.core.activity.DashboardSnapshot`` — one full render."""

    model_config = ConfigDict(frozen=True)

    projects: list[ProjectGroupView]
    generated_at: datetime
    total_workspaces: int
    needs_attention: int

    @classmethod
    def from_snapshot(cls, s: DashboardSnapshot) -> DashboardSnapshotView:
        return cls(
            projects=[ProjectGroupView.from_group(g) for g in s.projects],
            generated_at=s.generated_at,
            total_workspaces=s.total_workspaces,
            needs_attention=s.needs_attention,
        )


class DashboardEvent(BaseModel):
    """The SSE streaming envelope (epic #11 §5).

    One shape carries every server-sent kind. ``snapshot`` (sent on connect)
    embeds the full ``DashboardSnapshotView``; ``session_activity`` embeds the one
    changed ``WorkspaceActivityView`` so the client patches a single card;
    ``workspace_changed`` is a lifecycle wake-up (re-fetch); ``heartbeat`` keeps
    the connection warm; ``pane_snapshot`` is reserved for #19. ``seq`` is the
    monotonic SSE id used for ``Last-Event-ID`` replay.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["snapshot", "workspace_changed", "session_activity", "pane_snapshot", "heartbeat"]
    seq: int
    workspace_id: str | None = None
    repo_root: str | None = None
    detail: dict[str, str] = {}
    workspace: WorkspaceActivityView | None = None
    snapshot: DashboardSnapshotView | None = None

    @classmethod
    def from_delta(cls, delta: DashboardDelta) -> DashboardEvent:
        return cls(
            kind=delta.kind,
            seq=delta.seq,
            workspace_id=delta.workspace_id,
            repo_root=delta.repo_root,
            detail=dict(delta.detail),
            workspace=(
                WorkspaceActivityView.from_activity(delta.workspace)
                if delta.workspace is not None
                else None
            ),
        )

    @classmethod
    def snapshot_event(cls, snapshot: DashboardSnapshot, *, seq: int) -> DashboardEvent:
        return cls(kind="snapshot", seq=seq, snapshot=DashboardSnapshotView.from_snapshot(snapshot))

    @classmethod
    def heartbeat(cls, *, seq: int) -> DashboardEvent:
        return cls(kind="heartbeat", seq=seq)
