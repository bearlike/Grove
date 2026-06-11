"""Workspace state record + identity + status transitions.

A Workspace is a (git worktree, tmux session, agent) triple. This module
defines the data shape and the rules for how its status may evolve. Pure;
no I/O — those live in `git.py`, `tmux.py`, and `store.py`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from grove.core.config import AgentKind, GroveConfig, expand_template
from grove.core.errors import WorkspaceStateError


class WorkspaceStatus(StrEnum):
    """Workspace status — split into persistent intents and computed views.

    Three values are persistent intents: lifecycle methods write them to JSON
    and `JsonWorkspaceStore` rejects writes of any other value (defense in
    depth). The remaining four are derived at read time by
    `WorkspaceManager._reconcile_status` from the persistent intent + tmux
    session presence + worktree presence + tmux pane activity. `list()` and
    `peek()` always promote intents to displayed values, so callers reading
    through the manager see ACTIVE/IDLE/OFFLINE/PAUSED/ORPHANED/ERROR — never
    the raw RUNNING intent.
    """

    # Persistent intents (set by lifecycle methods, written to JSON).
    RUNNING = "running"  # Grove spawned a tmux session and hasn't torn it down.
    PAUSED = "paused"  # User intentionally tore down the session; branch retained.
    ERROR = "error"  # Lifecycle failed mid-flight; error_detail is set.

    # Computed-at-read-time (never persisted).
    ACTIVE = "active"  # session up, agent pane had output within threshold
    IDLE = "idle"  # session up, agent pane quiet for >= threshold
    OFFLINE = "offline"  # persisted intent RUNNING but tmux session is gone
    ORPHANED = "orphaned"  # worktree directory missing on disk


# Statuses that may be persisted to disk. Used by `JsonWorkspaceStore.save` as
# a guard against accidentally serializing a computed value.
PERSISTED_STATUSES: frozenset[WorkspaceStatus] = frozenset(
    {WorkspaceStatus.RUNNING, WorkspaceStatus.PAUSED, WorkspaceStatus.ERROR}
)

# Statuses where the tmux session is up and the workspace is operational.
# Used by `ensure_can_pause` / `ensure_can_attach`.
LIVE_STATUSES: frozenset[WorkspaceStatus] = frozenset(
    {WorkspaceStatus.ACTIVE, WorkspaceStatus.IDLE}
)


class InitStatus(StrEnum):
    """Outcome of the init script for one workspace, persisted on `WorkspaceState`."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class BranchProvenance(StrEnum):
    """Whether Grove created this workspace's branch or the user attached one.

    Drives the kill-confirm default and the rollback-on-create-failure
    policy. Persisted on every `WorkspaceState`. The principle is that
    Grove manages the worktree always; the branch is the user's domain
    when they attached it, and Grove's only when Grove created it. The
    remote is never touched in either case — that is `git push --delete`
    territory and stays in the user's shell.
    """

    GROVE_CREATED = "grove"
    """Grove created the branch — Auto / NewNamed / TrackRemote (the local
    tracking side of the latter is fresh too). Default-delete on kill."""

    USER_ATTACHED = "attached"
    """User pointed Grove at a pre-existing local branch (ExistingLocal),
    or a root workspace adopting the live checkout. Default-keep on kill so a
    real feature branch is never lost to a tear-down."""


class Placement(StrEnum):
    """Where a workspace's tmux session is rooted, and what Grove manages for it.

    The dimension orthogonal to status: it never changes after create() and
    decides which side effects each lifecycle method may fire. Lives here
    (not in `contracts/branch_plan.py`) because `branch_plan` imports *from*
    this module; the enum has to sit on the depended-upon side to avoid a
    cycle. `RootBranch.resolve()` is the only producer of `ROOT`.
    """

    WORKTREE = "worktree"
    """The default and historical shape: a dedicated git worktree under
    `${repo}/.worktrees`, removable/recreatable, with its own branch. Every
    worktree git side effect (add on create/resume, remove on pause/kill) runs."""

    ROOT = "root"
    """The session runs in the repo root itself — no dedicated worktree, no
    Grove-created branch; it adopts whatever HEAD is checked out. Grove manages
    only the tmux session, so worktree add/remove and branch delete are all
    skipped, and pause/resume are refused (there is no worktree to free or
    rebuild). Recover a vanished session with respawn; stop it with kill."""


