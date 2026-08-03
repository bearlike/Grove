"""Workspace state record + identity + status transitions.

A Workspace is a (git worktree, tmux session, agent) triple. This module
defines the data shape and the rules for how its status may evolve. Pure;
no I/O — those live in `git.py`, `tmux.py`, and `store.py`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from grove.core.config import AgentKind, GroveConfig, expand_template
from grove.core.errors import WorkspaceStateError

if TYPE_CHECKING:
    # Imported for the annotation only. `ticket_refs` defaults to an empty list,
    # so no `TicketRef` symbol is needed at runtime — and with postponed
    # annotations (`from __future__ import annotations`) the `list[TicketRef]`
    # hint is never evaluated. This keeps the engine dataclass on the
    # depended-upon side of the import arrow (contracts import workspace, never
    # the reverse) — the same one-directional trick `contracts/activity.py`
    # uses to reference engine dataclasses without a cycle.
    # Same one-directional trick, and here it is load-bearing rather than tidy:
    # `container_runtime` reaches `git` (via the devcontainer CLI boundary) and
    # `git` imports this module, so a runtime import would close a cycle. The
    # field defaults to None, so no symbol is needed at runtime.
    from grove.core.container_runtime import ContainerRuntimeState
    from grove.core.contracts.tickets import TicketRef


class WorkspaceStatus(StrEnum):
    """Workspace status — split into persistent intents and computed views.

    Three values are persistent intents: lifecycle methods write them to JSON
    and `JsonWorkspaceStore` rejects writes of any other value (defense in
    depth). The remaining four are derived at read time by
    `WorkspaceManager._reconcile_status` from the persistent intent + tmux
    session presence + worktree presence + tmux pane activity. `list()` and
    `peek()` always promote intents to displayed values, so callers reading
    through the manager see ACTIVE/IDLE/OFFLINE/PAUSED/ORPHANED/PROVISIONING/
    ERROR — never the raw RUNNING intent.

    **PROVISIONING earns its place by CHANGING THE REMEDY, which is the bar
    this enum is held to.** A container workspace is persisted the moment
    `create` starts, minutes before `devcontainer up` returns, and for that
    whole window the container legitimately does not exist yet — so the
    container dimension folded it onto OFFLINE, whose meaning is *the runtime
    is gone, respawn is the remedy*. Every word of that is wrong here: nothing
    is gone, and respawn is the one action that destroys the build in flight.
    The user is shown a dead-looking workspace and offered the button that
    kills it. The remedy for PROVISIONING is to WAIT, which no other status
    says, and it is the only status whose whole point is that it will end on
    its own.
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
    PROVISIONING = "provisioning"  # container being built/started right now


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


class ProvisionStatus(StrEnum):
    """Outcome of the container provisioning for one workspace.

    Field-for-field the :class:`InitStatus` trio (`provision_status` /
    `provision_duration_ms` / `provision_log_path`), deliberately reusing that
    seam rather than inventing a second vocabulary: both answer "did the
    preparation step this workspace needed succeed, and where is its log".
    ``SKIPPED`` is a host-runtime workspace — nothing to provision, not a
    failure.

    ``PROVISIONING`` is the one member with no :class:`InitStatus` twin, and
    the asymmetry is real rather than an oversight: an init script runs for
    seconds inside a verb nobody is watching, while a container provision runs
    for minutes with the record already visible on every surface. It is the
    IN-FLIGHT state, written before the CLI is invoked and overwritten by the
    outcome — so a record carrying it after the daemon died is a provision
    whose process is gone, and `respawn` is what restarts it.
    """

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    PROVISIONING = "provisioning"


