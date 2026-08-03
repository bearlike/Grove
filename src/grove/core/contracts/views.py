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
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.tickets import TicketRef
from grove.core.tmux import AttachInstruction, ContainerAttach, HostAttach
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    InitStatus,
    Placement,
    ProvisionProgress,
    ProvisionStatus,
    Runtime,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)

if TYPE_CHECKING:
    # Imported for annotation only. ``Project`` lives in ``grove.core.registry``,
    # which pulls in the manager and the store; a runtime import here would drag
    # both into every contracts import. ``from_project`` duck-types instead.
    from grove.core.registry import Project


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
    ticket_refs: list[TicketRef] = []
    # Runtime facts. Every one carries a default that means "an ordinary
    # host workspace", so a client built against the older schema decodes a
    # newer payload unchanged — the same defaulting discipline `placement` used.
    runtime: Runtime = Runtime.HOST
    runtime_fallback_reason: str | None = None
    provision_status: ProvisionStatus | None = None
    provision_duration_ms: int | None = None
    # When the in-flight provision started. Every client renders "building for
    # 2m10s" from this plus its own clock rather than from a server-computed
    # elapsed, which would be stale the instant it left the daemon — the same
    # reason `created_at` crosses raw. `provision_duration_ms` is its finished
    # twin: exactly one of the two is meaningful at a time.
    provision_started_at: datetime | None = None
    container: ContainerRuntimeState | None = None
    # Whether this workspace runs Grove's packaged default container config —
    # what every surface renders the "default container" NOTICE from. Distinct
    # from `runtime_fallback_reason`, which is a degradation: a repo with no
    # devcontainer.json still got the isolation it asked for.
    runtime_default_config: bool = False
    # Derived from `container.tmux_command` (see `WorkspaceState.runtime_no_tmux`)
    # — no field of this name is ever persisted. True iff this is a container
    # workspace whose image ships no in-container tmux, so the agent runs bare
    # and dies with the client that launched it.
    runtime_no_tmux: bool = False

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
            runtime=s.runtime,
            runtime_fallback_reason=s.runtime_fallback_reason,
            runtime_default_config=s.runtime_default_config,
            runtime_no_tmux=s.runtime_no_tmux,
            provision_status=s.provision_status,
            provision_duration_ms=s.provision_duration_ms,
            provision_started_at=s.provision_started_at,
            # Frozen model — safe to share with the record rather than copy.
            container=s.container,
            # TicketRef is frozen/immutable; the list is copied so the view can
            # never alias and mutate the engine record's refs.
            ticket_refs=list(s.ticket_refs),
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
    """One-shot ANSI snapshot of a workspace's agent tmux pane.

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


class ProvisionProgressView(BaseModel):
    """Wire mirror of ``grove.core.workspace.ProvisionProgress``.

    The fetch-on-demand half of the provisioning axis. ``WorkspaceStateView``
    already streams *whether* a workspace is provisioning and since when; this
    answers *is it still moving*, which only the provisioner's own log can say.
    It is a separate route rather than a field on the state view because
    reading it costs a file read per workspace, and the activity poll walks
    every workspace on the host ~every 2 s.

    ``provision_log_path`` is deliberately absent: it is a host path, the
    ``init_log_path`` precedent. A client that wants the log reads ``lines``.
    """

    model_config = ConfigDict(frozen=True)

    elapsed_ms: int | None
    headline: str
    lines: list[str]

    @classmethod
    def from_progress(cls, p: ProvisionProgress) -> ProvisionProgressView:
        return cls(elapsed_ms=p.elapsed_ms, headline=p.headline, lines=list(p.lines))


class HostAttachView(BaseModel):
    """Wire mirror of ``grove.core.tmux.HostAttach``."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["host"] = "host"
    tmux_session: str
    inside_outer_tmux: bool

    def attach_argv(self) -> list[str]:
        """The argv a FRESH pty runs to attach (the daemon's xterm.js bridge).

        Always a plain ``attach``: ``inside_outer_tmux`` describes the DAEMON
        host's own environment, which says nothing about a pty this process is
        about to fork. The variant still carries the flag because a wire client
        that hands the user a command to paste needs to warn them (MCP does).
        """
        return ["tmux", "attach", "-t", self.tmux_session]

    @classmethod
    def from_instruction(cls, a: HostAttach) -> HostAttachView:
        return cls(tmux_session=a.tmux_session, inside_outer_tmux=a.inside_outer_tmux)