@dataclass(slots=True)
class WorkspaceState:
    """Persisted runtime record for one workspace."""

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
    # Optional free-form text the user attaches to the workspace. Defaults
    # to None so legacy on-disk records load without migration. Empty string
    # is normalized to None at write time so we don't ship two values that
    # mean the same thing.
    description: str | None = None
    init_env: dict[str, str] = field(default_factory=dict)
    # Init-script outcome from the last create() (or resume() with run_on_resume).
    # Surfaced to clients via WorkspacePeek so a broken workspace is diagnosable
    # from the rail without opening logs.
    init_status: InitStatus | None = None
    init_duration_ms: int | None = None
    init_log_path: str | None = None
    # How the branch came to be associated with this workspace. Drives the
    # kill-modal default. Defaults to GROVE_CREATED so legacy on-disk records
    # written before this field existed load without migration — historical
    # behavior was "Grove always created the branch", which is exactly that.
    branch_provenance: BranchProvenance = BranchProvenance.GROVE_CREATED
    # Worktree vs. root. Defaults to WORKTREE so legacy records (written before
    # this field existed) load as the only shape Grove used to support — exactly
    # the branch_provenance precedent, no migration. Read by every lifecycle
    # method to gate the worktree side effects; never mutated after create().
    placement: Placement = Placement.WORKTREE
    # The agent session id Grove minted at launch (Claude Code's --session-id),
    # or None for a generic/shell agent it doesn't introspect. This is the
    # deterministic correlation key: Grove launched the agent with it, so it
    # knows the transcript path by construction (epic #11 §2). Minimal persisted
    # identity — the full live session set + activity is computed at read time by
    # the ActivityService, never stored. Defaults to None so legacy records load
    # without migration (the branch_provenance/placement precedent).
    agent_session_id: str | None = None
    # The agent's adapter kind, captured at create from the (per-repo) resolved
    # AgentSpec. Persisted so the cross-project dashboard can pick the adapter
    # straight from the record instead of re-resolving `agent_name` against a
    # config that may scope the agent to specific repos (e.g. a "Simplify"
    # profile defined only in mifflin/dunder, absent from the daemon's global
    # config). Defaults to None so legacy records load without migration — the
    # ActivityService falls back to a config lookup when it's None.
    agent_kind: AgentKind | None = None


@dataclass(slots=True, frozen=True)
class CommitSummary:
    """One commit row, as the peek pane wants to render it.

    `committed_at` is a timezone-aware datetime; humanizing to "2 minutes ago"
    is the client's job — keeping policy out of the engine.
    """

    sha: str  # short (8 chars)
    subject: str
    committed_at: datetime


@dataclass(slots=True, frozen=True)
class WorkspacePeek:
    """Rich snapshot for the selected workspace, recomputed on demand.

    Pure data. The TUI calls `WorkspaceManager.peek(id)` whenever it wants a
    fresh frame for the rail; nothing here is cached, polled, or animated.
    Failures in the underlying helpers degrade to zeros / empty rather than
    raise — peek must never break the render loop.
    """

    state: WorkspaceState
    base_ahead: int
    base_behind: int
    diff_added: int
    diff_removed: int
    dirty_files: int
    recent_commits: tuple[CommitSummary, ...]
    agent_snapshot: str | None
    snapshot_taken_at: datetime | None


# ─── identity ────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(title: str) -> str:
    """Lowercase ASCII slug of a workspace title; collapses runs of non-alnum to '-'.

    Module-level on purpose: a stateless one-line primitive, used by both
    ``WorkspaceIdentity`` (for session / worktree names) and the
    ``BranchPlan`` variants (for ``AutoBranch``'s generated branch name).
    Promoting it to a class would be ceremony without payoff — it has
    nothing to encapsulate and no methods to grow.
    """
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return s or "ws"