@dataclass(frozen=True, slots=True)
class ProvisionProgress:
    """What a user staring at a provisioning workspace needs to know.

    Three facts, and each answers a question the others cannot. ``elapsed_ms``
    answers *has this been going long enough to worry*. ``headline`` — the last
    non-blank line the provisioner wrote — answers *is it moving*, which no
    timer can. ``lines`` is the recent tail behind a fold, for the user who
    wants to see the build itself.

    **Deliberately not a parsed step or a percentage.** The lines come from the
    devcontainer CLI and BuildKit, whose output is somebody else's format with
    no contract; deriving "step 4 of 9" from it is provider-boundary policy
    that goes stale silently and reports confidently wrong progress, which is
    worse than no progress at all. Grove's own marker lines are already in the
    same stream, so the honest headline is whatever was written last.

    **Known and accepted: on a FAILED provision the tail is the CLI's Node
    stack trace**, which ``_ProvisionLog.diagnose`` exists precisely to look
    past. That is not a gap here. This type serves the workspace that is still
    building, where the last line written IS the build; a provision that failed
    has already raised an error carrying the marker-selected diagnosis, and its
    tail is then legitimately "the last thing that happened". Do not add
    marker selection here — it would hide the live build output this exists to
    show. Measured at 20 ms against a real 1.1 MB cold-build log.
    """

    elapsed_ms: int | None
    headline: str
    lines: tuple[str, ...]

    TAIL_LINES: ClassVar[int] = 40
    """Enough to show a build actually moving, small enough to ride a response
    body that a fleet view fetches per provisioning workspace."""

    @classmethod
    def read(cls, state: WorkspaceState, *, log_path: Path | None = None) -> ProvisionProgress:
        """Compose the progress of *state*'s in-flight provision.

        Best-effort by contract, like every other render-path read: an
        unreadable or absent log yields empty text rather than raising, because
        the caller is a status surface and the log is a courtesy the provision
        never waits on.
        """
        path = log_path or (Path(state.provision_log_path) if state.provision_log_path else None)
        lines = cls._tail(path)
        return cls(
            elapsed_ms=cls._elapsed_ms(state),
            headline=lines[-1] if lines else "",
            lines=lines,
        )

    @staticmethod
    def _elapsed_ms(state: WorkspaceState) -> int | None:
        """How long the provision has taken — counting, or final.

        The finished duration WINS, and it has to: `provision_started_at`
        outlives the provision (a finished one can still say when it began), so
        deriving elapsed from the clock alone reports a provision that ran for
        11 seconds as having taken five minutes and climbing, purely because
        nobody has looked at the workspace since. Caught by running a real
        create rather than by a test, which is the usual way for a field that
        is only wrong once the interesting moment has passed.

        `_provision_container` clears `provision_duration_ms` when it starts,
        so "the duration is set" is exactly "the provision is over".
        """
        if state.provision_duration_ms is not None:
            return state.provision_duration_ms
        elapsed = _provision_elapsed_seconds(state)
        return None if elapsed is None else elapsed * 1000

    @staticmethod
    def _tail(path: Path | None) -> tuple[str, ...]:
        """The last :attr:`TAIL_LINES` non-blank lines, or nothing.

        Reads the whole file rather than seeking backwards: a cold build's log
        reaches ~1 MB, which is one cheap read against the complexity of a
        reverse scan, and this runs per request rather than per poll tick.
        """
        if path is None:
            return ()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ()
        kept = [line.rstrip() for line in text.splitlines() if line.strip()]
        return tuple(kept[-ProvisionProgress.TAIL_LINES :])


class Runtime(StrEnum):
    """Where a workspace's agent process actually runs — the third
    orthogonal dimension, after status and :class:`Placement`.

    Persisted rather than re-derived from config, for the same reason
    ``placement`` is: every lifecycle verb needs the answer, and re-resolving it
    from the cascade would silently move an existing workspace into a container
    the moment the default flipped. It selects the ``LaunchBackend``, so it is a
    narrow literal type driving a branch, never a bool.

    Chosen ONCE at create (from ``CreateWorkspaceRequest.runtime``, else the
    ``container.enabled`` cascade default) and thereafter only ever promoted
    HOST → CONTAINER by a ``respawn`` that clears a
    :attr:`WorkspaceState.runtime_fallback_reason`.
    """

    HOST = "host"
    """The historical shape: the agent runs directly on this host (tmux pane or
    a detached process), sharing the host filesystem and namespace."""

    CONTAINER = "container"
    """The agent runs inside a devcontainer Grove provisioned for the worktree.
    The blast radius is the worktree plus whatever the config mounts — not a
    credential boundary."""


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