class ContainerAttachView(BaseModel):
    """Wire mirror of ``grove.core.tmux.ContainerAttach``."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["container"] = "container"
    argv: tuple[str, ...]

    def attach_argv(self) -> list[str]:
        """The argv a fresh pty runs — already complete; see :class:`ContainerAttach`."""
        return list(self.argv)

    @classmethod
    def from_instruction(cls, a: ContainerAttach) -> ContainerAttachView:
        return cls(argv=a.argv)


#: Wire-level discriminated union mirroring ``grove.core.tmux.AttachInstruction``.
#: Clients dispatch on ``kind``; both variants answer ``attach_argv()``, so a
#: client that only wants a terminal never has to branch at all.
type AttachInstructionView = Annotated[
    HostAttachView | ContainerAttachView, Field(discriminator="kind")
]

#: Decodes a wire payload into the union. A ``type`` alias has no
#: ``model_validate``, and a client still has to get from JSON to a variant.
ATTACH_INSTRUCTION_ADAPTER: TypeAdapter[AttachInstructionView] = TypeAdapter(AttachInstructionView)


def attach_instruction_view(instruction: AttachInstruction) -> AttachInstructionView:
    """The wire mirror of *instruction*.

    A module-level function rather than a ``from_instruction`` classmethod
    because the dispatch is between two closed unions: neither variant owns the
    choice, and a union alias has no class body to hang it on. The per-variant
    field mapping still lives on the variants, where it can't drift.
    """
    if isinstance(instruction, HostAttach):
        return HostAttachView.from_instruction(instruction)
    return ContainerAttachView.from_instruction(instruction)


class ProjectView(BaseModel):
    """Wire mirror of ``grove.core.registry.Project`` — one listable project.

    The answer to "which repos can I work in", which a remote client cannot
    derive on its own: the engine unions store-derived roots with the
    config-declared ``projects`` list, so a freshly added or fully drained
    repo still appears.

    ``repo_root`` is the value every repo-scoped call wants (``/agents``,
    ``/branches``, ``create``). ``cwd`` differs from it only for a nested
    project, where several projects share one repo root and differ only in
    where the agent session starts. Field names match ``ProjectGroupView``
    so the two read as the same concept on the wire.
    """

    model_config = ConfigDict(frozen=True)

    repo_root: str
    repo_name: str
    cwd: str

    @classmethod
    def from_project(cls, p: Project) -> ProjectView:
        return cls(
            repo_root=str(p.repo_root),
            # Display name only. Derived here rather than stored so it cannot
            # drift from the path, matching how ProjectGroupView names a repo.
            repo_name=p.repo_root.name,
            cwd=str(p.cwd),
        )


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

    ``latest_version`` / ``update_available`` carry the daemon-side release-skew
    check: the latest GitHub release tag (bare, e.g. ``0.2.0``) and whether
    it exceeds the installed ``version``. ``latest_version`` is ``None`` and
    ``update_available`` ``False`` until a successful check (offline / first
    call / error). Exposing it here lets the webapp render a "newer release"
    footer hint WITHOUT polling GitHub from the browser — one daemon-side check,
    cached for hours (see :mod:`grove.core.release`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    started_at: datetime
    uptime_seconds: int
    host: str
    user: str
    platform: str
    python_version: str
    latest_version: str | None = None
    update_available: bool = False


__all__ = [
    "ATTACH_INSTRUCTION_ADAPTER",
    "AttachInstructionView",
    "CommitSummaryView",
    "ContainerAttachView",
    "HealthView",
    "HostAttachView",
    "ProjectView",
    "ProvisionProgressView",
    "WhoamiView",
    "WorkspacePaneView",
    "WorkspacePeekView",
    "WorkspaceStateView",
    "attach_instruction_view",
]
