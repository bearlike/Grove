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

from pydantic import BaseModel, ConfigDict, Field

from grove.core.agents import AgentActivityState
from grove.core.contracts.phase import PhaseView
from grove.core.contracts.questions import AgentQuestionView
from grove.core.contracts.usage import DurationView, GenerationLatencyView, TokenClassesView
from grove.core.contracts.views import CommitSummaryView, WorkspacePaneView, WorkspaceStateView

if TYPE_CHECKING:
    from grove.core.activity import (
        DashboardDelta,
        DashboardSnapshot,
        FleetSummary,
        LiveCounters,
        ProjectGroup,
        QueueDepth,
        SessionActivity,
        TodoProgress,
        WorkspaceActivity,
    )
    from grove.core.agents import AgentActivity, AgentSession, ContextWindow, NativeFacts
    from grove.core.agents.hook import SubagentHookRecord


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
    # The parent/child link: ``None`` for a normal top-level session; for
    # an itemized sub-agent fleet member, the PRIMARY session's own ``session_id``
    # — lets a client group a workspace's flat ``sessions`` list back into a tree
    # without a second lookup. Defaults so a pre-existing client deserializes
    # unchanged (additive wire evolution, same convention as ``active_subagents``).
    parent_session_id: str | None = None

    @classmethod
    def from_session(cls, s: AgentSession) -> AgentSessionView:
        return cls(
            session_id=s.session_id,
            adapter_kind=s.adapter_kind,
            provenance=s.provenance,
            tmux_window=s.tmux_window,
            parent_session_id=s.parent_session_id,
        )


class NativeFactsView(BaseModel):
    """Wire mirror of ``grove.core.agents.NativeFacts``.

    Present only on a native session (``None`` on ``AgentActivityView.native``
    for every terminal one), and each field ``None`` until the owned stream has
    stated it — a cost of ``0`` and "no cost reported yet" are different facts.
    """

    model_config = ConfigDict(frozen=True)

    cost_usd: float | None = None
    ttft_ms: int | None = None
    turn_duration_ms: int | None = None
    last_exit_code: int | None = None
    permission_denials: int = 0
    subagents_spawned: int | None = None
    """The harness's own sub-agent census for this SESSION, from its terminal
    ``result`` frame. Distinct from ``AgentActivityView.active_subagents``,
    which is how many are running right now: a session that spawned ten and
    finished them all reads ``10`` here and ``0`` there, and both are correct."""
    subagents_completed: int | None = None
    subagents_failed: int | None = None

    @classmethod
    def from_facts(cls, f: NativeFacts) -> NativeFactsView:
        return cls(
            cost_usd=f.cost_usd,
            ttft_ms=f.ttft_ms,
            turn_duration_ms=f.turn_duration_ms,
            last_exit_code=f.last_exit_code,
            permission_denials=f.permission_denials,
            subagents_spawned=f.subagents_spawned,
            subagents_completed=f.subagents_completed,
            subagents_failed=f.subagents_failed,
        )


class ContextWindowView(BaseModel):
    """Wire mirror of ``grove.core.agents.ContextWindow``.

    A block, absent as a whole when the harness reported nothing (``None`` on
    ``AgentActivityView.context``): a client draws a meter or nothing, never a
    meter at zero for a session that simply has not said. ``used_fraction`` is
    carried rather than left to the client so every surface rounds the same
    way; ``size``/``used`` stay beside it for the tooltip.
    """

    model_config = ConfigDict(frozen=True)

    size: int
    used: int
    used_fraction: float

    @classmethod
    def from_context(cls, c: ContextWindow) -> ContextWindowView:
        return cls(size=c.size, used=c.used, used_fraction=c.used_fraction)