@dataclass(slots=True, frozen=True)
class TranscriptContext:
    """Optional per-workspace override for reading a transcript recorded under
    a different runtime context than the reader's — the agent's
    actual cwd and config dir, as its own transcript records them, are not the
    host reader's ``agent_cwd`` / ambient ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``,
    so a read with no override searches the wrong folder name (``encode_cwd``)
    under the wrong config-dir cascade entirely.

    Carries only what a *read* needs — the adapters' own parsing and env
    resolution are unchanged (the provider-boundary rule): a caller
    scopes the matching env var to ``config_dir`` for one read call, and
    substitutes ``agent_cwd`` wherever the workspace's own ``agent_cwd``/
    ``scan_cwds`` would otherwise be passed in. ``agent_cwd`` is matched only
    as an opaque string against each transcript record's own ``cwd`` field —
    it never needs to resolve as a real path on this host.

    **Populated at every launch** (create / resume / respawn) by
    :meth:`for_launch`, off the *composed* launch env the agent actually
    receives — so a relaunch after a config edit re-derives it instead of
    letting a stale value stand, and a removed pin clears it back to ``None``.
    ``None`` (the default, and still the overwhelmingly common case: no pin, or
    a kind with no config-dir concept) means every read falls back to
    ``agent_cwd``/``scan_cwds`` and the ambient env exactly as before, so every
    legacy record and every unpinned workspace reads byte-for-byte as it did.

    **``config_dir`` is always a HOST path; ``agent_cwd`` is always the
    runtime's own string** — an asymmetry only the launch backend can honor,
    which is why ``LaunchBackend.transcript_context`` computes the whole record
    and the manager stores it verbatim (a ``host_namespace`` boolean alone
    could only say "my paths are meaningless to you", never what they mean). A
    container backend translates its config dir through its own mounts and
    leaves the cwd untranslated — the mount-aware case the ``agent_cwd`` field
    has always anticipated, and the reason that field is an opaque string
    rather than a ``Path``. Where nothing is
    reachable, the backend answers ``None``, which is the honest answer *and*
    the behavior that works today: the reader's ambient env is what finds a
    bind-mounted transcript.
    """

    CONFIG_DIR_ENV: ClassVar[dict[str, str]] = {
        "claude_code": "CLAUDE_CONFIG_DIR",
        "codex": "CODEX_HOME",
    }
    """Agent kind → the env var whose value that kind's filesystem adapter
    resolves its data root from. A provider-protocol fact (see
    ``grove.core.agents.claude_code`` / ``codex``), not user policy, so it is
    named in code rather than config. A kind absent from the map (mewbo,
    generic) has no config-dir concept at all: nothing to pin at launch, and a
    scope around a read is a no-op for it. THE single definition — the read
    side (``WorkspaceManager.transcript_config_dir_scope``) and the write side
    (:meth:`for_launch`) must agree, so neither may keep its own copy."""

    config_dir: str
    """Host directory standing in for the runtime's CLAUDE_CONFIG_DIR/
    CODEX_HOME (e.g. an agent-pinned profile dir, or the host side of a bind
    mount)."""

    agent_cwd: str
    """The cwd string the transcript itself records (e.g. a
    container-internal path)."""

    @classmethod
    def for_launch(
        cls, *, kind: str, env: Mapping[str, str], agent_cwd: Path
    ) -> TranscriptContext | None:
        """The context to persist for an agent launched with ``env`` in ``agent_cwd``.

        The asymmetry this exists to close: a filesystem adapter resolves
        its data root from the **reading** process's ambient environment (the
        daemon's, the TUI's), but the agent runs under a deliberately hermetic,
        *different* env (``env_unset`` then ``env``). So an operator who
        pins a profile per agent — ``AgentSpec.env = {"CLAUDE_CONFIG_DIR": …}``,
        the documented mechanism — sends the agent's transcripts somewhere
        the reader never looks; no transcript found means the workspace pins at
        STARTING with a blank agent axis forever. Recording where the agent was
        actually pointed is what lets the read scope itself to match.

        Pure: ``env`` is the caller's already-composed launch env, never
        ``os.environ`` — reading the process env here would re-introduce the
        very reader-vs-agent confusion above, and side effects belong at the
        edges. ``None`` — the common case, and the exact historical behavior —
        whenever ``kind`` has no config-dir env var (mewbo/generic) or the
        launch env pins none, counting a blank or all-whitespace value as
        "none": both adapters ``strip()`` before resolving, so such a pin never
        reached a real directory anyway, and the store decoder rejects the same
        shape — a value that survives a round-trip unchanged is the invariant
        both entry points have to uphold.

        ``agent_cwd`` is recorded as the *launch* cwd, which for a host launch
        is the same path the reader would scan anyway (``transcript_scan_cwds``
        dedupes it away). It is only load-bearing for a runtime whose recorded
        cwd differs from the host's — the container case this class was built
        for, whose backend would supply a mount-aware value.
        """
        var = cls.CONFIG_DIR_ENV.get(kind)
        if var is None:
            return None
        config_dir = env.get(var, "").strip()
        if not config_dir:
            return None
        return cls(config_dir=config_dir, agent_cwd=str(agent_cwd))


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
    # Where the agent session starts, relative to the worktree root. A
    # POSIX relative path (e.g. "homelab", "services/api") or "" for the worktree
    # root itself — the historical shape. The worktree and branch always anchor
    # at the repo root; this only moves the agent's cwd into a nested *project*
    # subdir, so several subdirs of one repo can be distinct projects. Defaults
    # to "" so legacy records load without migration (the placement precedent).
    project_subpath: str = ""
    # The agent session id Grove minted at launch (Claude Code's --session-id),
    # or None for a generic/shell agent it doesn't introspect. This is the
    # deterministic correlation key: Grove launched the agent with it, so it
    # knows the transcript path by construction. Minimal persisted
    # identity — the full live session set + activity is computed at read time by
    # the ActivityService, never stored. Defaults to None so legacy records load
    # without migration (the branch_provenance/placement precedent).
    agent_session_id: str | None = None
    # The agent's adapter kind, captured at create from the (per-repo) resolved
    # AgentSpec. Persisted so the cross-project dashboard can pick the adapter
    # straight from the record instead of re-resolving `agent_name` against a
    # config that may scope the agent to specific repos (e.g. a "Work"
    # profile defined only in private-repos, absent from the daemon's global
    # config). Defaults to None so legacy records load without migration — the
    # ActivityService falls back to a config lookup when it's None.
    agent_kind: AgentKind | None = None
    # External tickets associated with this workspace. The branch name is the
    # source of truth: create() and the existing-branch attach path derive these
    # by parsing the final branch through the configured providers (>1 match →
    # each ref `ambiguous=True`); a manual attach/detach overrides. Pydantic
    # `TicketRef` (it crosses the wire on every workspace view), serialized
    # explicitly by the store. Defaults to an empty list so legacy records load
    # without migration — the branch_provenance/placement precedent.
    ticket_refs: list[TicketRef] = field(default_factory=list)
    # Optional override for reading this workspace's transcript from a
    # different runtime context than the host — see TranscriptContext.
    # None (the default) means every transcript read falls back to agent_cwd/
    # scan_cwds and the ambient config-dir env exactly as before; legacy
    # records load without migration, same precedent as agent_kind/placement.
    transcript_context: TranscriptContext | None = None
    # Where this workspace's agent runs. Defaults to HOST so every
    # record written before runtime selection existed loads as the only shape
    # Grove used to support — the placement/branch_provenance precedent, no
    # migration step. Read (never re-resolved from config) by every launch verb
    # to pick the backend.
    runtime: Runtime = Runtime.HOST
    # Whether this workspace's agent is handed Grove's first-turn brief (the
    # pointer at the `working-in-grove` skill). Resolved once at create from
    # `cfg.brief.enabled` or the explicit request flag, then persisted, so a
    # later change to the config default never re-decides for a workspace that
    # already exists. Defaults to False so records written before the brief
    # existed load as what they are — never briefed — with no migration, the
    # placement/branch_provenance precedent.
    brief: bool = False
    # Non-None ⟺ a container was wanted and a host workspace was produced,
    # because the container runtime was UNAVAILABLE at create (D5 arm 4).
    # The requested value needs no second field: the fallback only ever runs
    # container → host. Persists for the workspace's lifetime and never
    # auto-clears — the isolation guarantee cannot retroactively apply to work
    # already done — until a `respawn` succeeds in promoting the workspace.
    runtime_fallback_reason: str | None = None
    # Container provisioning outcome, field-for-field with the init trio above.
    # SKIPPED for a host workspace; the log is written AS the provision runs, so
    # a timed-out cold build still leaves an artifact.
    provision_status: ProvisionStatus | None = None
    provision_duration_ms: int | None = None
    provision_log_path: str | None = None
    # When the in-flight provision started, ISO-8601 UTC. `provision_duration_ms`
    # answers "how long did it take" only once it is over; a surface telling a
    # user whether to keep waiting needs the elapsed time of a provision that
    # has NOT finished, and the two cannot be one field — a duration written at
    # the end is absent for the entire window it would have been useful in.
    # Left in place after the outcome lands, so a finished provision can still
    # say when it began. Legacy records load as None; no migration.
    provision_started_at: str | None = None
    # The container this workspace's agent runs in, or None for host mode. ONE
    # optional field rather than a dozen scalars: the identity, its argv builders
    # and the teardown invariant travel together as one object, so no call site
    # can assemble a command from half of it. Every fact on it is what
    # `devcontainer up` REPORTED, never re-derived from Grove's own guesses.
    # Defaults to None so legacy records load without migration (the
    # transcript_context precedent), and None stays the honest answer for
    # every host-mode workspace.
    container: ContainerRuntimeState | None = None
    # True iff this container workspace ran Grove's PACKAGED default config
    # (D5 arm 3: the repo had no `.devcontainer/` of its own at create time).
    # Persisted rather than re-derived, because the answer is a create-time
    # fact: a repo that commits a devcontainer.json later must not retroactively
    # change what an existing workspace was built from. It is what every surface
    # renders the "running on Grove's default container" notice from — a notice,
    # never a warning: absence of a config is not a degradation. False for a host
    # workspace and for one that used the project's own config, so legacy
    # records decode as the historical shape.
    runtime_default_config: bool = False

    @property
    def runtime_no_tmux(self) -> bool:
        """This container has no in-container tmux — the agent runs bare.

        **Derived, not stored** — the fact already lives on
        ``container.tmux_command`` (empty string: the "no bundle for
        this arch and the image ships none" degradation). Storing a second
        field would let it drift from the one that produces it; a property
        cannot. `runtime is HOST` (including a fallback workspace) always
        reads `False` here — this is a CONTAINER-mode fact, distinct from
        `runtime_fallback_reason` (container unavailable at all) and
        `runtime_default_config` (no project devcontainer.json): a container
        can come up fine, on Grove's own packaged config or the project's,
        and still ship no tmux to hold the agent past a client detach.
        """
        if self.runtime is not Runtime.CONTAINER or self.container is None:
            return False
        return not self.container.tmux_command

    @property
    def init_env(self) -> dict[str, str]:
        """The ``GROVE_*`` variables an init script runs with.

        **Derived, not stored** — and it replaces a persisted field of the same
        name that had neither producer nor consumer, round-tripping empty
        through every save. All four values are already on this record, so
        storing them was caching facts we hold, with a way to be wrong that the
        derivation does not have: a stored env survives a branch rename or a
        moved worktree and then reports the old one. A property cannot go stale.

        A property rather than a builder at the call sites for the reason the
        asymmetry existed at all: `create` composed these inline while `resume`
        and `respawn` passed nothing, so `$GROVE_BRANCH` was silently empty on
        every `run_on_resume` run. One derivation on the record that owns the
        facts leaves no call site able to forget.
        """
        return {
            "GROVE_REPO": self.repo_root,
            "GROVE_WORKTREE": self.worktree_path,
            "GROVE_BRANCH": self.branch,
            "GROVE_AGENT": self.agent_name,
        }

    @property
    def grove_owns_branch(self) -> bool:
        """Grove created this branch, so a Grove teardown may delete it.

        The single definition of "whose branch is this", read by ``kill()`` (as
        the default for its ``delete_branch`` flag, which an explicit caller may
        still override) and by ``_rollback_create`` (as a hard gate — a rollback
        the user never asked for has no override). The rule lived only in
        ``kill`` once: rollback force-deleted whatever branch the record
        named, so a user who attached their own ``feature/x`` and hit a failing
        init script lost it to a create that never completed — the same damage
        an unvalidated branch name does, arriving through teardown instead
        of argv. ROOT placement is never Grove's: the branch there is the live
        checkout the workspace adopted.
        """
        return (
            self.placement is not Placement.ROOT
            and self.branch_provenance is BranchProvenance.GROVE_CREATED
        )

    @property
    def agent_cwd(self) -> Path:
        """Absolute directory the agent session runs in: ``worktree / subpath``.

        The single source of truth for "where the agent starts", reused by
        every session-(re)creation path (resume/respawn) so they can't drift on
        the nested-cwd rule. ``project_subpath == ""`` collapses to the
        worktree root — the historical behavior.
        """
        base = Path(self.worktree_path)
        return base / self.project_subpath if self.project_subpath else base

    @property
    def scan_cwds(self) -> tuple[Path, ...]:
        """The cwds whose transcripts/sidecars may belong to this workspace (#F7).

        The union of ``agent_cwd`` (``worktree/subpath`` — where the agent is
        configured to run, and where a nested project's transcripts record their
        cwd) and the worktree ROOT (``worktree_path`` — where a session
        hand-started at the repo/worktree root records *its* cwd). Re-keying
        discovery from the worktree root to ``agent_cwd`` alone silently
        dropped that root-recorded session for a nested project; scanning both
        recovers it. Deduped when the subpath is empty (the flat-workspace common
        case), so the second entry only exists for a genuinely nested project.
        ``agent_cwd`` first — the primary project cwd; callers that need
        newest-first across the union re-sort by mtime.
        """
        base = Path(self.worktree_path)
        agent = self.agent_cwd
        return (agent,) if agent == base else (agent, base)

    @property
    def transcript_scan_cwds(self) -> tuple[Path, ...]:
        """The cwd(s) a transcript read should scan for this workspace.

        The context's recorded cwd (first — it is the most specific answer)
        UNIONED with the ordinary ``scan_cwds``, deduped in order. No override
        (the default) is byte-for-byte ``scan_cwds``.

        **A deliberate widening of the original behavior**, which had the
        override REPLACE the union on the reasoning that a container-recorded
        cwd can never equal a host path. That held while only a hypothetical
        container launch would set a context; an ordinary HOST launch now sets
        one too, and for a nested project (``project_subpath``) replacement
        would narrow the scan from ``{agent_cwd, worktree_root}`` to one entry
        — silently dropping the root-recorded session the union arm above
        exists to recover. The union cannot lose data in the container
        case either: a host path that holds no matching transcript simply globs
        empty, so the extra entry costs one empty scan, never a wrong answer.
        """
        ctx = self.transcript_context
        if ctx is None:
            return self.scan_cwds
        recorded = Path(ctx.agent_cwd)
        return (recorded, *(cwd for cwd in self.scan_cwds if cwd != recorded))

    def adopts_session(
        self, born_at: datetime | None, *, live_here_at: datetime | None = None
    ) -> bool:
        """Whether a *discovered* (non-minted) session belongs to this workspace.

        Two independent kinds of evidence, either sufficient — each measured
        against this workspace's own ``created_at`` (a session is ours only if
        it was alive here at/after we came into being):

        - ``born_at`` — the session's transcript BIRTH (first-record timestamp).
          Immutable, so a stale file merely being touched or re-read can't fake
          it. A workspace's cwd can hold transcripts written before it existed —
          most commonly ROOT placement, whose cwd is the shared repo root — and
          treating "newest transcript in the cwd" as "our session" (the pre-fix
          bug) presented a stale, unrelated conversation as a brand-new
          workspace's own. Birth, not recency, is the correct test.
        - ``live_here_at`` — the timestamp of a hook sidecar proving the session
          was live *in this workspace* (the caller attributes it by cwd/pane at
          the boundary; this predicate stays pure and never reads a sidecar).
          A session the user RESUMED inside this workspace's pane is born
          *earlier* than the workspace, so birth can never adopt it — but its
          post-create sidecar ts does. Birth alone silently drops every
          resumed session; the ``>= created_at`` guard still rejects a
          previous tenant of a reused cwd, whose sidecar predates this
          workspace.

        Used by both discovery-adoption sites (`ActivityService.sessions_for`,
        `SessionExplorer.for_workspace`); a session Grove itself minted and
        launched never calls this — it is unconditionally ours, born or not.

        Both timestamps are optional (unknown birth, no sidecar) and never adopt
        when absent — unproven evidence can't be shown to postdate creation. A
        tz-naive value is coerced to UTC defensively so a malformed timestamp
        degrades the comparison rather than raising on the poll path (the
        peek/best-effort discipline).
        """
        return self._is_after_create(born_at) or self._is_after_create(live_here_at)

    def _is_after_create(self, ts: datetime | None) -> bool:
        """``ts`` is present and at/after this workspace's ``created_at`` (UTC-coerced)."""
        if ts is None:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts >= self.created_at


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
        transcript path by construction, deterministically.
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