class WorkspaceIdentity:
    """Deterministic identifiers for a new workspace.

    Owns the trio of facts every ``WorkspaceManager.create()`` call needs
    up front: a fresh workspace id, a timestamp suffix shared by the
    worktree path and the tmux session name, and the helpers to build
    those names.

    The branch is **not** here — it comes from the ``BranchPlan``, which
    has its own variant-aware ``resolve()``. Splitting the two concerns
    keeps each one nameable in a single sentence and lets the four
    branch variants (Auto / NewNamed / ExistingLocal / TrackRemote)
    each own their own naming policy without smuggling it through this
    class.
    """

    @staticmethod
    def new_id() -> str:
        """A fresh hex UUID for the workspace's persistent id."""
        return uuid.uuid4().hex

    @staticmethod
    def new_session_id() -> str:
        """A canonical (dashed) UUID for an agent session — distinct from `new_id`.

        Agent tools like Claude Code require an RFC-4122 UUID for `--session-id`,
        so this returns the dashed form rather than the workspace's bare hex id.
        Grove mints it, launches the agent with it, and thereby knows the
        transcript path by construction (deterministic correlation, #11 §2).
        """
        return str(uuid.uuid4())

    @staticmethod
    def timestamp() -> str:
        """``YYYYMMDD-HHMMSS`` UTC timestamp, the suffix shared by the
        worktree path and tmux session name (and the auto-generated
        branch name when ``AutoBranch`` is used)."""
        return datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")

    @staticmethod
    def session_name(cfg: GroveConfig, title: str, ts: str) -> str:
        """``{cfg.tmux.session_prefix}{slug(title)}-{ts}`` — the tmux session name."""
        return f"{cfg.tmux.session_prefix}{slug(title)}-{ts}"

    @staticmethod
    def worktree_path(cfg: GroveConfig, repo_root: Path, title: str, ts: str) -> Path:
        """``{root_template-expanded}/{slug(title)}-{ts}`` — where the worktree lives on disk."""
        base = expand_template(cfg.worktree.root_template, repo_root)
        return base / f"{slug(title)}-{ts}"


# ─── transition validators (pure; raise WorkspaceStateError) ────────────────


def ensure_can_pause(state: WorkspaceState) -> None:
    # Root workspaces have no worktree to free, so pause is meaningless: there
    # is nothing to reclaim and the session is the only managed resource. Refuse
    # loudly and point at kill (stop) / respawn (restart) instead of silently
    # tearing down the user's real checkout.
    if state.placement is Placement.ROOT:
        raise WorkspaceStateError(
            f"cannot pause workspace {state.id}: root workspaces have no worktree to free; "
            "use kill to stop it (respawn brings the session back)"
        )
    # `state.status` here is the *computed* status the caller observed. ACTIVE
    # and IDLE both mean "session is up"; either is fine. RUNNING (the raw
    # persisted intent) is also accepted so callers that bypass the manager's
    # promotion still work.
    if state.status not in (LIVE_STATUSES | {WorkspaceStatus.RUNNING}):
        raise WorkspaceStateError(
            f"cannot pause workspace {state.id}: status is {state.status}, expected active/idle"
        )


def ensure_can_resume(state: WorkspaceState) -> None:
    # A root workspace can never reach PAUSED (pause is refused above), so the
    # status check below already covers it; the explicit guard makes the error
    # legible if some caller hand-builds a PAUSED root record.
    if state.placement is Placement.ROOT:
        raise WorkspaceStateError(
            f"cannot resume workspace {state.id}: root workspaces are never paused; "
            "use respawn to restart the session"
        )
    if state.status != WorkspaceStatus.PAUSED:
        raise WorkspaceStateError(
            f"cannot resume workspace {state.id}: status is {state.status}, expected paused"
        )


def ensure_can_respawn(state: WorkspaceState) -> None:
    if state.status != WorkspaceStatus.OFFLINE:
        raise WorkspaceStateError(
            f"cannot respawn workspace {state.id}: status is {state.status}, expected offline"
        )


def ensure_can_kill(state: WorkspaceState) -> None:
    # Kill is the universal escape hatch — accept every status, including the
    # computed ones. The only thing kill CAN'T tolerate is an unknown enum
    # value, which would already raise on attribute access.
    del state


def ensure_can_attach(state: WorkspaceState) -> None:
    # Attach requires a live session. ACTIVE and IDLE are both fine; OFFLINE
    # is not — the user should respawn first.
    if state.status not in (LIVE_STATUSES | {WorkspaceStatus.RUNNING}):
        raise WorkspaceStateError(
            f"cannot attach to workspace {state.id}: status is {state.status}, expected active/idle"
        )


def ensure_can_update(state: WorkspaceState) -> None:
    # Update is metadata-only (title / description); permitted in every
    # status except ORPHANED, where the worktree is gone and the record is
    # headed for kill anyway. Renaming a doomed record adds confusion
    # without a use case. ERROR is intentionally allowed: a user might
    # want to add a "see ticket #123" note while the workspace is broken.
    if state.status == WorkspaceStatus.ORPHANED:
        raise WorkspaceStateError(
            f"cannot update workspace {state.id}: status is orphaned (worktree missing); "
            "kill the record instead"
        )
