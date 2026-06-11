"""Pydantic mirrors of engine dataclasses — the wire shape.

These types are the daemon's response models. The engine continues to
use the underlying dataclasses (``WorkspaceState`` etc.) for in-process
state; ``Views`` exist purely so anything crossing a client/server
boundary is Pydantic-validated and JSON-Schema-documented, per the
``CLAUDE.md`` boundary rule.

Each ``View`` is field-for-field with its source dataclass, minus
internal-only fields explicitly excluded (e.g. ``init_log_path``,
``init_env``). Add ``from_*`` classmethods only — no inverse direction;
the engine never accepts a View.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from grove.core.tmux import AttachInstruction
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    InitStatus,
    Placement,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)


class WorkspaceStateView(BaseModel):
    """Wire mirror of ``grove.core.workspace.WorkspaceState``."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    repo_root: str
    branch: str
    base_branch: str
    worktree_path: str
    tmux_session: str
    agent_name: str
    status: WorkspaceStatus
    created_at: datetime
    updated_at: datetime
    paused_at: datetime | None = None
    error_detail: str | None = None
    description: str | None = None
    init_status: InitStatus | None = None
    init_duration_ms: int | None = None
    branch_provenance: BranchProvenance = BranchProvenance.GROVE_CREATED
    placement: Placement = Placement.WORKTREE

    @classmethod
    def from_state(cls, s: WorkspaceState) -> WorkspaceStateView:
        return cls(
            id=s.id,
            title=s.title,
            repo_root=s.repo_root,
            branch=s.branch,
            base_branch=s.base_branch,
            worktree_path=s.worktree_path,
            tmux_session=s.tmux_session,
            agent_name=s.agent_name,
            status=s.status,
            created_at=s.created_at,
            updated_at=s.updated_at,
            paused_at=s.paused_at,
            error_detail=s.error_detail,
            description=s.description,
            init_status=s.init_status,
            init_duration_ms=s.init_duration_ms,
            branch_provenance=s.branch_provenance,
            placement=s.placement,
        )


class CommitSummaryView(BaseModel):
    """Wire mirror of ``grove.core.workspace.CommitSummary``."""

    model_config = ConfigDict(frozen=True)

    sha: str
    subject: str
    committed_at: datetime

    @classmethod
    def from_summary(cls, c: CommitSummary) -> CommitSummaryView:
        return cls(sha=c.sha, subject=c.subject, committed_at=c.committed_at)


class WorkspacePeekView(BaseModel):
    """Wire mirror of ``grove.core.workspace.WorkspacePeek``."""

    model_config = ConfigDict(frozen=True)

    state: WorkspaceStateView
    base_ahead: int
    base_behind: int
    diff_added: int
    diff_removed: int
    dirty_files: int
    recent_commits: list[CommitSummaryView]
    agent_snapshot: str | None
    snapshot_taken_at: datetime | None

    @classmethod
    def from_peek(cls, p: WorkspacePeek) -> WorkspacePeekView:
        return cls(
            state=WorkspaceStateView.from_state(p.state),
            base_ahead=p.base_ahead,
            base_behind=p.base_behind,
            diff_added=p.diff_added,
            diff_removed=p.diff_removed,
            dirty_files=p.dirty_files,
            recent_commits=[CommitSummaryView.from_summary(c) for c in p.recent_commits],
            agent_snapshot=p.agent_snapshot,
            snapshot_taken_at=p.snapshot_taken_at,
        )


class WorkspacePaneView(BaseModel):
    """One-shot ANSI snapshot of a workspace's agent tmux pane (#19).

    The focused-pane source for the dashboard's "one live focus": a client polls
    this for the single expanded card (status-gated to WORKING) rather than
    mounting N live terminals. ``ansi`` is ``tmux capture-pane -e`` output (SGR
    only — safe to render as colored text or to strip); ``None`` when the session
    isn't live or has no pane. Best-effort like peek — the route never raises.
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: str
    ansi: str | None
    taken_at: datetime | None

    @classmethod
    def from_capture(
        cls, workspace_id: str, snapshot: str | None, taken_at: datetime | None
    ) -> WorkspacePaneView:
        return cls(workspace_id=workspace_id, ansi=snapshot, taken_at=taken_at)


class AttachInstructionView(BaseModel):
    """Wire mirror of ``grove.core.tmux.AttachInstruction``."""

    model_config = ConfigDict(frozen=True)

    tmux_session: str
    inside_outer_tmux: bool

    @classmethod
    def from_instruction(cls, a: AttachInstruction) -> AttachInstructionView:
        return cls(tmux_session=a.tmux_session, inside_outer_tmux=a.inside_outer_tmux)


class HealthView(BaseModel):
    """Public liveness probe — unauthenticated, no host identity.

    Returned by ``GET /healthz``. Two fields only:

    - ``status``: literal ``"ok"`` while the process answers requests.
      Reserved as a literal so future degraded states (e.g. ``"draining"``)
      can be added without breaking the discriminator on clients.
    - ``version``: ``grove.__version__``. Public-safe — already advertised
      in ``/openapi.json``'s ``info.version``.

    Deliberately omits hostname, username, started_at, uptime — those
    identify *who* runs the daemon and live behind auth in
    :class:`WhoamiView`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"] = "ok"
    version: str


class WhoamiView(BaseModel):
    """Authenticated daemon identity + uptime.

    Returned by ``GET /whoami``. Distinct from
    ``GET /auth/sessions/me`` (which describes the *calling session*) —
    this one answers "who is the daemon, on what host, since when."

    All fields are populated server-side from stdlib (``socket``,
    ``getpass``, ``platform``) and the lifespan-captured ``started_at``;
    the view itself is pure data with no engine coupling.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    started_at: datetime
    uptime_seconds: int
    host: str
    user: str
    platform: str
    python_version: str


__all__ = [
    "AttachInstructionView",
    "CommitSummaryView",
    "HealthView",
    "WhoamiView",
    "WorkspacePaneView",
    "WorkspacePeekView",
    "WorkspaceStateView",
]