def ensure_can_pause(state: WorkspaceState, *, clean: bool = True) -> None:
    """Preconditions for `pause`, including the one the docs always claimed.

    ``clean`` is the caller's answer to "would removing this worktree discard
    anything" — a parameter rather than a probe so this predicate stays pure,
    exactly as ``ensure_can_respawn``'s ``promotable`` is. It cannot be probed
    here regardless: `workspace.py` cannot import `git.py`, which imports
    ``CommitSummary`` from this module.

    **Why it belongs at the precondition layer and not where git put it.**
    `pause` kills the tmux session, stops the container, and only then removes
    the worktree — and git refuses a dirty removal, so the refusal used to land
    after two side effects had already run. The user was told the pause failed,
    which reads as "nothing happened", while the workspace sat with no session,
    a stopped container, a worktree still on disk and a record still RUNNING:
    a state no verb produces deliberately. A precondition runs before any of it,
    so a refused pause really is a no-op.

    The destructive ordering below it is untouched and must stay so — the pane's
    exec dies with the session, and the worktree is the container's bind mount.
    That ordering constrains the teardown; it says nothing about a read-only
    probe of an intact worktree taken before the teardown begins.

    Defaults to ``True`` so a caller that has not asked git behaves exactly as
    before: git remains the backstop, this is the early and accurate refusal.
    """
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
    if not clean:
        raise WorkspaceStateError(
            f"cannot pause workspace {state.id}: the worktree has uncommitted changes "
            "(or could not be inspected); commit them, or pause with force to discard them"
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


def ensure_can_respawn(
    state: WorkspaceState, *, promotable: bool = False, sessionless: bool = False
) -> None:
    """Respawn restarts a dead session — or promotes a fallback workspace.

    The OFFLINE gate is right for the recovery case it was written for: a
    session that vanished. But respawn is ALSO the documented way out of a
    runtime fallback ("fix the runtime, then `grove respawn` to promote"), and
    a fallback workspace is alive on the host — ACTIVE or IDLE, never OFFLINE.
    Gating on OFFLINE alone made that instruction unreachable through every
    verb a user has: respawn refused as "expected offline", and pausing first
    only changed the complaint to "expected offline, got paused". The promotion
    machinery existed and nothing could reach it.

    ``promotable`` is the caller's answer to "would promoting actually succeed
    right now" — it stays a parameter rather than a probe so this predicate
    remains pure. It must mean *the runtime is available again*, not merely
    *this workspace fell back*: with containers on by default every host-mode
    workspace on a Docker-less machine carries a fallback reason, so treating
    the reason alone as license would restart a live session to promote it into
    a runtime that is still missing — strictly worse than refusing.

    ``sessionless`` is the second such fact, and it exists because a container
    workspace whose agent lives in a tmux INSIDE the container reads
    ACTIVE/IDLE with no host session at all, so "the session vanished" — the
    exact case this gate was written for — is no longer spelled OFFLINE.
    Without it the one verb that re-opens a viewport refuses to run on the
    only workspaces that need it. Same discipline as ``promotable``: the
    caller establishes the fact (one ``has_session``) and the predicate stays
    pure.

    It admits a LIVE status only, and that narrowness is the whole of its
    safety. PAUSED, ERROR and ORPHANED workspaces all have no session either,
    and none of them is a viewport to rebuild — a blanket bypass would have
    turned respawn into a verb that accepts every workspace on the host, which
    is precisely what the status gate is for.
    """
    if promotable:
        return
    if sessionless and state.status in LIVE_STATUSES:
        return
    if state.status != WorkspaceStatus.OFFLINE:
        raise WorkspaceStateError(
            f"cannot respawn workspace {state.id}: status is {state.status}, expected offline"
        )


def ensure_can_kill(state: WorkspaceState) -> None:
    # Kill is the universal escape hatch — accept every status, including the
    # computed ones. The only thing kill CAN'T tolerate is an unknown enum
    # value, which would already raise on attribute access.
    del state


def _not_live_detail(state: WorkspaceState, *, verb: str) -> str:
    """Why *verb* is refused, phrased as the thing to DO next.

    A refusal naming only the status is a dead end for the one status the user
    can neither fix nor wait out knowingly: "status is provisioning" reads the
    same as "status is offline", and the remedies are opposites — wait, versus
    respawn. So PROVISIONING gets the elapsed time and the log path, which is
    the whole answer to "is this stuck or is it working".
    """
    base = f"cannot {verb} workspace {state.id}: status is {state.status}"
    if state.status is not WorkspaceStatus.PROVISIONING:
        return f"{base}, expected active/idle"
    waited = _provision_elapsed_seconds(state)
    so_far = f" ({waited}s so far)" if waited is not None else ""
    log = f"; follow it in {state.provision_log_path}" if state.provision_log_path else ""
    return (
        f"{base} — the container is still being built{so_far} and this workspace"
        f" will come up on its own{log}"
    )


def _provision_elapsed_seconds(state: WorkspaceState) -> int | None:
    """Whole seconds since the in-flight provision began, or ``None``.

    ``None`` for a record with no start stamp (every workspace created before
    the field existed) and for an unparseable one — an absent number is honest
    where a zero would read as "it just started".
    """
    if not state.provision_started_at:
        return None
    try:
        started = datetime.fromisoformat(state.provision_started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - started).total_seconds()))