class LiveCountersView(BaseModel):
    """Wire mirror of ``grove.core.activity.LiveCounters``.

    A *block*, not loose fields: either a live tier is actively reporting (all
    three populated) or the whole block is absent on
    ``AgentActivityView.live`` — never a partial/inconsistent live read. This
    is a faster tier than ``AgentActivityView.tokens_in``/``tokens_out`` (the
    transcript-derived, per-turn-settled cumulative totals): a client renders
    ``live`` WHILE generating and falls back to the cumulative fields the
    instant ``live`` goes absent again (a turn flushed, or no fast side-channel
    is wired yet).
    """

    model_config = ConfigDict(frozen=True)

    tokens_in: int
    tokens_out: int
    generating_since: datetime

    @classmethod
    def from_live(cls, live: LiveCounters) -> LiveCountersView:
        return cls(
            tokens_in=live.tokens_in,
            tokens_out=live.tokens_out,
            generating_since=live.generating_since,
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
    # Sub-agents/background tasks spawned but not yet returned — defaults so a
    # pre-existing client deserializes unchanged (additive wire evolution).
    active_subagents: int = 0
    model: str | None
    tokens_in: int
    tokens_out: int
    # Context-window pressure. Defaults so a pre-existing client deserializes
    # unchanged; ``None`` is "this harness did not say", never a zero.
    context: ContextWindowView | None = None
    # A stale native worker wrote cumulative context bytes which Grove suppresses
    # rather than rendering as occupancy. `None` is still "not measured"; this
    # value tells the client that respawning starts the current-format producer.
    # Nullable with no schema default so older clients continue to decode it.
    context_unavailable_reason: Literal["stale_native_worker"] | None = None
    # Owned-stream facts (cost, TTFT, last exit code); ``None`` on a terminal
    # session and on an older daemon, so a client renders nothing rather than 0.
    native: NativeFactsView | None = None
    last_event_at: datetime | None
    # When the session was born (its first record), beside ``last_event_at``.
    # A subagent row states both, so a reader can tell a long run from a late
    # one. Defaults so a pre-existing client deserializes unchanged; ``None`` is
    # "this adapter did not measure it", never the epoch.
    started_at: datetime | None = None
    needs_attention: bool
    error_detail: str | None
    # Reserved for the future external-LLM interpreter; always None today.
    interpreted_status: str | None = None
    # The questions the agent is asking RIGHT NOW, captured live from the
    # hook sidecar and cross-checked against the transcript before they ship. One
    # AskUserQuestion call carries up to four questions answered atomically with
    # one POST, so the whole group rides together (ordered as asked); empty ⇒
    # nothing pending. Defaults to [] so a pre-existing client deserializes
    # unchanged (additive wire evolution). It rides the live activity stream so a
    # client renders an answer affordance the instant the questions appear.
    questions: list[AgentQuestionView] = []
    # Live in-flight token counters — populated only while a fast
    # side-channel is actively reporting (a proxy is the primary source;
    # partial-message deltas / OTel metrics are fallbacks). ``None`` means no
    # live tier is wired yet, or the session isn't currently generating: the
    # client hides the indicator rather than showing zeros, settling to the
    # cumulative ``tokens_in``/``tokens_out`` above. Defaults to None so a
    # pre-existing client deserializes unchanged (additive wire evolution).
    live: LiveCountersView | None = None

    @classmethod
    def from_activity(
        cls, a: AgentActivity, *, live: LiveCountersView | None = None
    ) -> AgentActivityView:
        return cls(
            state=a.state,
            title=a.title,
            current_task=a.current_task,
            human_turns=a.human_turns,
            assistant_replies=a.assistant_replies,
            replies_per_turn=list(a.replies_per_turn),
            tool_calls=a.tool_calls,
            active_subagents=a.active_subagents,
            model=a.model,
            tokens_in=a.tokens_in,
            tokens_out=a.tokens_out,
            context=ContextWindowView.from_context(a.context) if a.context is not None else None,
            context_unavailable_reason=a.context_unavailable_reason,
            native=NativeFactsView.from_facts(a.native) if a.native is not None else None,
            last_event_at=a.last_event_at,
            started_at=a.started_at,
            needs_attention=a.needs_attention,
            error_detail=a.error_detail,
            interpreted_status=a.interpreted_status,
            questions=[AgentQuestionView.from_question(q) for q in a.questions],
            live=live,
        )


class SessionActivityView(BaseModel):
    """Wire mirror of ``grove.core.activity.SessionActivity``."""

    model_config = ConfigDict(frozen=True)

    session: AgentSessionView
    activity: AgentActivityView
    # Defaults to ``None`` so a pre-existing client deserializes unchanged
    # (additive wire evolution). Four small integers plus a confidence
    # string — bounded like every other field on this payload, which is why
    # it is safe to ride the ~1 Hz stream rather than needing a fetch-on-demand
    # route the way an unbounded turn list or tool body does. ``None`` means
    # "not measured", never "did no work" — see ``DurationView``.
    duration: DurationView | None = None
    # Unfolds ``activity.tokens_in`` into the classes that sum to it (fresh
    # input, cache read, cache creation — folded together BY DESIGN, see
    # ``ClaudeCodeAdapter.usage_tokens``), plus reasoning/output/provider_total
    # where a provider reports them. Same shape and same reason as ``duration``
    # above: six nullable integers, bounded, safe to ride the stream; ``None``
    # on the whole field for the same population ``duration`` is null for (a
    # fleet-entry row, or a read this tick could not price), and a ``None``
    # PER CLASS means that class specifically was not measured — never a
    # fabricated zero. Defaults to ``None`` so a pre-existing client
    # deserializes unchanged (additive wire evolution).
    tokens: TokenClassesView | None = None
    # The model's own average response wait for this session — generation
    # intervals only, never folded with tool time. Two small integers,
    # bounded like every other field here; same nullability rule and same
    # population as ``duration``. Defaults to ``None`` for the same additive-
    # evolution reason as the two fields above.
    latency: GenerationLatencyView | None = None

    @classmethod
    def from_session_activity(cls, sa: SessionActivity) -> SessionActivityView:
        return cls(
            session=AgentSessionView.from_session(sa.session),
            activity=AgentActivityView.from_activity(
                sa.activity,
                live=LiveCountersView.from_live(sa.live) if sa.live is not None else None,
            ),
            duration=sa.duration,
            tokens=sa.tokens,
            latency=sa.latency,
        )


class TodoProgressView(BaseModel):
    """Wire mirror of ``grove.core.activity.TodoProgress`` — counts, never items.

    The deliberate counterpart to the full ``TodoListView``, which stays
    fetch-on-demand behind ``GET /workspaces/{id}/todo``. This module's rule is
    that the SSE stream carries only bounded payloads (the same reason session
    turns never ride it), and a checklist is unbounded in both length and text;
    four integers render "4/10 done" on every card for a fixed cost.

    Absent (``None`` on the parent view) means the agent has called no todo tool
    — a client hides the indicator rather than rendering 0/0, exactly as it does
    for ``live`` and ``phase``.
    """

    model_config = ConfigDict(frozen=True)

    total: int
    completed: int
    in_progress: int
    pending: int

    @classmethod
    def from_progress(cls, p: TodoProgress) -> TodoProgressView:
        return cls(
            total=p.total,
            completed=p.completed,
            in_progress=p.in_progress,
            pending=p.pending,
        )


class QueueDepthView(BaseModel):
    """Wire mirror of ``grove.core.activity.QueueDepth`` — a count, never the
    messages.

    ``TodoProgressView``'s sibling and its argument applies verbatim: the full
    list stays fetch-on-demand behind ``GET /workspaces/{id}/queue`` because a
    queue is unbounded in length and text, where this rides the ~1 Hz delta for
    every workspace on the host. One integer renders "2 waiting" for a fixed
    cost.

    Absent (``None`` on the parent view) means nothing is waiting — which
    deliberately reads the same for a harness whose queue Grove cannot see,
    because a card hides the indicator either way. The distinction a client acts
    on ("nothing waiting" against "no idea") lives on the route, as
    ``WorkspaceQueueView.supported``.
    """

    model_config = ConfigDict(frozen=True)

    pending: int

    @classmethod
    def from_depth(cls, d: QueueDepth) -> QueueDepthView:
        return cls(pending=d.pending)


class SubagentActivityView(BaseModel):
    """Wire mirror of ``grove.core.agents.hook.SubagentHookRecord`` — one LIVE
    sub-agent, hook-pushed rather than transcript-derived.

    The full-detail counterpart to ``FleetProgressView``'s counts, served only
    behind ``GET /workspaces/{id}/fleet`` (see that view's docstring for why
    it never rides the SSE stream). ``current_tool`` is the name of a
    ``PreToolUse`` this sub-agent has not yet resolved with its own
    ``PostToolUse`` — the same "unresolved means in-flight" fact
    ``ToolCallView`` carries for a finished transcript's tool calls, sourced
    here from the live push instead. ``last_message`` is populated only once
    ``state`` has settled to ``waiting`` (an explicit ``SubagentStop``); it is
    never a placeholder while the sub-agent runs.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    agent_type: str | None
    state: AgentActivityState
    started_at: datetime
    last_event_at: datetime
    current_tool: str | None
    last_message: str | None

    @classmethod
    def from_record(cls, r: SubagentHookRecord) -> SubagentActivityView:
        return cls(
            agent_id=r.agent_id,
            agent_type=r.agent_type,
            state=r.state,
            started_at=r.started_at,
            last_event_at=r.last_event_at,
            current_tool=r.current_tool,
            last_message=r.last_message,
        )


class SubagentFleetView(BaseModel):
    """The full child-agent roster for one workspace root session.

    ``subagents`` preserves Claude Code's hook-pushed live roster. ``sessions``
    is the provider-neutral, transcript-derived child projection; a matching
    child id appears in both sources only once in ``sessions``. The route and
    stream never embed child turns: a selected child remains drillable through
    the existing cursor-aware ``/turns`` endpoint.

    ``supported`` distinguishes an adapter with no child-reader capability from
    a supported root with no children. ``error`` is a best-effort read failure,
    never silently reported as an empty fleet.
    """

    model_config = ConfigDict(frozen=True)

    subagents: list[SubagentActivityView] = []
    sessions: list[SessionActivityView] = []
    session_id: str | None = None
    supported: bool = False
    error: str | None = None


class FleetProgressView(BaseModel):
    """Wire mirror of ``grove.core.activity.FleetSummary`` — counts, never the roster.

    ``TodoProgressView``'s sibling: rides the ~1 Hz ``session_activity`` delta
    as two integers ("2 running of 5"), where the full per-agent detail (name,
    current tool, elapsed time) is unbounded in count and stays behind
    ``GET /workspaces/{id}/fleet``.

    Absent (``None`` on the parent view) means this session has pushed no
    sub-agent status at all — a client hides the indicator rather than
    rendering 0/0, exactly as it does for ``todo`` and ``queue``.
    """

    model_config = ConfigDict(frozen=True)

    active: int
    total: int

    @classmethod
    def from_summary(cls, s: FleetSummary) -> FleetProgressView:
        return cls(active=s.active, total=s.total)


class WorkspaceActivityView(BaseModel):
    """Wire mirror of ``grove.core.activity.WorkspaceActivity`` — one dashboard card.

    ``recent_commits`` is the durable latest-activity signal (newest first;
    ``recent_commits[0]`` is the card's "what was done, when committed" line).
    ``observed_at`` is the per-card "updated Xs ago"; the dashboard-wide refresh
    time stays on ``DashboardSnapshotView.generated_at``.

    ``phase``, ``todo``, ``queue`` and ``fleet`` are the task axis: what the
    agent says it is doing about the task, how far through its own checklist
    it is, how much the harness is still holding for it, and how many
    sub-agents it has running. All default to ``None`` so a pre-existing
    client deserializes unchanged (additive wire evolution), and all mean
    "nothing to report" when absent — never a zero value.

    ``branch`` is the LIVE branch, already derived per tick by the engine and
    dropped here until now — so every client rendered ``state.branch``, the
    create-time snapshot nothing refreshes, and named the branch a workspace
    was born on rather than the one it is on. It carries
    ``WorkspaceActivity.live_branch`` (live, else recorded), so it is never
    empty and a client needs no fallback of its own. ``state.branch`` stays
    exactly as it was: that field is the IDENTITY ``resume`` rebuilds the
    worktree from, and this one is what a reader should see.
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
    phase: PhaseView | None = None
    todo: TodoProgressView | None = None
    queue: QueueDepthView | None = None
    fleet: FleetProgressView | None = None
    # `default_factory`, never `default=""`: Pydantic writes a `default` key
    # into the schema for the latter, and `openapi-typescript`'s
    # `defaultNonNullable` then generates the property as REQUIRED — so an
    # additive field would break every existing client literal. Same trap
    # `CreateWorkspaceRequest.attachments` paid for; see contracts/CLAUDE.md.
    branch: str = Field(default_factory=str)

    @classmethod
    def from_activity(cls, w: WorkspaceActivity) -> WorkspaceActivityView:
        return cls(
            state=WorkspaceStateView.from_state(w.state),
            branch=w.live_branch,
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
            phase=PhaseView.from_report(w.phase) if w.phase is not None else None,
            todo=TodoProgressView.from_progress(w.todo) if w.todo is not None else None,
            queue=QueueDepthView.from_depth(w.queue) if w.queue is not None else None,
            fleet=FleetProgressView.from_summary(w.fleet) if w.fleet is not None else None,
        )


class ProjectGroupView(BaseModel):
    """Wire mirror of ``grove.core.activity.ProjectGroup``."""

    model_config = ConfigDict(frozen=True)

    repo_root: str
    repo_name: str
    cwd: str
    workspaces: list[WorkspaceActivityView]
    error: str | None = None
    """Why this project could not be read, or ``None`` when it was.

    Non-null means the group is DEGRADED: its config would not resolve, so no
    workspace could be listed and ``workspaces`` is empty. Surfaced rather than
    dropped, because a repo whose config is broken is the one an operator most
    needs named — a silently missing project reads as a healthy fleet.
    """

    @classmethod
    def from_group(cls, g: ProjectGroup) -> ProjectGroupView:
        return cls(
            repo_root=g.repo_root,
            repo_name=g.repo_name,
            cwd=g.cwd,
            error=g.error,
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
    """The SSE streaming envelope.

    One shape carries every server-sent kind. ``snapshot`` (sent on connect)
    embeds the full ``DashboardSnapshotView``; ``session_activity`` embeds the one
    changed ``WorkspaceActivityView`` so the client patches a single card;
    ``workspace_changed`` is a lifecycle wake-up (re-fetch); ``heartbeat`` keeps
    the connection warm; ``pane_snapshot`` embeds one ``WorkspacePaneView`` (the
    live focused-pane push) and rides a *dedicated* per-workspace stream, not
    the cross-project ``/events`` fan-out — its ~1 Hz cadence and per-id scope are
    a different concern from the activity deltas. ``seq`` is the monotonic SSE id
    used for ``Last-Event-ID`` replay.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "snapshot",
        "workspace_changed",
        "session_activity",
        "pane_snapshot",
        "heartbeat",
        "catalog_changed",
        "workspace_source_changed",
    ]
    seq: int
    workspace_id: str | None = None
    repo_root: str | None = None
    detail: dict[str, str] = {}
    workspace: WorkspaceActivityView | None = None
    snapshot: DashboardSnapshotView | None = None
    # Carried only by ``pane_snapshot`` frames; ``None`` on every other kind.
    pane: WorkspacePaneView | None = None

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

    @classmethod
    def pane_event(cls, pane: WorkspacePaneView, *, seq: int) -> DashboardEvent:
        """One live focused-pane push — the streaming twin of ``GET .../pane``.

        Reuses the one-shot endpoint's ``WorkspacePaneView`` so the snapshot's
        ``ansi``/``taken_at`` shape is identical whether a client polls once or
        consumes the stream.
        """
        return cls(kind="pane_snapshot", seq=seq, workspace_id=pane.workspace_id, pane=pane)