def ensure_can_attach(state: WorkspaceState) -> None:
    # Attach requires a live session. ACTIVE and IDLE are both fine; OFFLINE
    # is not — the user should respawn first.
    if state.status not in (LIVE_STATUSES | {WorkspaceStatus.RUNNING}):
        raise WorkspaceStateError(_not_live_detail(state, verb="attach to"))


def ensure_can_steer(state: WorkspaceState) -> None:
    # Steering (send_message) types into the live agent pane, so it has
    # exactly attach's precondition: the session must be up. OFFLINE wants
    # a respawn first; PAUSED has no session at all.
    if state.status not in (LIVE_STATUSES | {WorkspaceStatus.RUNNING}):
        raise WorkspaceStateError(_not_live_detail(state, verb="send message to"))


def ensure_can_update(state: WorkspaceState) -> None:
    # Update is metadata-only (title / description); permitted in every
    # status except ORPHANED, where the worktree is gone and the record is
    # headed for kill anyway. Renaming a doomed record adds confusion
    # without a use case. ERROR is intentionally allowed: a user might
    # want to add a note describing why the workspace is broken.
    if state.status == WorkspaceStatus.ORPHANED:
        raise WorkspaceStateError(
            f"cannot update workspace {state.id}: status is orphaned (worktree missing); "
            "kill the record instead"
        )
