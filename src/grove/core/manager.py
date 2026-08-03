"""WorkspaceManager — the only orchestrating class in grove.core.

A manager is bound to a single repo_root + GroveConfig. It composes the
side-effecting modules (git, tmux, store) into the lifecycle operations:
create, pause, resume, kill, attach, list. Subscribers (the TUI) are
notified via plain callbacks; no event bus, no asyncio queue.

Side effects are concentrated in `git.py` and `tmux.py`; this module
sequences them and persists state. Errors are caught at orchestration
boundaries, recorded onto the state record, emitted as `error` events,
then re-raised as GroveError so callers can render a toast.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from grove.core import channel, native, paths, permission, tmux
from grove.core.agents import (
    AgentAdapter,
    AgentQuestion,
    AnswerSelection,
    SessionControls,
    TodoList,
    all_adapters,
    get_adapter,
    resolve_models,
)
from grove.core.agents.brief import AgentBrief
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.hook import DEFAULT_DAEMON_LOOPBACK_URL, ClaudeHook, PendingQuestion
from grove.core.config import AgentKind, AgentSpec, GroveConfig, load_config
from grove.core.container_agent import ContainerAgent, ContainerAgentEntry
from grove.core.container_decor import DecorPayload, DecorPlan
from grove.core.container_policy import AgentSharePlan
from grove.core.container_runtime import ContainerLifecycle, ContainerLiveness, ContainerState
from grove.core.container_tmux import ContainerPaneLiveness, ContainerTmux, PaneReading
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.branch_plan import AutoBranch, BranchMode, ResolvedBranch
from grove.core.contracts.questions import QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketRef, TicketSelector
from grove.core.devcontainer import DevcontainerCli
from grove.core.env_source import EnvSource
from grove.core.errors import (
    AgentSessionNotFound,
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    CapabilityUnavailable,
    ContainerError,
    EnvSourceError,
    GroveError,
    MewboError,
    PaneNotFound,
    QuestionAnswerInvalid,
    QuestionNotPending,
    ResumeNotSupported,
    SteeringUnsupported,
    TmuxError,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.git import GitRepo
from grove.core.launch import (
    AgentExit,
    DevcontainerLaunchBackend,
    LaunchBackend,
    LaunchSpec,
    TmuxLaunchBackend,
)
from grove.core.mewbo import MewboClient
from grove.core.phase import PhaseFile, PhaseReport, TaskPhase
from grove.core.preflight import HostPreflight
from grove.core.runtime import ContainerProvisioner, RuntimeDecision, RuntimeResolver
from grove.core.store import JsonWorkspaceStore
from grove.core.tickets import TicketProviderRegistry
from grove.core.tmux import AttachInstruction, ContainerAttach
from grove.core.workspace import (
    LIVE_STATUSES,
    CommitSummary,
    InitStatus,
    Placement,
    ProvisionProgress,
    ProvisionStatus,
    Runtime,
    TranscriptContext,
    WorkspaceIdentity,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
    ensure_can_attach,
    ensure_can_kill,
    ensure_can_pause,
    ensure_can_respawn,
    ensure_can_resume,
    ensure_can_steer,
    ensure_can_update,
)

if TYPE_CHECKING:
    # Local-imported at call time (sessions.py imports manager.py — a module-level
    # import here would cycle); typed under TYPE_CHECKING for annotations only.
    from grove.core.sessions import SessionExplorer

# Module-scope alias so method return annotations don't resolve `list` to the
# `WorkspaceManager.list` method (the class-scope shadowing mypy trap — see also
# `primary_transcript`'s tuple return).
_Argv = list[str]

EventKindStr = Literal[
    "created",
    "paused",
    "resumed",
    "respawned",
    "killed",
    "updated",
    "message_sent",
    "question_answered",
    "control_invoked",
    "error",
    "offline_detected",
    "orphaned_detected",
    # A provision has STARTED. The only event kind announcing the beginning of
    # something rather than its outcome, because it is the only step long
    # enough that its beginning is news: `created`/`resumed`/`respawned` all
    # arrive minutes later, on the far side of it.
    "provisioning",
]

# Agent kinds steered over their own API rather than tmux injection. The
# membership check in send_message/interrupt routes these to _steer_remote —
# the single remote dispatch point. frozenset[str] rather than AgentKind:
# the persisted agent_kind being matched is read back from JSON as plain str.
_REMOTE_STEERED_KINDS: frozenset[str] = frozenset({"mewbo"})

# Agent kinds whose tools expose an in-session SLASH-CONTROL surface — a
# ``/name`` command/skill invocation and the interactive ``/model <id>`` switch,
# delivered through the ordinary steer path. The filesystem CLI adapters
# (claude_code, codex); a remote (mewbo) session or a bare shell has none, so the
# trigger verbs raise `CapabilityUnavailable` for those. frozenset[str] (not
# AgentKind) — matched against the effective kind read back as plain str.
_CONTROL_KINDS: frozenset[str] = frozenset({"claude_code", "codex"})

# Agent kinds that can resume an EXISTING session by explicit id at launch:
# a filesystem CLI adapter that carries a resume handle (`claude --resume <id>`,
# `codex resume <id>`). DERIVED from the adapter layer's own `resumable` flag
# rather than hand-listed, so a future resumable adapter is picked up
# automatically instead of being silently missed. A remote (mewbo) session and a
# generic shell have no launch resume handle, so create() rejects
# `resume_session_id` for anything outside this set before any side effect.
# frozenset[str] (not AgentKind) — matched against `agent.kind`.
_RESUMABLE_KINDS: frozenset[str] = frozenset(a.kind for a in all_adapters() if a.resumable)


class _Unset:
    """Sentinel marker for "argument not specified" on update().

    A class (not a singleton-instance constant) so the type checker can
    distinguish "left alone" from "set to None / set to empty string" in
    the public ``update`` signature: ``title: str | _Unset = _UNSET``.
    None means "set to None" for description; the sentinel means "don't
    touch this field at all".
    """


_UNSET: _Unset = _Unset()


@dataclass(frozen=True, slots=True)
class WorkspaceEvent:
    """Lifecycle notification for clients. Pull-based render still preferred —
    treat events as wake-ups, then re-call `list()` for state."""

    kind: EventKindStr
    workspace_id: str
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _InitRun:
    """One init-script invocation, with BOTH failure shapes normalized to `rc`.

    `tmux.run_init_script` fails two ways: the script runs and exits non-zero,
    or the call *raises* — before the subprocess ever starts (mutually exclusive
    `inline`+`path`, a missing script file) or on timeout. create() branched on
    those separately and only the exit-code arm consulted
    `init_script.fail_fast`, so a raise was unconditionally fatal: a user who
    had explicitly set `fail_fast: false` still lost every workspace (worktree,
    branch, and record) to a config typo. Translating the raise into a non-zero
    `rc` here is what lets `_init_abort_detail` be the ONE place that decides
    whether an init failure is fatal.

    The default (`rc=0`) is the "never ran" case — an init the caller gated off,
    which `_init_outcome` reports as SKIPPED.
    """

    rc: int = 0
    summary: str = ""

    @classmethod
    def from_exit_code(cls, rc: int) -> _InitRun:
        return cls(rc=rc, summary=f"init script exited {rc}")

    @classmethod
    def from_exception(cls, exc: Exception, log_path: Path | None) -> _InitRun:
        """Translate a raise into a failed run, recording the cause in the init log.

        A raise happens *before* `run_init_script` writes its log, so without
        this the one artifact that explains the failure never exists and
        `_log_failure_detail` has nothing to tail — the peek rail showed a
        rolled-back workspace with no trail at all. Written in the same section
        shape `run_init_script` uses, with the cause in the stderr section (the
        tail everyone reads). Best-effort: an unwritable log must not mask the
        failure it describes.

        `rc` is a synthetic 1 — the script never ran, so there is no real exit
        status; the honest cause is `summary`, which every caller carries into
        its message.
        """
        summary = f"init script raised: {exc}"
        if log_path is not None:
            try:
                paths.ensure_dir(log_path.parent)
                log_path.write_text(
                    f"--- stdout ---\n--- stderr ---\n{summary}\n", encoding="utf-8"
                )
            except OSError as write_exc:
                logger.warning("could not write init log to {}: {}", log_path, write_exc)
        return cls(rc=1, summary=summary)


class WorkspaceManager:
    """Orchestrates workspace lifecycle for one repo + one merged config."""

    def __init__(
        self,
        *,
        repo_root: Path,
        cfg: GroveConfig,
        store: JsonWorkspaceStore,
        mewbo_client: MewboClient | None = None,
        native_steer: native.NativeSteerClient | None = None,
        launch_backend: LaunchBackend | None = None,
        ticket_registry: TicketProviderRegistry | None = None,
        devcontainer_cli: DevcontainerCli | None = None,
        preflight: HostPreflight | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._cfg = cfg
        self._store = store
        # Injected for tests (DI at the I/O boundary); production passes None
        # and the first mewbo launch builds one from cfg.mewbo.
        self._mewbo_client = mewbo_client
        # The paneless-steering delivery seam: a workspace with no tmux
        # pane routes send/answer/interrupt here instead of into a pane. Injected
        # for tests like `mewbo_client`; production builds the channel-backed
        # default lazily on first native steer.
        self._native_steer_client = native_steer
        # The swappable "start the assembled command in the workspace" seam.
        # Default is tmux; a container/headless runtime injects its own
        # backend without touching the AgentSpec/adapter/decoration composition.
        self._launch_backend = launch_backend or TmuxLaunchBackend()
        # The two container I/O boundaries, injected together for tests (a fake
        # CLI + a scripted host preflight is all a container test ever needs)
        # and built lazily in production so a host-only install never pays for
        # them. The container LAUNCH backend is NOT injectable alongside them:
        # which backend a workspace gets is decided by `state.runtime`, not by
        # construction — see `_backend_for`.
        self._devcontainer_cli = devcontainer_cli
        self._preflight = preflight
        self._runtime_resolver: RuntimeResolver | None = None
        self._container_provisioner: ContainerProvisioner | None = None
        self._container_backend: LaunchBackend | None = None
        self._container_liveness: ContainerLiveness | None = None
        # The agent-pane half of container liveness: "is the agent
        # alive inside the container", memoized on its own longer window
        # because it costs four times an inspect. Built lazily, so a host-only
        # install never constructs one.
        self._pane_liveness: ContainerPaneLiveness | None = None
        # Lazily built from cfg.tickets on first access; injectable for tests so
        # an httpx.MockTransport / fake env reaches every provider.
        self._ticket_registry = ticket_registry
        self._git = GitRepo(repo_root)
        self._subs: list[Callable[[WorkspaceEvent], None]] = []
        # Last reconciled status per workspace ID — drift events fire only
        # when the status actually changes, so a subscriber that refreshes
        # via list() can't trigger recursive offline_detected / orphaned_detected.
        self._last_reconciled_status: dict[str, WorkspaceStatus] = {}

    # ─── identity / accessors ──────────────────────────────────────────────

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def config(self) -> GroveConfig:
        return self._cfg

    @property
    def store(self) -> JsonWorkspaceStore:
        return self._store

    @property
    def provides_pane(self) -> bool:
        """Whether the launch backend hosts a tmux pane (False = headless).

        The one seam every tmux-only path consults: reconciliation skips the
        has-session/pane-activity derivation when False, the activity blend
        treats the workspace like a remote adapter (pane not authoritative), and
        pane-bound steering/snapshot raise ``CapabilityUnavailable``.
        """
        return self._launch_backend.provides_pane

    @property
    def ticket_providers(self) -> TicketProviderRegistry:
        """The repo's enabled ticket providers, built once from ``cfg.tickets``.

        The single surface both the engine (pure branch parse/format, used by
        ``create``/``attach_ticket``) and the daemon's ``/tickets`` routes
        (network list/get) share — no client re-implements parsing or linking.

        Caching it for this manager's whole life — which in a daemon is the whole
        process — is safe only because no provider captures a credential: each
        resolves its ``token_env`` per request through the registry's live env
        mapping, so a token that appears later (an init script's dotenv, a rotated
        secret) is picked up without rebuilding anything. ``repo_root`` goes with
        it so a repo-relative ``tickets.env_file`` resolves against the repo whose
        cascade produced this config.
        """
        if self._ticket_registry is None:
            self._ticket_registry = TicketProviderRegistry(
                self._cfg.tickets, repo_root=self._repo_root
            )
        return self._ticket_registry

    def subscribe(self, callback: Callable[[WorkspaceEvent], None]) -> Callable[[], None]:
        """Register a sync callback. Returns an unsubscribe handle."""
        self._subs.append(callback)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._subs.remove(callback)

        return _unsub

    # ─── lifecycle ─────────────────────────────────────────────────────────

    def list(self) -> list[WorkspaceState]:
        """Workspaces in this repo, with each persisted intent promoted to its
        currently-displayed status (ACTIVE / IDLE / OFFLINE / ORPHANED).

        Reconciliation calls live tmux + filesystem helpers per running
        workspace — bounded subprocess work, the same shape we already do for
        `has_session`. See `_reconcile_status` for the policy.
        """
        records = self._store.for_repo(self._repo_root)
        reconciled: list[WorkspaceState] = []
        for state in records:
            promoted = self._reconcile_status(state)
            reconciled.append(promoted)
            self._maybe_emit_status_drift(state, promoted)
        reconciled.sort(key=_list_sort_key)
        return reconciled

    def get(self, workspace_id: str) -> WorkspaceState:
        return self._store.get(workspace_id)

    def _apply_ticket_branch(
        self, resolved: ResolvedBranch, request: CreateWorkspaceRequest
    ) -> ResolvedBranch:
        """Rewrite an auto branch to carry the ticket's canonical key, if asked.

        When the caller names a ticket AND lets Grove generate the branch
        (``AutoBranch``), the name becomes ``{branch_prefix}{key}-{slug}`` so the
        tracker links PRs/commits. The provider must be enabled — raises
        ``TicketProviderNotConfigured`` here, before any side effect. A
        user-named / checkout / root plan is returned untouched; the ticket is
        still associated later via the branch re-parse.
        """
        if request.ticket is None or not isinstance(request.branch_plan, AutoBranch):
            return resolved
        stem = self.ticket_providers.format_branch_name(request.ticket, request.title)
        return _dc_replace(resolved, name=f"{self._cfg.worktree.branch_prefix}{stem}")

    def _project_subpath(self, project_cwd: Path | None) -> str:
        """Resolve a requested agent cwd to a POSIX subpath under the repo root.

        ``None`` → ``""`` (agent starts at the worktree root, the historical
        shape). Otherwise the path must be the repo root or a descendant of it;
        anything else is a loud ``GroveError`` raised before any side effect.
        Both sides are ``resolve()``d (the registry keys ``repo_root`` that way),
        so a symlinked or relative ``project_cwd`` still maps correctly.
        """
        if project_cwd is None:
            return ""
        resolved = project_cwd.expanduser().resolve()
        try:
            rel = resolved.relative_to(self._repo_root)
        except ValueError:
            raise GroveError(
                f"project cwd {resolved} is not within repo root {self._repo_root}"
            ) from None
        posix = rel.as_posix()
        return "" if posix == "." else posix

    def _run_init_script(
        self,
        *,
        state: WorkspaceState,
        worktree: Path,
        log_path: Path | None,
    ) -> _InitRun:
        """Run the configured init script; an init failure never raises from here.

        The one seam create/resume/respawn share, so the fail_fast policy has a
        single input shape: a raise is translated into a failed `_InitRun`
        (see `_InitRun.from_exception`) instead of propagating, leaving
        `_init_abort_detail` as the only site that decides whether the failure
        is fatal. Each caller still owns its own cleanup.

        Takes the whole `state` rather than an `extra_env` the caller composes.
        It was a shared seam that shared everything except the one thing
        it needed to: `create` passed the four `GROVE_*` variables inline and the
        other two verbs passed nothing, so a script reading `$GROVE_BRANCH`
        worked on create and silently saw an empty string on every
        `run_on_resume` resume. Deriving them here from
        `WorkspaceState.init_env` makes forgetting structurally impossible
        rather than a thing three call sites have to remember.
        """
        try:
            rc = tmux.run_init_script(
                self._cfg.init_script,
                worktree=worktree,
                repo_root=self._repo_root,
                extra_env=state.init_env,
                log_path=log_path,
            )
        except Exception as exc:
            logger.warning("init script raised: {}", exc)
            return _InitRun.from_exception(exc, log_path)
        return _InitRun.from_exit_code(rc)

    def _init_enabled(self, state: WorkspaceState, *, skip: bool = False) -> bool:
        """Does the init script run for THIS workspace — the gate all three verbs share.

        The fourth member of the init trio (`_run_init_script` / `_init_outcome`
        / `_init_abort_detail`): create/resume/respawn ask this instead of
        re-spelling the condition, so `enabled`, the per-create `skip_init`
        override, and the `applies_to` runtime scope can't drift between them.

        Reads the PERSISTED runtime, never the requested one: a workspace that
        asked for a container and fell back to the host IS a host workspace — it
        is running on the host, so a host-scoped script applies to it and a
        container-scoped one does not. False here means `InitStatus.SKIPPED`,
        the same outcome `enabled: false` has always produced.
        """
        cfg = self._cfg.init_script
        is_container = state.runtime is Runtime.CONTAINER
        return cfg.enabled and not skip and cfg.applies(is_container=is_container)

    def _init_abort_detail(self, init: _InitRun, *, log_path: Path, rollback: str) -> str | None:
        """THE fail_fast decision: the message to abort with, or None to continue.

        Both failure shapes reach here as `rc != 0` (see `_InitRun`), so
        `fail_fast: false` means "keep this workspace" for a raise exactly as it
        does for a non-zero exit — the asymmetry between them is what destroyed
        workspaces whose owner had explicitly opted out of that. Never a second
        copy of this branch at a call site; `rollback` only names what the caller
        is about to undo, and the caller owns that cleanup.

        Routed through `_log_failure_detail` like every other fail_fast init
        raise, so the abort message carries the kept log's path + tail.
        """
        if init.rc == 0:
            return None
        if not self._cfg.init_script.fail_fast:
            logger.warning("{} but fail_fast=False; continuing", init.summary)
            return None
        return _log_failure_detail(f"{init.summary}; fail_fast=True so {rollback}", log_path)

    # ─── runtime selection + container provisioning ─────────────────────────

    def _resolver(self) -> RuntimeResolver:
        """The host-vs-container decision tree, built once per manager."""
        if self._runtime_resolver is None:
            self._runtime_resolver = RuntimeResolver(
                self._cfg, cli=self._devcontainer_cli, preflight=self._preflight
            )
        return self._runtime_resolver

    def _provisioner(self) -> ContainerProvisioner:
        if self._container_provisioner is None:
            self._container_provisioner = ContainerProvisioner(
                self._cfg, repo_root=self._repo_root, cli=self._devcontainer_cli
            )
        return self._container_provisioner

    def _backend_for(self, state: WorkspaceState) -> LaunchBackend:
        """The launch backend this workspace's PERSISTED runtime selects.

        Selection reads ``state.runtime`` — never config — so ``cfg.container.enabled``
        cannot silently launch an existing workspace on the wrong backend: flipping
        the cascade default can't move an existing workspace, and an injected
        ``launch_backend`` still owns the host arm (headless is one of its
        implementations of the same seam).
        """
        if state.runtime is not Runtime.CONTAINER:
            return self._launch_backend
        if self._container_backend is None:
            self._container_backend = DevcontainerLaunchBackend(cli=self._devcontainer_cli)
        return self._container_backend

    def _provision_container(
        self, state: WorkspaceState, decision: RuntimeDecision, *, phase: str
    ) -> WorkspaceState:
        """Bring the workspace's container up; raise on failure (D5 arms 5/6).

        FATAL by contract, never a downgrade to host: an environment that exists
        but is broken must not silently launch the agent on the machine the
        workspace was created to be isolated from. The container the CLI managed
        to create is deliberately LEFT RUNNING for diagnosis — destroying it would
        remove the evidence at exactly the moment it is needed — and the raised
        message carries the provision log's path and tail — the log was written
        as the provision ran, so it exists even for a timeout. Callers own their
        own rollback around this.

        **The ONE site that decides fatality, across two failure shapes.** A
        provision fails either by raising (nothing came up, or the failure left
        no container to name) or by returning an identity whose ``provisioned``
        is False (``up`` failed but left a container running). Both end here, in
        one refusal — a policy wired to only one of the two shapes would silently
        not apply to the other.

        The returned shape is the one worth recording, so it is PERSISTED before
        the raise. Without that, a failed re-provision left the record still
        claiming the previous, successful container — so reconciliation read
        RUNNING and ``attach``/``steer`` were allowed into a container whose
        lifecycle hooks had just failed, the same condition as *no egress
        firewall*. Persisted, the workspace reconciles to OFFLINE (via
        ``ContainerState.UNPROVISIONED``), which refuses both verbs and offers
        ``respawn`` — the remedy that re-provisions. On ``create`` the save is
        undone by the caller's rollback, correctly: that record is going away
        entirely, and the raised message already names the container.
        """
        log_path = paths.provision_log_path(state.id)
        started = _utcnow()
        # Announce the provision BEFORE running it. The record has been visible
        # on every surface since the first line of `create`, and without this
        # the whole multi-minute build is indistinguishable from a dead
        # workspace — the record says RUNNING, the container does not exist yet,
        # and reconciliation reads OFFLINE. The log path is published here for
        # the same reason it is published at all: it is written line-by-line AS
        # the provision runs, so it is only useful to a waiting user WHILE the
        # provision is still going. Persisted rather than held in memory because
        # the reader is another request on another thread.
        in_flight = {
            "provision_status": ProvisionStatus.PROVISIONING,
            "provision_started_at": started.isoformat(),
            "provision_log_path": str(log_path),
            "provision_duration_ms": None,
        }
        state = _replace(state, **in_flight)
        self._record_provision_started(state, in_flight)
        self._emit("provisioning", state.id, {"phase": phase, "log_path": str(log_path)})
        self._ensure_control_files()
        try:
            container = self._provisioner().provision(
                workspace_id=state.id,
                worktree=Path(state.worktree_path),
                decision=decision,
                log_path=log_path,
                kind=self.effective_kind(state),
                # The repo's own remotes are an egress destination the allowlist
                # cannot derive from config: an agent must be able to fetch and
                # push its own repository, and a self-hosted forge is nobody's
                # to hard-code. Read from the REPO root, not the worktree — a
                # freshly-added worktree shares the repo's remotes either way,
                # and the root is readable even before the worktree exists.
                remote_urls=self._git.remote_urls(),
                base=state.container,
                # Resolved here rather than inside the provisioner so the one
                # site that can fail loudly for a missing file or a failing
                # command is the same transaction that rolls the workspace back.
                container_env=self._container_env(state),
            )
        except ContainerError as exc:
            self._record_provision_failure(state, exc, started=started, log_path=log_path)
            container_id = getattr(exc, "container_id", None)
            docker_bin = self._cfg.container.docker_bin
            kept = (
                f" container {container_id} was KEPT for diagnosis;"
                f" remove it with `{docker_bin} rm -f {container_id}` when done"
                if container_id
                else ""
            )
            self._emit(
                "error",
                state.id,
                {
                    "phase": phase,
                    "error": str(exc),
                    "container_id": container_id or "",
                    "log_path": str(log_path),
                },
            )
            raise GroveError(
                _log_failure_detail(f"container provisioning failed: {exc};{kept}", log_path)
            ) from exc
        return _replace(
            state,
            container=container,
            **_provision_outcome(True, started, log_path),
        )

    def _record_provision_started(self, state: WorkspaceState, fields: dict[str, Any]) -> None:
        """Publish the in-flight provision onto the STORED record.

        Grafted onto the record as persisted rather than saving *state*, for
        the reason ``_record_provision_failure`` documents: ``respawn`` and
        ``resume`` arrive holding a RECONCILED status (OFFLINE / PAUSED) that
        the store refuses by design, so writing the in-hand state here would
        fail every re-provision. Only `create` holds a raw persisted intent.

        Best-effort: this write exists so a waiting user can see that something
        is happening, and losing it must never cost them the provision itself.
        """
        try:
            self._store.save(_replace(self._store.get(state.id), **fields))
        except (WorkspaceNotFound, GroveError, OSError) as exc:
            logger.warning("could not record provision start for {}: {}", state.id, exc)

    def _record_provision_failure(
        self,
        state: WorkspaceState,
        exc: ContainerError,
        *,
        started: datetime,
        log_path: Path,
    ) -> None:
        """Persist the unprovisioned identity a failed provision left behind.

        The write that makes ``ContainerState.UNPROVISIONED`` reachable, and the
        reason it matters is a safety hole rather than tidiness: without it a
        failed RE-provision left the record still naming the previous,
        successful container, so the workspace reconciled to ACTIVE and
        ``attach``/``steer`` were allowed into a container whose lifecycle hooks
        had just failed — the same condition as *no egress firewall*.
        Recorded, it reconciles to OFFLINE, which refuses both verbs and points
        at ``respawn``, the verb that re-provisions.

        A failure with no identity to record (:class:`ProvisionFailed` is the
        only shape that carries one) leaves the record alone: overwriting a
        working container with nothing would lose the handle a teardown needs.

        Never saves *state* itself: a re-provision arrives holding a RECONCILED
        status (``respawn`` gets OFFLINE) and the store refuses a computed
        status by design, so the runtime fields are grafted onto the record as
        persisted — the same shape ``respawn`` uses to carry its own across. On
        ``create`` the caller's rollback then drops the whole record, correctly:
        it is going away, and the raised message already names the container.

        Best-effort throughout: losing this write must never mask the failure
        about to be raised.
        """
        container = getattr(exc, "container", None)
        if container is None:
            return
        try:
            stored = self._store.get(state.id)
        except WorkspaceNotFound:  # pragma: no cover - create saves before provisioning
            return
        self._store.save(
            _replace(
                stored,
                container=container,
                **_provision_outcome(container.provisioned, started, log_path),
            )
        )

    def _share_plan(self, state: WorkspaceState, agent: AgentSpec) -> AgentSharePlan | None:
        """The agent-config share for a CONTAINER workspace; ``None`` on the host.

        Gated on the persisted runtime rather than on ``container.enabled``, for
        the same reason backend selection is: config says what the NEXT workspace
        gets, `state.runtime` says what THIS one is.

        The launch spec carries the whole plan, not just its ``env``, because the
        backend answers ``transcript_context`` off the same mount table —
        two derivations of one fact is exactly how the env could come to point
        somewhere the mount does not. The plan's own ``env_unset`` is deliberately
        NOT applied at launch: it exists for a host pane that would inherit the
        daemon's environment, and this env crosses into a fresh container
        via ``--remote-env``, where nothing is inherited to clear.
        """
        if state.runtime is not Runtime.CONTAINER:
            return None
        return self._provisioner().share_plan(state.id, kind=agent.kind)

    def _container_env(self, state: WorkspaceState) -> EnvSource:
        """The ``container.env_file`` / ``env_command`` variables for a container.

        Gated on the PERSISTED runtime for the same reason ``_share_plan`` and
        backend selection are: config says what the next workspace gets,
        ``state.runtime`` says what this one is. A host workspace's agent already
        inherits the user's shell, so injecting there would be solving a problem
        it does not have.

        Unlike ``_share_plan`` this is RESOLVED, not derived — an ``env_command``
        runs a host process — and it is deliberately asked TWICE per workspace
        start: once to provision (the values reach the project's lifecycle hooks
        via ``--secrets-file``) and once to launch (they reach the agent via the
        launch env). The alternative was threading the resolved mapping through
        ``_provision_container``, ``_launch`` and ``_launch_spec`` for all three
        verbs to carry a value only the container arm ever reads. So the
        documented contract is that the command must be idempotent and cheap.

        Caching it on the manager would remove the second run and is the wrong
        trade: a daemon's ``WorkspaceManager`` is cached per repo for the
        process's lifetime, so the cache would hold resolved secrets in memory
        for days and serve a stale value long after the store rotated it. The
        whole point of ``env_command`` is that the values are not kept anywhere.

        The resolver raises its own ``EnvSourceError`` (it serves several config
        sections now, ``tickets`` among them), re-raised here as the
        ``ContainerError`` the create/resume/respawn fork already rolls back on —
        one narrowing at the consumer's own boundary, rather than a shared seam
        pretending every failure is a container failure.
        """
        if state.runtime is not Runtime.CONTAINER:
            return EnvSource({})
        try:
            return EnvSource.resolve(self._cfg.container, repo_root=self._repo_root)
        except EnvSourceError as exc:
            raise ContainerError(str(exc)) from exc

    def _is_promotable(self, state: WorkspaceState) -> bool:
        """Whether respawning ``state`` right now would actually promote it.

        The side-effecting half of :func:`ensure_can_respawn`'s ``promotable``:
        a workspace that fell back AND whose runtime has since become available.
        Both halves are required. A fallback reason alone is not enough — with
        containers on by default, every workspace on a machine without a
        container runtime carries one, so accepting the reason by itself would
        let respawn tear down a live session to "promote" it into a runtime
        that is still absent. Bounded (the resolver's probes are the same ones
        `grove doctor` runs) and best-effort: if the probe itself fails, the
        answer is simply "no", and the ordinary OFFLINE gate applies.
        """
        if not state.runtime_fallback_reason:
            return False
        try:
            return self._resolver().resolve(requested=None, repo_root=self._repo_root).is_container
        except GroveError:
            return False

    def _ensure_runtime(self, state: WorkspaceState, *, promote: bool) -> WorkspaceState:
        """Make ``state``'s persisted runtime ready to launch into.

        **Never re-resolves host-vs-container from config.** ``resume``/
        ``respawn`` read the persisted ``runtime``, because re-resolving would
        mean flipping ``container.enabled`` silently moved every paused
        workspace into a container on its next resume — a change of isolation
        the user never asked for on work already in flight.

        The single exception is the promotion ``respawn`` exists to offer
        (``promote=True``): a workspace carrying a ``runtime_fallback_reason``
        wanted a container and got the host only because the runtime was
        unavailable, so re-evaluating the tree is exactly what "fix docker, then
        respawn" means. A workspace that explicitly chose ``--runtime host``
        carries no reason and is therefore never auto-upgraded — that was a
        decision, not a defeat.
        """
        if promote and state.runtime_fallback_reason:
            decision = self._resolver().resolve(requested=None, repo_root=self._repo_root)
            if not decision.is_container:
                # Still unavailable: refresh the reason (it may have changed
                # from "CLI missing" to "engine down") and stay on the host.
                logger.info(
                    "respawn kept workspace {} on the host: {}",
                    state.id,
                    decision.fallback_reason,
                )
                return _replace(
                    state,
                    runtime_fallback_reason=decision.fallback_reason
                    or state.runtime_fallback_reason,
                )
            promoted = self._provision_container(
                _replace(
                    state,
                    runtime=Runtime.CONTAINER,
                    # A promotion is this workspace's first container, so which
                    # config it was built from is decided HERE, not at its
                    # original host create.
                    runtime_default_config=decision.config_path is not None,
                ),
                decision,
                phase="respawn.provision",
            )
            logger.info("workspace {} promoted host → container on respawn", state.id)
            return _replace(promoted, runtime_fallback_reason=None)
        if state.runtime is not Runtime.CONTAINER:
            return state
        # An existing container workspace: `up` again (it is idempotent and
        # re-attaches by id-label) so a relaunch after a reboot, a pause, or a
        # vanished session has something to exec into.
        return self._provision_container(
            state, self._resolver().container_decision(self._repo_root), phase="provision"
        )

    def create(self, request: CreateWorkspaceRequest) -> WorkspaceState:  # noqa: PLR0912, PLR0915
        """Spin up a fresh workspace from a validated client request.

        Validation order (no side effects until all pass):

        1. The requested agent must exist in the merged config.
        2. The resolved ``BranchPlan`` must agree with live git state —
           per-mode rules in ``_validate_branch_plan``: a NEW name must
           not collide with an existing branch and the base ref must
           exist; a CHECKOUT name must exist locally and not already be
           checked out at another worktree.
        3. Worktree → init script → tmux session, in that order, with
           ``_rollback_create`` cleaning up any partial state on
           failure.

        Branch provenance (``GROVE_CREATED`` vs ``USER_ATTACHED``) is
        derived from the resolved plan and persisted on the state, so
        ``kill()`` later knows whether the branch is safe to delete.
        """
        agent = self._cfg.find_agent(request.agent_name)
        if agent is None:
            raise GroveError(f"unknown agent: {request.agent_name}")
        # Resume-into-workspace is gated + validated HERE, before any side
        # effect, so a bad request fails clean with no rollback work (mint runs
        # post-worktree, too late). Three checks, in order:
        #   1. the kind must carry a launch resume handle (claude_code/codex);
        #   2. the ref (id OR unique prefix) must resolve in this project —
        #      an unknown/ambiguous ref used to run ALL side effects and then
        #      strand a fully-provisioned workspace pinned to a bogus id;
        #   3. the resolved session's adapter kind must equal the agent's
        #      (kind parity) — a cross-kind pin is a permanent dead pointer.
        # The RESOLVED full id (not the raw prefix) is what gets adopted.
        resume_session_id = request.resume_session_id
        if resume_session_id is not None:
            if agent.kind not in _RESUMABLE_KINDS:
                raise ResumeNotSupported(
                    f"agent kind {agent.kind or 'generic'!r} cannot resume a session by id; "
                    "resume is supported only for claude_code and codex agents"
                )
            try:
                resume_listing = self._session_explorer().resolve(resume_session_id)
            except GroveError as exc:
                raise AgentSessionNotFound(str(exc)) from exc
            if resume_listing.summary.adapter_kind != agent.kind:
                raise AgentSessionNotFound(
                    f"session {resume_listing.summary.session_id} is a "
                    f"{resume_listing.summary.adapter_kind} session, but agent "
                    f"{request.agent_name!r} is {agent.kind} whose adapter cannot read a "
                    f"{resume_listing.summary.adapter_kind} transcript"
                )
            resume_session_id = resume_listing.summary.session_id

        # The runtime decision runs BEFORE any side effect (D5): arm 2 refuses
        # here with no rollback work, and arm 4's fallback is decided while the
        # worktree still doesn't exist. It is resolved from config exactly ONCE
        # in a workspace's life — from here on the persisted value is the answer.
        decision = self._resolver().resolve(requested=request.runtime, repo_root=self._repo_root)
        if decision.notice:
            logger.info("{}", decision.notice)

        ts = WorkspaceIdentity.timestamp()
        resolved = request.branch_plan.resolve(self._cfg, request.title, ts)
        resolved = self._apply_ticket_branch(resolved, request)
        self._validate_branch_plan(resolved)

        session = WorkspaceIdentity.session_name(self._cfg, request.title, ts)
        is_root = resolved.placement is Placement.ROOT
        # Root placement runs in the repo root on the live checkout: the worktree
        # IS repo_root and the branch is whatever HEAD points to (a detached HEAD
        # records "HEAD"). Worktree placement keeps the historical derivation.
        if is_root:
            worktree = self._repo_root
            branch = self._git.current_branch() or "HEAD"
        else:
            worktree = WorkspaceIdentity.worktree_path(
                self._cfg, self._repo_root, request.title, ts
            )
            branch = resolved.name
        # Agent cwd vs worktree placement: the worktree (above) and branch
        # always anchor at the repo root; `project_cwd` only moves where the
        # AGENT session starts, into a nested subdir of that worktree. Validated
        # against the repo root before any side effect — an out-of-repo cwd is a
        # loud error here, leaving no rollback work.
        subpath = self._project_subpath(request.project_cwd)
        agent_cwd = worktree / subpath if subpath else worktree
        now = _utcnow()
        # `base_branch` on the persisted record drives the peek's
        # ahead/behind/diff math. For NEW plans it's the explicit base;
        # for TrackRemote it's the upstream we'll set; for CHECKOUT and root
        # there is no base, so we fall back to "HEAD" — peek tolerates
        # a missing-base (returns zeros) when the branch and base agree.
        base_for_peek = resolved.base_ref or resolved.tracks or "HEAD"
        # Description normalizes empty string → None so the wire and disk
        # values agree on a single representation of "no description".
        description = (request.description or "").strip() or None
        # Branch name is the source of truth for ticket association: re-parse the
        # FINAL branch (ticket-aware auto, user-named, or an adopted checkout)
        # through every enabled provider. >1 match → each ref ambiguous. Pure, no
        # network — enrichment is the daemon's on-demand job.
        ticket_refs = self.ticket_providers.parse_workspace_refs(branch)
        state = WorkspaceState(
            id=WorkspaceIdentity.new_id(),
            title=request.title,
            repo_root=str(self._repo_root),
            branch=branch,
            base_branch=base_for_peek,
            worktree_path=str(worktree),
            tmux_session=session,
            agent_name=request.agent_name,
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
            description=description,
            branch_provenance=resolved.provenance,
            placement=resolved.placement,
            project_subpath=subpath,
            agent_session_id=None,  # minted after the worktree exists, below
            agent_kind=agent.kind,
            ticket_refs=ticket_refs,
            runtime=decision.runtime,
            runtime_fallback_reason=decision.fallback_reason,
            # Resolved from the cascade exactly like `runtime` and persisted for
            # the same reason: it is applied at every launch, so the answer has
            # to be the one this workspace was created under.
            brief=self._cfg.brief.enabled if request.brief is None else request.brief,
            # Arm 3, recorded as a create-time fact: the resolver hands back a
            # `config_path` only when it substituted Grove's packaged default
            # for a repo that had no `.devcontainer/`. Persisted rather than
            # re-derived, because a repo that commits one LATER must not
            # retroactively change what this workspace was built from.
            runtime_default_config=decision.is_container and decision.config_path is not None,
            # SKIPPED means "nothing to provision" (a host workspace), the same
            # way the init trio reports a script that never ran — not a failure.
            provision_status=None if decision.is_container else ProvisionStatus.SKIPPED,
        )
        # Persist before side effects so a crash leaves a recoverable record.
        self._store.save(state)

        # Root placement creates no worktree and no branch — it adopts the live
        # checkout. Only worktree placement issues `git worktree add`.
        if not is_root:
            try:
                self._add_worktree(resolved, worktree)
            except Exception as exc:
                self._record_error(state, f"worktree_add failed: {exc}")
                self._emit("error", state.id, {"phase": "worktree_add", "error": str(exc)})
                # The full rollback, not a bare `store.delete`: `git
                # worktree add -b` creates the REF before it validates the path,
                # and `_add_worktree` sets an upstream after the add returns, so
                # a failure here routinely leaves Grove's own branch — and
                # sometimes the worktree — behind with the record gone. The next
                # create with the same name then fails `BranchConflict`, blaming
                # the user for a branch Grove abandoned. Safe to unwind
                # unconditionally because rollback deletes only a branch
                # `grove_owns_branch` vouches for.
                self._rollback_create(state)
                raise GroveError(f"failed to create worktree: {exc}") from exc

        # The agent's phase file is Grove's plumbing living inside the user's
        # tree, exactly like the container provisioner's generated artifacts —
        # and the exclusion is a CORRECTNESS requirement, not tidiness:
        # `git worktree remove` refuses while untracked files exist, so the
        # first phase an agent ever reports would otherwise break `pause` and
        # `kill` for the rest of the workspace's life. Verified against real
        # git: removal succeeds with an EXCLUDED phase file present and is
        # refused with an unexcluded one.
        #
        # Unconditional, root placement included: there the reason is the other
        # one `ensure_excluded` documents — a root workspace writes into the
        # user's live checkout, where an untracked artifact of ours shows up as
        # their own uncommitted work. Idempotent and best-effort by contract,
        # and it targets the shared common dir, so one call per create is free
        # after the first. BOTH shapes: the per-agent directory every launch
        # now names, and the legacy single file a workspace created before the
        # per-agent layout landed may still be carrying.
        self._git.ensure_excluded(*PhaseFile.EXCLUDES)

        # `skip_init` is a per-create override of `init_script.enabled`; either
        # one being off — or an `applies_to` that doesn't cover this workspace's
        # runtime — means the script never runs and the outcome is SKIPPED.
        # Gating the call here (rather than relying on run_init_script's internal
        # enabled-check) is what lets a single create opt out without touching
        # config — and keeps the risky "init in the real repo root" path off by
        # default for root workspaces, which auto-check skip in the UI. `state`
        # already carries the resolved runtime (decided above, before any side
        # effect), so the gate sees the EFFECTIVE runtime including a fallback.
        init_enabled = self._init_enabled(state, skip=request.skip_init)
        init_log = paths.init_log_path(state.id)
        init_started = _utcnow()
        init = _InitRun()
        if init_enabled:
            init = self._run_init_script(state=state, worktree=worktree, log_path=init_log)

        state = _replace(
            state,
            **_init_outcome(init_enabled, init.rc, init_started, init_log),
        )

        # One decision, both failure shapes: a script that exited non-zero and a
        # call that raised are the same "init failed" to fail_fast, so an
        # explicit `fail_fast: false` never destroys the workspace either way.
        abort = self._init_abort_detail(
            init, log_path=init_log, rollback="workspace was rolled back"
        )
        if abort is not None:
            self._rollback_create(state)
            self._emit(
                "error",
                state.id,
                {
                    "phase": "init_script",
                    "exit_code": str(init.rc),
                    "log_path": str(init_log),
                    "error": init.summary,
                },
            )
            raise GroveError(abort)

        # The container comes up AFTER the init script (which preps the HOST
        # worktree) and before the launch that execs into it. Failure is fatal
        # and transactional exactly like a fail_fast init — but the container
        # itself is kept, so the rolled-back worktree still has a diagnosable
        # environment attached to the log.
        if decision.is_container:
            try:
                state = self._provision_container(state, decision, phase="provision")
            except GroveError:
                self._rollback_create(state)
                raise
            self._store.save(state)

        # Deterministic session correlation: mint the agent session id
        # AFTER the worktree exists — claude_code ids carry no ordering
        # constraint, but a mewbo create anchors the remote session to the
        # worktree cwd, which the API validates is a real directory. Failure is
        # loud and transactional, exactly like a fail_fast init.
        try:
            agent_session_id = self._mint_agent_session_id(
                agent,
                worktree=agent_cwd,
                title=request.title,
                model=request.model,
                resume_session_id=resume_session_id,
            )
        except MewboError as exc:
            self._rollback_create(state)
            self._emit("error", state.id, {"phase": "agent_session", "error": str(exc)})
            raise
        if agent_session_id is not None:
            state = _replace(state, agent_session_id=agent_session_id)
            self._store.save(state)
        # `initial_prompt` is create-only — never threaded into resume/respawn.
        # For claude_code it rides the launch argv (race-free); for mewbo it is
        # delivered after the workspace is persisted (below), so it isn't passed
        # here (mewbo's decoration is empty anyway). `resume` flips the
        # session-id flag to the tool's resume form (`--resume` / `resume <uuid>`).
        # One briefed prompt for BOTH delivery sites: composing it twice is how
        # the launch argv and the remote dispatch come to disagree.
        initial_prompt = self._brief_prompt(state, agent, request.initial_prompt)
        launch_decoration = self._compose_launch(
            agent,
            agent_session_id,
            state=state,
            initial_prompt=initial_prompt,
            model=request.model,
            resume=resume_session_id is not None,
        )

        try:
            # `state.tmux_session`/`state.agent_cwd` are the `session`/`agent_cwd`
            # locals above, read back off the record so all three launch verbs
            # hand `_launch` the identical shape.
            transcript_context = self._launch(state, agent, launch_decoration)
        except Exception as exc:
            self._rollback_create(state)
            self._emit("error", state.id, {"phase": "tmux", "error": str(exc)})
            raise GroveError(f"failed to set up tmux session: {exc}") from exc

        # Recorded on the SAME save that already closed create — a launch that
        # raised rolled the whole record away above, so there is no window where
        # a half-written context can survive.
        state = _replace(_touch(state), transcript_context=transcript_context)
        self._store.save(state)
        self._emit(
            "created",
            state.id,
            {
                "title": request.title,
                "agent": request.agent_name,
                # The runtime facts ride the create event so a subscriber can
                # surface the fallback (a degradation the user must see) or the
                # default-config notice (informational) without re-reading the
                # record. Empty string, not a missing key — the detail map is
                # `dict[str, str]` and consumers treat blank as "nothing to say".
                "runtime": state.runtime.value,
                "runtime_fallback_reason": state.runtime_fallback_reason or "",
                "runtime_notice": decision.notice or "",
            },
        )
        self._deliver_remote_initial_prompt(state, agent, initial_prompt)
        return state

    def _deliver_remote_initial_prompt(
        self, state: WorkspaceState, agent: AgentSpec, initial_prompt: str | None
    ) -> None:
        """Re-engage a freshly-created remote (mewbo) session with the user's first
        task, where the prompt can't ride a launch argv.

        A mewbo session's launch decoration is empty — the pane runs a bare shell
        and the session lives server-side — so its initial prompt is delivered
        through the SAME remote dispatch send_message uses; `/message` has no boot
        race. Best-effort by design: this runs AFTER the workspace is persisted, so
        a delivery failure must NOT roll back a successfully-created workspace — it
        is logged, and the user steers manually. A no-op for claude_code (the prompt
        already rode the launch) and for the empty-prompt case.
        """
        if not initial_prompt or agent.kind not in _REMOTE_STEERED_KINDS:
            return
        try:
            self._steer_remote(state, "message", initial_prompt)
        except (MewboError, GroveError) as exc:
            logger.warning(
                "initial prompt delivery to remote session for {} failed; "
                "workspace is created, steer manually: {}",
                state.id,
                exc,
            )

    def _add_worktree(self, resolved: ResolvedBranch, worktree: Path) -> None:
        """Issue the `git worktree add` for a worktree-placement create.

        NEW mode creates the branch with `-b` off its base (and sets the
        upstream afterward for a tracking plan); CHECKOUT mode attaches an
        existing branch. Root placement never reaches here — `create()` gates
        the call on placement. Setting upstream after `-b` (rather than
        `--track`) mirrors the most widely-deployed git's behavior, which
        varies across versions and config defaults; a stale remote ref would
        already have failed validation upstream.
        """
        if resolved.mode == BranchMode.NEW:
            self._git.worktree_add(
                worktree,
                new_branch=resolved.name,
                base=resolved.base_ref or "HEAD",
            )
            if resolved.tracks:
                self._git.branch_set_upstream(resolved.name, resolved.tracks)
        else:
            self._git.worktree_add(worktree, existing_branch=resolved.name)

    def _validate_branch_plan(self, resolved: ResolvedBranch) -> None:
        """Reconcile a resolved plan against live git state.

        Raises a typed ``BranchError`` subclass on conflict, returns
        silently when the plan is good. Always called *before* any
        worktree side effect, so a failure here leaves no rollback work.

        Per-mode rules:

        - **NEW** — branch name must not collide with any existing
          branch (local or remote, since a colliding remote would
          create an immediate ambiguity); base ref must resolve to a
          commit (branches, tags, and SHAs all accepted via ``rev-parse``).
        - **CHECKOUT** — branch name must exist locally; must not
          currently be checked out at another worktree (git itself
          would refuse the worktree add, but the typed error gives
          clients a structured way to surface the conflict).
        - **ROOT** — nothing to validate: Grove creates no branch and no
          worktree, it adopts whatever HEAD already points to. (resolved.name
          is the empty sentinel here, which the per-mode rules below would
          wrongly reject — so root short-circuits.)
        """
        if resolved.placement is Placement.ROOT:
            return
        if resolved.mode == BranchMode.NEW:
            if self._git.find_branch(resolved.name) is not None:
                raise BranchConflict(
                    f"branch {resolved.name!r} already exists; "
                    "use Existing to check it out, or pick a different name"
                )
            if resolved.base_ref and self._git.rev_parse(resolved.base_ref) is None:
                raise BranchNotFound(f"base ref {resolved.base_ref!r} does not exist")
        else:  # CHECKOUT
            info = self._git.find_branch(resolved.name)
            if info is None or info.kind != "local":
                raise BranchNotFound(
                    f"local branch {resolved.name!r} does not exist; use Auto or New to create one"
                )
            location = self._git.checkout_location(resolved.name)
            if location is not None:
                raise BranchAlreadyCheckedOut(name=resolved.name, worktree=location)

    # ─── branch read proxies (clients populate dropdowns from these) ───────

    def list_local_branches(self) -> tuple[BranchInfo, ...]:
        """Every local branch in the repo, with HEAD marker, upstream, and checkout site.

        Tuple, not list, because the return is a point-in-time snapshot —
        mutating it after the call would mislead the caller about live
        repo state. A fresh call rebuilds. Same shape for the remote
        helper below.
        """
        return tuple(self._git.list_local_branches())

    def list_remote_branches(self) -> tuple[BranchInfo, ...]:
        """Every remote-tracking branch (excluding ``origin/HEAD`` symref)."""
        return tuple(self._git.list_remote_branches())

    def current_branch(self) -> str | None:
        """The local branch HEAD points to, or ``None`` if HEAD is detached."""
        return self._git.current_branch()

    def default_branch(self) -> str:
        """Best-effort default branch (``origin/HEAD`` → ``init.defaultBranch`` → ``main``)."""
        return self._git.default_branch()

    # ─── container lifecycle ─────────────────────────────────────────────────

    def _container(self, state: WorkspaceState) -> ContainerLifecycle | None:
        """The container verbs for *state*, or ``None`` for a host-mode workspace.

        ``state.container is None`` is the single host/container branch every
        lifecycle method makes — everything past it (ordering, argv assembly,
        the teardown invariant) lives in ``container_runtime``, so the manager
        never names a container itself.

        The agent's in-container session NAME is the one thing the manager has
        to supply, because it is cascaded config: the graceful shutdown
        signals that session and no other, and a name baked into
        ``container_runtime`` would be exactly the policy-in-code this tree
        forbids. It is the same value the launch composes its ``new-session -A``
        from, so the shutdown addresses what the launch created.
        """
        if state.container is None:
            return None
        return ContainerLifecycle(
            state.container,
            docker_bin=self._cfg.container.docker_bin,
            agent_session=self._cfg.container.tmux.session,
        )

    def _container_state(self, state: WorkspaceState) -> ContainerState | None:
        """The live container substate; ``None`` on the host or if unreadable.

        The READ half of the host/container branch `_container` owns — kept
        beside it and gated the same way — but memoized, because its one caller
        is `_reconcile_status` and that runs per workspace per poll (see
        :class:`~grove.core.container_runtime.ContainerLiveness` for the cost
        argument). The `runtime` test is what keeps a host workspace's
        reconciliation byte-identical: it never constructs the reader, so it
        never reaches for docker at all.
        """
        if state.runtime is not Runtime.CONTAINER or state.container is None:
            return None
        if self._container_liveness is None:
            self._container_liveness = ContainerLiveness(docker_bin=self._cfg.container.docker_bin)
        return self._container_liveness.state_of(state.container)

    def pause(self, workspace_id: str, *, force: bool = False) -> WorkspaceState:
        state = self._store.get(workspace_id)
        # Asked BEFORE any side effect, which is the entire fix: git's own
        # refusal fires at step three, by which point the session is dead and the
        # container stopped, and neither is undone. `force` means the user has
        # already said discard, so the question is not worth a subprocess.
        clean = True if force else self._git.is_clean(Path(state.worktree_path))
        ensure_can_pause(state, clean=clean)
        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("kill_session during pause failed: {}", exc)
        # ── container: stop AFTER the session (the pane's exec dies with
        # it) and BEFORE the worktree goes, since the worktree is the bind mount.
        # Killing that session is NOT what stops the agent — the host
        # pane is only a viewport onto a tmux inside the container, so `pause()`
        # signals the agent in its own namespace and waits for it first.
        # Best-effort: a container that will not stop must not block reclaiming
        # the worktree, which is what the user asked for.
        # Nothing to persist afterwards: a stop leaves the IDENTITY untouched
        # (the id is exactly what has to survive so resume can reuse it), and
        # the container's new substate is a live read, never a stored field.
        container = self._container(state)
        if container is not None:
            try:
                container.pause()
            except Exception as exc:
                logger.warning("container stop during pause failed: {}", exc)
        try:
            self._git.worktree_remove(Path(state.worktree_path), force=force)
        except Exception as exc:
            self._emit("error", state.id, {"phase": "pause.worktree_remove", "error": str(exc)})
            raise GroveError(
                f"could not remove worktree (use force=True to discard changes): {exc}"
            ) from exc

        new_state = _replace(
            state,
            status=WorkspaceStatus.PAUSED,
            paused_at=_utcnow(),
            updated_at=_utcnow(),
            error_detail=None,
        )
        self._store.save(new_state)
        self._emit("paused", state.id)
        return new_state

    def resume(self, workspace_id: str) -> WorkspaceState:
        state = self._store.get(workspace_id)
        ensure_can_resume(state)
        agent = self._cfg.find_agent(state.agent_name)
        if agent is None:
            raise GroveError(
                f"agent {state.agent_name!r} no longer present in config; "
                "edit your config or kill this workspace"
            )

        # Resume *continues* the same agent session, so reuse the persisted id —
        # Claude Code re-opens that transcript. respawn() takes the other branch
        # (a fresh id for a brand-new session); this is the one place that choice
        # is made, so the two verbs can't drift.
        #
        # One rule: continue what EXISTS, mint what doesn't. Flip to the
        # tool's resume flag only when the pinned session has ALREADY materialized
        # (a transcript is on disk) and the kind can resume by id — else keep the
        # mint form, so a resume-created codex workspace doesn't relaunch a bare
        # `codex` (new thread, stale pin) and a never-materialized minted claude id
        # keeps `--session-id` so it can still mint fresh. Materialization is
        # checked BEFORE the worktree is recreated; transcripts outlive worktrees
        # (they live under the encoded-cwd projects folder), so the check is valid.
        resume = agent.kind in _RESUMABLE_KINDS and self._pinned_session_materialized(agent, state)
        launch_decoration = self._compose_launch(
            agent, state.agent_session_id, state=state, resume=resume
        )

        worktree = Path(state.worktree_path)
        try:
            self._git.worktree_add(worktree, existing_branch=state.branch)
        except Exception as exc:
            self._emit("error", state.id, {"phase": "resume.worktree_add", "error": str(exc)})
            raise GroveError(f"could not recreate worktree: {exc}") from exc

        init_changes: dict[str, object] = {}
        if self._cfg.init_script.run_on_resume:
            init_enabled = self._init_enabled(state)
            init_log = paths.init_log_path(state.id)
            init_started = _utcnow()
            init = _InitRun()
            if init_enabled:
                init = self._run_init_script(state=state, worktree=worktree, log_path=init_log)
            # Same single decision create makes — a raise from the init call is
            # no longer fatal on its own, so `fail_fast: false` resumes into a
            # workspace whose init failed instead of dropping the worktree.
            abort = self._init_abort_detail(
                init, log_path=init_log, rollback="the recreated worktree was removed on resume"
            )
            if abort is not None:
                self._git.worktree_remove(worktree, force=True)
                raise GroveError(abort)
            init_changes = dict(_init_outcome(init_enabled, init.rc, init_started, init_log))

        # ── container: the worktree (the bind mount) exists again, so the
        # container comes back BEFORE the agent is launched into it, and a
        # failure here is fatal — launching an agent into a container that is
        # not there is not a degraded resume, it is a broken one — unwinding the
        # worktree exactly like a launch failure. The persisted runtime is READ,
        # never re-resolved: flipping the cascade default must not move a paused
        # workspace into a container.
        try:
            state = self._ensure_runtime(state, promote=False)
        except GroveError:
            self._git.worktree_remove(worktree, force=True)
            raise

        try:
            transcript_context = self._launch(state, agent, launch_decoration)
        except Exception as exc:
            self._git.worktree_remove(worktree, force=True)
            self._emit("error", state.id, {"phase": "resume.tmux", "error": str(exc)})
            raise GroveError(f"could not start tmux session: {exc}") from exc

        new_state = _replace(
            state,  # already carries this launch's container + provision trio
            status=WorkspaceStatus.RUNNING,
            paused_at=None,
            updated_at=_utcnow(),
            error_detail=None,
            transcript_context=transcript_context,
            **init_changes,
        )
        self._store.save(new_state)
        self._emit("resumed", state.id)
        return new_state

    def kill(self, workspace_id: str, *, delete_branch: bool | None = None) -> None:
        """Tear down the workspace's tmux session (always) and worktree.

        For worktree placement the worktree is always removed; the local
        branch is deleted only when ``delete_branch`` is True. Default
        (``None``) resolves from ``state.branch_provenance``:
        ``GROVE_CREATED`` → True (Grove made the branch; safe to drop),
        ``USER_ATTACHED`` → False (the user's pre-existing branch stays).

        For **root** placement the worktree IS the repo root and the branch is
        the live checkout, so kill never removes the directory and never deletes
        the branch — even if the caller passes ``delete_branch=True``. It only
        stops the session and forgets the record.

        **Remote branches are never touched.** Period — there is no flag
        to opt into remote deletion. That's ``git push --delete``
        territory and stays in the user's shell, with their own
        credentials. Best-effort on each step; a failure on one stage
        does not prevent later stages from running — but it IS reported:
        the ``killed`` event's ``branch_deleted`` is the outcome (not the
        request) and a ``residue`` key names every stage that failed, because
        the record that could otherwise be used to find the leftovers is
        deleted a line later.

        **One failure is not forgiven: a container teardown that did not
        happen**. Every other stage fails toward something the user can
        still see and fix by hand — a worktree on disk, a branch that stayed.
        A container Grove cannot remove is nameable only through this record, so
        deleting the record would leave it running and unreachable forever. The
        workspace is kept as ERROR carrying the reason and this raises instead.
        """
        state = self._store.get(workspace_id)
        ensure_can_kill(state)
        is_root = state.placement is Placement.ROOT
        if delete_branch is None:
            delete_branch = state.grove_owns_branch
        if is_root:
            # Hard override: the repo root is never Grove's to remove and the
            # live branch is never Grove's to delete, whatever the caller asks.
            delete_branch = False

        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("kill_session during kill failed: {}", exc)
        # ── container: tmux-first, then the container, then the worktree.
        # Removing a container out from under a live `docker exec` wedges the
        # TTY, and the worktree is the container's bind mount — so this is the
        # only correct position for it, not merely a convenient one.
        container = self._container(state)
        teardown_error: str | None = None
        if container is not None:
            try:
                container.teardown()
            except Exception as exc:
                # Remembered rather than merely logged: the record is the
                # only thing on this host that can still NAME this container, so
                # dropping it below would strand a running container that nothing
                # — no verb, no sweep — can ever find again.
                teardown_error = str(exc)
                logger.warning("container teardown during kill failed: {}", exc)
        branch_deleted, residue = self._drop_git_artifacts(
            state, is_root=is_root, delete_branch=delete_branch
        )
        if teardown_error is not None:
            # Everything else already ran — this is the one failure that must not
            # end in a forgotten record, so the workspace survives as ERROR
            # carrying the reason, and `kill` (which accepts every status) is the
            # retry. Its logs are kept for the same reason a rolled-back init's
            # are: they are what the failure has to be diagnosed from.
            detail = f"container teardown failed: {teardown_error}"
            self._record_error(state, detail)
            self._emit("error", state.id, {"phase": "kill.container", "error": teardown_error})
            raise GroveError(
                f"{detail}; the workspace record was KEPT so the container can still be "
                "named — fix the cause and run kill again"
            )
        # Only the init log goes: it is the user's own script output, and
        # re-running init regenerates it. The PROVISION log stays — it is the
        # sole record of what the container path decided silently, and a killed
        # container cannot be re-run to produce it again, so dropping it here
        # destroyed the one artifact a user debugging that container needs.
        _drop_init_log(state.id)
        self._store.delete(state.id)
        self._last_reconciled_status.pop(state.id, None)
        killed_detail = {"branch_deleted": "true" if branch_deleted else "false"}
        if residue:
            # Names the stages that did NOT happen, so a caller can tell a clean
            # teardown from one that left work behind. `branch_deleted` reports
            # the OUTCOME for the same reason: it used to echo the *intent*, so a
            # `git branch -D` that failed still announced a deleted branch and
            # the one surface that could have said otherwise said nothing.
            killed_detail["residue"] = ",".join(residue)
        self._emit("killed", state.id, killed_detail)

    def _drop_git_artifacts(
        self, state: WorkspaceState, *, is_root: bool, delete_branch: bool
    ) -> tuple[bool, Sequence[str]]:
        """Remove `kill`'s git-side leftovers; report what actually went.

        Best-effort by contract — a failed worktree removal must not stop the
        branch delete, since each leaves a different, separately fixable thing
        behind. But best-effort is not the same as unreported: `kill` deletes the
        record right after this, and the record is what a user would otherwise
        use to find the leftovers, so the outcome has to leave here with the
        caller. Returns whether the branch is really gone, plus the names of the
        stages that failed.
        """
        residue: list[str] = []
        branch_deleted = False
        if not is_root:
            try:
                self._git.worktree_remove(Path(state.worktree_path), force=True)
            except Exception as exc:
                residue.append("worktree")
                logger.warning("worktree_remove during kill failed: {}", exc)
        if delete_branch:
            try:
                self._git.branch_delete(state.branch, force=True)
                branch_deleted = True
            except Exception as exc:
                residue.append("branch")
                logger.warning("branch_delete during kill failed: {}", exc)
        if not is_root:
            try:
                self._git.worktree_prune()
            except Exception as exc:
                residue.append("worktree_prune")
                logger.warning("worktree_prune during kill failed: {}", exc)
        return branch_deleted, residue

    def update(
        self,
        workspace_id: str,
        *,
        title: str | _Unset = _UNSET,
        description: str | None | _Unset = _UNSET,
    ) -> WorkspaceState:
        """Rename the title or set/clear the description on a workspace.

        Metadata-only — never touches the worktree, the tmux session, or
        the branch. Title is the slug seed for the worktree path and tmux
        session name *at create time*; both are persisted strings after
        that and renaming the title does NOT rebuild them. The user keeps
        the on-disk worktree dir and the live tmux session they already
        have; only the displayed title changes.

        Sentinel semantics: ``_UNSET`` (the default) means "leave alone".
        ``title="..."`` sets a new title (must be 1..120 chars after
        stripping). ``description=None`` or ``description=""`` clears it
        (stored as None — empty string and None are equivalent and we
        normalize on write). ``description="..."`` sets it.

        Refuses if both args are unset (nothing to do) and if the
        workspace is ORPHANED (worktree gone; record headed for kill).
        Emits an ``"updated"`` event with ``title_changed`` /
        ``description_changed`` flags so subscribers know what shifted
        without diffing themselves.
        """
        if title is _UNSET and description is _UNSET:
            raise WorkspaceStateError("update requires at least one of title, description")
        # Read persisted state for the write path (preserves the persisted
        # intent — RUNNING/PAUSED/ERROR — that the store can round-trip),
        # AND a reconciled view for the validation path so ORPHANED is
        # rejected even though the persisted intent is still RUNNING.
        persisted = self._store.get(workspace_id)
        ensure_can_update(self._reconcile_status(persisted))

        changes: dict[str, object] = {}
        title_changed = False
        if not isinstance(title, _Unset):
            new_title = title.strip()
            if not new_title:
                raise WorkspaceStateError("title must not be empty")
            if len(new_title) > 120:
                raise WorkspaceStateError("title must be 120 characters or fewer")
            if new_title != persisted.title:
                changes["title"] = new_title
                title_changed = True

        description_changed = False
        if not isinstance(description, _Unset):
            new_description: str | None
            if description is None:
                new_description = None
            else:
                stripped = description.strip()
                if len(stripped) > 2000:
                    raise WorkspaceStateError("description must be 2000 characters or fewer")
                new_description = stripped or None
            if new_description != persisted.description:
                changes["description"] = new_description
                description_changed = True

        if not changes:
            # Nothing actually changed (caller passed the same values).
            # Return current state, do not bump updated_at, do not emit.
            return persisted

        new_state = _replace(persisted, updated_at=_utcnow(), **changes)
        self._store.save(new_state)
        self._emit(
            "updated",
            new_state.id,
            {
                "title_changed": "true" if title_changed else "false",
                "description_changed": "true" if description_changed else "false",
            },
        )
        return new_state

    def attach_ticket(self, workspace_id: str, selector: TicketSelector) -> WorkspaceState:
        """Manually associate a ticket with a workspace (the branch-parse override).

        Idempotent by ``(provider, id)``: re-attaching the same ticket is a
        no-op. A pull request is attached the same way, by the same selector
        carrying ``kind="pull_request"`` — one list, no parallel PR surface. The
        key stays ``(provider, id)`` because that is what the forge links on, so
        re-attaching a ref whose ``kind`` was wrong (a branch parse assumed
        ``issue``) CORRECTS it in place rather than silently keeping the stale
        kind or growing a duplicate row.

        The ref is stored bare (provider + id + kind) — display enrichment
        (title/status) is the daemon's on-demand fetch, never persisted here, so
        attach stays pure and offline-safe. Permitted in any status except
        ORPHANED (same gate as ``update`` — a doomed record gains nothing).
        """
        persisted = self._store.get(workspace_id)
        ensure_can_update(self._reconcile_status(persisted))
        key = (selector.provider, selector.id)
        matched = [r for r in persisted.ticket_refs if (r.provider, r.id) == key]
        if matched and all(r.kind == selector.kind for r in matched):
            return persisted
        new_refs = [
            *(r for r in persisted.ticket_refs if (r.provider, r.id) != key),
            TicketRef(provider=selector.provider, id=selector.id, kind=selector.kind),
        ]
        new_state = _replace(persisted, updated_at=_utcnow(), ticket_refs=new_refs)
        self._store.save(new_state)
        self._emit(
            "updated",
            new_state.id,
            {"ticket_attached": f"{selector.provider}:{selector.id}"},
        )
        return new_state

    def attach_link(self, workspace_id: str, link: str) -> WorkspaceState:
        """Attach whatever a human typed — a URL, ``#42``, ``42``, ``owner/repo#42``.

        The one seam between raw text and :meth:`attach_ticket`: the repo's
        provider registry resolves the link (inferring provider and issue-vs-PR)
        and the attach itself is unchanged, so every surface that accepts a
        pasted link gets identical parsing, identical ambiguity refusal, and
        identical idempotency. Raises ``TicketLinkError`` /
        ``TicketLinkAmbiguous`` before touching the store.
        """
        return self.attach_ticket(workspace_id, self.ticket_providers.resolve_link(link))

    def detach_ticket(self, workspace_id: str, provider: str, ticket_id: str) -> WorkspaceState:
        """Remove a ticket association. Idempotent — a missing ref is a no-op."""
        persisted = self._store.get(workspace_id)
        ensure_can_update(self._reconcile_status(persisted))
        new_refs = [
            r for r in persisted.ticket_refs if not (r.provider == provider and r.id == ticket_id)
        ]
        if len(new_refs) == len(persisted.ticket_refs):
            return persisted
        new_state = _replace(persisted, updated_at=_utcnow(), ticket_refs=new_refs)
        self._store.save(new_state)
        self._emit("updated", new_state.id, {"ticket_detached": f"{provider}:{ticket_id}"})
        return new_state

    def find_by_ticket(self, provider: str, ticket_id: str) -> WorkspaceState | None:
        """Resolve the workspace tracking ticket ``(provider, ticket_id)``, if any.

        The issue-ops routing seam: "does a workspace already exist for this
        ticket, so steer it instead of creating a new one." Scans ``list()``
        (already reconciled to each workspace's displayed status) for a
        ``ticket_refs`` match — no new persisted state, no separate index.

        ``kill()`` deletes the persisted record outright rather than marking
        it KILLED, so a killed workspace can never surface here — "the newest
        non-killed match" is true of everything ``list()`` returns by
        construction. When more than one live workspace tracks the same
        ticket (hand-attached twice, or a fresh workspace opened for a ticket
        an older one already tracks), the tie-break is the NEWEST by
        ``created_at`` — the most recent workspace is the one issue-ops
        should steer.
        """
        matches = [
            state
            for state in self.list()
            if any(r.provider == provider and r.id == ticket_id for r in state.ticket_refs)
        ]
        if not matches:
            return None
        return max(matches, key=lambda s: s.created_at)

    def remap_session(self, workspace_id: str, session_ref: str) -> WorkspaceState:
        """Manually pin an existing agent session as this workspace's primary.

        The trusted-operator counterpart to the automatic discovery/adoption
        path: when ``/clear`` rotated the id (the minted pointer went dead), or a
        hand-started session should own the card, the user names it and Grove
        records it as ``agent_session_id`` — the very field ``create()`` mints —
        so every read path (the dashboard blend, ``sessions_for``, the CLI
        inspector) tracks it by construction, no discovery heuristic needed.

        Resolution runs through the project's :class:`SessionExplorer`
        (``resolve`` accepts a unique id-prefix, scoped to this repo's worktrees),
        so a typo or a foreign id fails loudly *before* the write — re-raised as
        :class:`AgentSessionNotFound` (404) rather than the bare ``GroveError``
        resolve emits. Idempotent by resolved id, mirroring ``attach_ticket``:
        re-pinning the same session is a no-op (no re-persist, no event).

        Manual pinning is TRUSTED: unlike discovery it applies **no** ``created_at``
        birth-gate — the operator's explicit choice outranks the heuristic,
        exactly as a ``grove_launched`` session is never gated. It DOES enforce
        adapter-kind equality: pinning a codex session onto a claude_code
        workspace would leave the workspace's adapter permanently unable to read
        it (a dead pointer that returns 200) — so a kind mismatch is rejected as
        ``AgentSessionNotFound`` naming both kinds. Permitted in any status except
        ORPHANED (the ``attach_ticket`` gate — a doomed record gains nothing).
        Emits ``updated`` with ``session_remapped: <id>``.
        """
        persisted = self._store.get(workspace_id)
        ensure_can_update(self._reconcile_status(persisted))
        try:
            listing = self._session_explorer().resolve(session_ref)
        except GroveError as exc:
            # resolve() raises a bare GroveError for no-match / ambiguous-prefix;
            # re-raise in the session domain so the daemon maps it to 404 rather
            # than a generic 500. The original message (candidate ids on an
            # ambiguous prefix) is preserved so the user can extend the prefix.
            raise AgentSessionNotFound(str(exc)) from exc
        resolved_id = listing.summary.session_id
        kind = self.effective_kind(persisted)
        if listing.summary.adapter_kind != kind:
            raise AgentSessionNotFound(
                f"session {resolved_id} is a {listing.summary.adapter_kind} session, but "
                f"workspace {persisted.id} runs a {kind} agent whose adapter cannot read a "
                f"{listing.summary.adapter_kind} transcript"
            )
        if persisted.agent_session_id == resolved_id:
            return persisted  # idempotent: already pinned to this session
        new_state = _replace(persisted, updated_at=_utcnow(), agent_session_id=resolved_id)
        self._store.save(new_state)
        self._emit("updated", new_state.id, {"session_remapped": resolved_id})
        return new_state

    def _session_explorer(self) -> SessionExplorer:
        """A read-only :class:`SessionExplorer` over this same manager.

        Local import: ``sessions.py`` imports ``manager.py``, so a module-level
        import here would cycle. Construction is cheap (no I/O — the explorer
        only holds the manager); building one per ``remap_session`` call keeps the
        session-ref resolution DRY with ``grove sessions`` instead of duplicating
        the unique-prefix scan.
        """
        from grove.core.sessions import SessionExplorer  # noqa: PLC0415

        return SessionExplorer(self)

    def attach(self, workspace_id: str) -> AttachInstruction:
        """Where the client should attach, and to WHOSE multiplexer.

        Two arms, chosen by the one predicate the launch already branches on —
        whether this workspace's container can run a tmux
        (`_container_tmux`, i.e. `ContainerRuntimeState.tmux_command`):

        * **Container** — the container's own tmux owns the session, so the
          client execs straight into it. There is deliberately no host session
          to target: a host pane running `devcontainer exec … tmux attach` is a
          shadow client that clamps every later client's terminal size to its
          own (measured: a client asking for 200x50 got 161x41).
        * **Host** — every host workspace, and the container that has no tmux
          inside it, where the agent really does run in a host pane.

        The container argv is composed from the SAME
        :class:`~grove.core.container_agent.ContainerAgentEntry` that starts the
        agent, so the way in cannot drift from the way it was launched.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_attach(state)
        entry = self._container_entry(state, session=self._cfg.container.tmux.session)
        if entry is not None:
            return ContainerAttach(argv=tuple(entry.argv(detached=False)))
        return tmux.attach_instruction(state.tmux_session)

    def _container_entry(
        self, state: WorkspaceState, *, session: str
    ) -> ContainerAgentEntry | None:
        """The in-container entry for *session*, or ``None`` if there is no tmux there.

        The one composition behind every "get me into this container's tmux"
        answer — `attach` and `container_agent_argv` — so the workspace's own
        agent and any additional one are entered identically. ``None`` on
        exactly the predicate `_container_tmux` already owns (host workspace,
        fallback workspace, or a container image with no reachable tmux), which
        is what keeps the degrade path a single condition rather than a second
        notion of "can this container run tmux".
        """
        if self._container_tmux(state) is None or state.container is None:
            return None
        return ContainerAgentEntry(
            container=state.container,
            cfg=self._cfg,
            worktree=Path(state.worktree_path),
            cwd=state.agent_cwd,
            session=session,
            cli=self._devcontainer(),
        )

    # ─── several agents in one container ────────────────────────────────────
    #
    # Opt-in and user-driven by construction: nothing below runs unless a user
    # asks for it, and no create, resume or respawn calls any of it. The design
    # decision — extra agents are in-container tmux SESSIONS with no persisted
    # state, rather than plural `agent_session_id` or a second workspace record
    # — and what it rejected are recorded on `grove.core.container_agent`.

    def container_agents(self, workspace_id: str) -> tuple[ContainerAgent, ...]:
        """Every agent running inside this workspace's container.

        Read live from the container's own tmux server, never from the record:
        that server is the only thing that knows an agent ended on its own, and
        a persisted roster would be a migration plus a way to be wrong. The
        workspace's own agent is included and flagged `primary`, because "what
        is running in here" that omitted the main one would be a trap.

        Raises rather than returning empty when docker cannot be read: an empty
        roster and an unreadable one look identical to a caller and mean
        opposite things — the standing rule that "cannot tell" must never be spelled
        as an answer.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        reports = self._container_tmux_or_refuse(state).list_sessions()
        if reports is None:
            raise ContainerError(
                f"could not read the tmux server inside workspace {state.id}'s container "
                f"(is {self._cfg.container.docker_bin!r} on this process's PATH?)"
            )
        return ContainerAgent.roster(
            reports,
            primary=self._cfg.container.tmux.session,
            shell=self._cfg.container.tmux.shell_session,
        )

    def add_container_agent(
        self,
        workspace_id: str,
        *,
        agent: str | None = None,
        name: str | None = None,
        model: str | None = None,
        initial_prompt: str | None = None,
    ) -> ContainerAgent:
        """Start ANOTHER agent inside this workspace's container. Returns it.

        The whole of the user's ask: *"multiple Claude Code instances can also
        run within the same container if necessary by the person of choice"*.
        Never automatic — no lifecycle verb calls this.

        The launch is composed through exactly the seams the workspace's own
        agent uses — `_compose_launch` for the decoration (session id, hook
        settings, channels, model, the trailing prompt positional) and
        `_launch_env` for the hermetic env — so the second agent is configured
        identically to the first and the two cannot drift. It differs in three
        things only: its own in-container session name, `-A -d` (start it, do
        not drop the caller into it), and no `remain-on-exit`. That last is
        deliberate and follows the rule story 3 established: keeping a corpse is
        only safe where something owns the clearing, and for the primary agent
        the launch backend owns it. Nothing owns it here, so an additional agent
        that exits leaves no session — which `container_agents` then honestly
        stops listing.

        **Not persisted, and that is the design rather than a shortcut**: the
        agent's transcript reaches the workspace's agent axis on its own, since
        the existing discovery/adoption path adopts any session of the
        workspace's kind born in its cwd after it was created (see
        `ActivityService.sessions_for`). The one honest limitation: an agent of a
        DIFFERENT kind from the workspace's is not surfaced there, because that
        scan is kind-scoped on purpose — it runs, it is listable,
        attachable, peekable and steerable, but the workspace card stays about
        the workspace's own kind.

        A remote-steered kind is refused: a mewbo session runs on a backend, so
        "add one to this container" names nothing.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_steer(state)
        container_tmux = self._container_tmux_or_refuse(state)
        container = state.container
        if container is None:  # pragma: no cover - `_container_tmux_or_refuse` proved it
            raise CapabilityUnavailable(f"workspace {state.id} has no container")
        spec = self._agent_spec(agent or state.agent_name)
        if spec.kind in _REMOTE_STEERED_KINDS:
            raise CapabilityUnavailable(
                f"agent {spec.name!r} is kind {spec.kind!r}, which runs on a backend rather "
                "than in this container — there is nothing to add here"
            )
        reports = container_tmux.list_sessions()
        if reports is None:
            raise ContainerError(
                f"could not read the tmux server inside workspace {state.id}'s container "
                "to choose a free agent name"
            )
        slot = ContainerAgent.mint_name(
            name,
            base=self._cfg.container.tmux.session,
            taken=frozenset(
                {report.name for report in reports} | {self._cfg.container.tmux.shell_session}
            ),
        )
        worktree = Path(state.worktree_path)
        session_id = self._mint_agent_session_id(spec, worktree=worktree, title=slot)
        code, output = ContainerAgentEntry(
            container=container,
            cfg=self._cfg,
            worktree=worktree,
            cwd=state.agent_cwd,
            session=slot,
            command=spec.command,
            decoration=tuple(
                self._compose_launch(
                    spec,
                    session_id,
                    state=state,
                    model=model,
                    initial_prompt=initial_prompt,
                )
            ),
            # `agent_slot` so this agent reports its task phase to its OWN file
            # rather than overwriting the workspace's — the one field in
            # the launch env that is per-agent rather than per-workspace.
            env=self._launch_env(state, spec, agent_slot=slot),
            cli=self._devcontainer(),
        ).start()
        if code != 0:
            raise ContainerError(
                f"could not start agent {slot!r} in workspace {state.id}'s container "
                f"(exit {code}): {output.strip() or 'no output'}"
            )
        self._assert_agent_started(container_tmux, slot=slot, command=spec.command, state=state)
        self._emit("control_invoked", state.id, {"control": "agent_add", "agent": slot})
        logger.info(
            "workspace {}: started agent {} ({}) in its container", state.id, slot, spec.name
        )
        return ContainerAgent(name=slot, primary=False, attached=False, idle_seconds=0)

    @staticmethod
    def _assert_agent_started(
        container_tmux: ContainerTmux, *, slot: str, command: str, state: WorkspaceState
    ) -> None:
        """Re-read the server, because `new-session -d`'s exit status answers the wrong question.

        Measured on a real container: ``tmux new-session -A -d -s x
        <missing-binary>`` exits **0** — tmux DID create a session — and by the
        time the very next command runs the session is already gone, because the
        pane process could not start and nothing here keeps a corpse. So the
        exit status answers "did tmux create a session", never "is the agent
        running", and reporting success off it hands the user a name that
        addresses nothing (`grove agent list` a second later shows one agent,
        `attach` says there is no such agent, and `add` already said it worked).

        Found by driving a real `grove agent add --agent codex` against an image
        that ships no codex, which is the ordinary case for adding an agent of a
        DIFFERENT kind from the one the image was built for — not an edge.

        The read is deterministic in the direction that matters: an agent that
        cannot start is already gone when this runs, so an absent session means
        it failed rather than that it has not appeared yet. An agent that starts
        and dies a moment LATER still reports started, which is honest — it did
        — and the roster is what tells the truth from then on.
        """
        after = container_tmux.list_sessions()
        if after is None or any(report.name == slot for report in after):
            return
        raise ContainerError(
            f"agent {slot!r} did not stay running in workspace {state.id}'s container: "
            f"tmux started a session and {command!r} exited immediately. Is that command "
            "installed in this image? (`grove shell` to look)"
        )

    def kill_container_agent(self, workspace_id: str, name: str) -> None:
        """End one ADDITIONAL agent's in-container session. Idempotent.

        Refuses the workspace's own agent by name: that one has lifecycle verbs
        of its own (`pause` / `respawn` / `kill`), which also deal with the
        container, the worktree and the record — ending its session from here
        would leave the workspace looking alive with nothing in it, and no verb
        would report why.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        slot = ContainerAgent.validate_name(name)
        if slot == self._cfg.container.tmux.session:
            raise CapabilityUnavailable(
                f"{slot!r} is workspace {state.id}'s own agent — pause, respawn or kill the "
                "workspace instead; this verb ends the ADDITIONAL agents in its container"
            )
        if slot == self._cfg.container.tmux.shell_session:
            raise CapabilityUnavailable(
                f"{slot!r} is the interactive shell session (`grove shell`), not an agent"
            )
        self._container_tmux_or_refuse(state, session=slot).end_session()
        self._emit("control_invoked", state.id, {"control": "agent_kill", "agent": slot})

    def container_agent_argv(self, workspace_id: str, name: str) -> _Argv:
        """The argv that attaches a terminal to one in-container agent.

        Returned rather than executed for the same reason `attach` returns an
        `AttachInstruction`: the core does not own the caller's process model —
        the CLI `execvp`s this and *becomes* the exec.

        The session is verified to exist first, which is what lets the argv omit
        a command entirely (`-A` ignores one when attaching anyway) instead of
        re-deriving an agent command tmux would throw away — and it turns "that
        agent has ended" into Grove's own sentence rather than tmux's.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_attach(state)
        slot = ContainerAgent.validate_name(name)
        running = {found.name for found in self.container_agents(workspace_id)}
        if slot not in running:
            raise AgentSessionNotFound(
                f"no agent {slot!r} is running in workspace {state.id}'s container "
                f"(running: {', '.join(sorted(running)) or 'none'})"
            )
        entry = self._container_entry(state, session=slot)
        if entry is None:  # pragma: no cover - `container_agents` proved it
            raise CapabilityUnavailable(f"workspace {state.id} has no container")
        return entry.argv(detached=False)

    def _agent_spec(self, name: str) -> AgentSpec:
        """The roster entry called *name*, or a typed refusal listing what exists."""
        spec = self._cfg.find_agent(name)
        if spec is None:
            raise GroveError(
                f"unknown agent {name!r} — configured agents: "
                f"{', '.join(a.name for a in self._cfg.agents) or 'none'}"
            )
        return spec

    def send_message(self, workspace_id: str, text: str, *, agent: str = "") -> None:
        """Type ``text`` into the workspace's agent pane and submit it.

        Grove's follow-up/steer surface for tmux-hosted agents.
        Policy lives here; the literal-safe injection mechanism is
        ``tmux.send_text``. Gates, in order: remote-steered kinds dispatch
        to the adapter arm; the session must be live (OFFLINE/PAUSED →
        typed ``WorkspaceStateError``); a pane must resolve via the same
        ``pane_target`` policy peek captures from (None → ``PaneNotFound``).

        ``agent`` names one of the ADDITIONAL agents a container may host;
        empty is the workspace's own agent and every gate below is
        unchanged. A named agent takes the container arm and only that arm: a
        remote (mewbo) session and a paneless runtime have no container tmux to
        hold a second agent, so naming one there is refused rather than
        misdelivered to the primary — which would be exactly the "silently pick
        one and pretend" this story is not allowed to do.

        The ``message_sent`` audit event carries the resolved target and
        the text *length*, never the content — steering text can hold
        secrets and events fan out to every subscriber and log sink.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        if agent:
            self._container_tmux_or_refuse(state, session=agent)
        elif state.agent_kind is not None and state.agent_kind in _REMOTE_STEERED_KINDS:
            self._steer_remote(state, "message", text)
            return
        if not agent and not self._launch_backend.provides_pane:
            # Paneless runtime: no tmux pane to type into, so deliver over
            # the agent's native channel instead of raising. Same
            # dispatch-point pattern as the remote arm — one seam, not a fork.
            self._steer_native(state, "message", text)
            return
        ensure_can_steer(state)
        pane = self._agent_pane(state, session=agent)
        if pane is None:
            raise PaneNotFound(
                f"no tmux pane resolved for workspace {state.id} "
                f"(session {state.tmux_session!r} reports no windows)"
            )
        pane.send_text(text, settle_ms=self._cfg.tmux.steer_settle_ms)
        self._emit(
            "message_sent",
            state.id,
            {"target": pane.display, "text_length": str(len(text))},
        )

    def interrupt(self, workspace_id: str) -> None:
        """Interrupt the workspace's agent, where its adapter supports it.

        Today no tmux-hosted kind does: an Escape or C-c keystroke into an
        arbitrary CLI is not a contract — it might cancel a prompt, kill a
        shell job, or do nothing, and the provider-boundary rule forbids
        guessing per-tool key bindings. So claude_code/generic refuse with
        ``SteeringUnsupported``. The mewbo arm (a real API interrupt) goes
        through the same ``_steer_remote`` dispatch point as send_message.
        """
        state = self._store.get(workspace_id)
        if state.agent_kind is not None and state.agent_kind in _REMOTE_STEERED_KINDS:
            self._steer_remote(state, "interrupt")
            return
        if not self._launch_backend.provides_pane:
            # Paneless runtime: no pane to signal, so route the interrupt
            # over the native channel instead of raising. Best-effort — a
            # native interrupt primitive is still landing (stream-json control),
            # but this no longer refuses the op the way the old capability gap did.
            self._steer_native(state, "interrupt")
            return
        raise SteeringUnsupported(
            f"agent kind {state.agent_kind or 'generic'!r} has no safe interrupt: "
            "injecting a cancel keystroke into an arbitrary CLI is not a contract"
        )

    def _steer_remote(
        self,
        state: WorkspaceState,
        op: Literal["message", "interrupt"],
        text: str | None = None,
    ) -> None:
        """THE single dispatch point for remote-steered (mewbo) agents.

        Deliberately no tmux/liveness gate: the remote session outlives the
        pane, and ``POST /message`` re-engages an idle or finished session by
        design — only a terminated one rejects, surfacing as the typed
        ``MewboError``. The audit event mirrors the tmux arm's shape (target +
        text length, never content).
        """
        session_id = state.agent_session_id
        if not session_id:
            raise AgentSessionNotFound(
                f"workspace {state.id} has no recorded mewbo session to steer"
            )
        if op == "message":
            self._mewbo().send_message(session_id, text or "")
            self._emit(
                "message_sent",
                state.id,
                {"target": f"mewbo:{session_id}", "text_length": str(len(text or ""))},
            )
        else:
            self._mewbo().interrupt(session_id)

    def _steer_native(
        self,
        state: WorkspaceState,
        op: Literal["message", "interrupt"],
        text: str | None = None,
    ) -> None:
        """THE single dispatch point for paneless (headless) agents.

        The ``_steer_remote`` mirror for a runtime that has no tmux pane
        (``provides_pane`` False): deliver over the agent's native channel
        instead of typing into a pane. Best-effort like the client it delegates to
        — a delivery failure logs and returns, never raising into the caller's
        path (steering a paneless runtime is fire-and-forget, not a transaction).
        The audit event mirrors the tmux/remote arms (target + text length, never
        content). A workspace with no recorded session (a generic detached shell)
        has nothing to steer — the same ``AgentSessionNotFound`` as the remote arm.
        """
        session_id = state.agent_session_id
        if not session_id:
            raise AgentSessionNotFound(
                f"workspace {state.id} has no recorded agent session to steer natively"
            )
        if op == "message":
            self._native_steer().send_message(session_id, text or "")
            self._emit(
                "message_sent",
                state.id,
                {"target": f"native:{session_id}", "text_length": str(len(text or ""))},
            )
        else:
            self._native_steer().interrupt(session_id)

    def answer_question(self, workspace_id: str, request: QuestionAnswerRequest) -> None:
        """Drive a pending ``AskUserQuestion`` to resolution by keystroke.

        Dispatch semantics, like ``send_message``: this returns as soon as the
        keystrokes are sent — the resolution (the ``tool_result``) lands later and
        streams via the transcript + the PostToolUse sidecar clear. Gates, in
        order: the workspace must exist (``WorkspaceNotFound`` → 404);
        ``session_id`` must be the session Grove minted for this workspace's pane
        (``QuestionNotPending`` → 409, else a foreign or cross-workspace
        session_id could steer keystrokes into the wrong pane); a captured
        question for ``session_id`` must still match ``tool_use_id``
        (``QuestionNotPending`` → 409, the human may have answered in the
        terminal); the plan must fit the captured questions (``QuestionAnswerInvalid``
        → 422); a pane must resolve (``PaneNotFound`` → 409).

        The capture is re-checked immediately before the send to *shrink* — never
        close — the terminal race: if the human answers between our check and our
        keystrokes, the extra keys land in the freshly-reset composer as harmless
        literal text, never as a second answer to a question that is gone. The
        keystroke grammar itself lives in the Claude adapter (the provider
        boundary); the manager only orchestrates and maps errors.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        if request.session_id != state.agent_session_id:
            raise QuestionNotPending(
                f"session {request.session_id!r} is not the agent session bound to "
                f"workspace {state.id}'s pane (expected {state.agent_session_id!r})"
            )
        pending = self._pending_capture(request.session_id, request.tool_use_id)
        questions = AgentQuestion.from_tool_call(
            pending.tool_name, pending.tool_input, pending.tool_use_id
        )
        selections = [
            AnswerSelection(indexes=tuple(a.selected_indexes or ()), text=a.text)
            for a in request.answers
        ]
        if not self._launch_backend.provides_pane:
            # Paneless runtime: no pane to keystroke the picker, so
            # render the answer to text and deliver it over the native channel.
            # The keystroke grammar's picker-only rejections (confirm / optionless
            # free-text / multiSelect+text) don't apply — plain text can answer
            # any question — so this arm skips `build_answer_keys` deliberately.
            self._steer_native(state, "message", native.render_answer(questions, selections))
            self._emit(
                "question_answered",
                state.id,
                {
                    "target": f"native:{request.session_id}",
                    "tool_use_id": pending.tool_use_id,
                    "answers": str(len(selections)),
                },
            )
            return
        try:
            ops = ClaudeCodeAdapter.build_answer_keys(questions, selections)
        except ValueError as exc:
            raise QuestionAnswerInvalid(str(exc)) from exc
        pane = self._agent_pane(state)
        if pane is None:
            raise PaneNotFound(
                f"no tmux pane resolved for workspace {state.id} "
                f"(session {state.tmux_session!r} reports no windows)"
            )
        # Re-check the capture right before the send to shrink the terminal race.
        self._pending_capture(request.session_id, request.tool_use_id)
        pane.send_keys(ops, settle_ms=self._cfg.tmux.steer_settle_ms)
        self._emit(
            "question_answered",
            state.id,
            {
                "target": pane.display,
                "tool_use_id": pending.tool_use_id,
                "answers": str(len(selections)),
            },
        )

    def session_controls(self, workspace_id: str) -> SessionControls:
        """Enumerate the input controls available to this workspace's session.

        The read behind the webapp's control panel: TIER 1 filesystem scan via the
        workspace's adapter (slash commands / skills / MCP servers — cheap, works
        with NO running session), plus the config-derived model catalog
        (``resolve_models`` — the single catalog seam every surface shares) and
        the Grove-hosted permission posture. ``current_model`` is a best-effort
        transcript read (the running session's model), guarded so a parse hiccup
        just leaves it ``None``.

        Best-effort by contract — it feeds a render panel, so a scan/parse failure
        degrades the surface rather than raising (the ``peek`` discipline). The
        workspace must exist (``store.get`` raises ``WorkspaceNotFound``); past
        that, everything is guarded.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        kind = self.effective_kind(state)
        adapter = get_adapter(kind)
        agent = self._cfg.find_agent(state.agent_name)
        session_id = state.agent_session_id or ""
        try:
            # Scoped: the TIER 1 scan reads user-level commands/skills out
            # of the adapter's config-dir cascade, so an unscoped read lists the
            # READER's profile — wrong content, not missing content, which is the
            # harder failure to notice. try/except stays outermost so the panel
            # still degrades rather than raises, scope or no scope.
            with self.transcript_scope(state):
                scanned = adapter.session_controls(state.transcript_scan_cwds[0], session_id)
        except Exception as exc:  # best-effort panel read, must never raise
            logger.debug("session_controls scan failed for {}: {}", state.id, exc)
            scanned = SessionControls.empty()
        models = resolve_models(
            kind=kind,
            command=agent.command if agent is not None else "",
            configured=agent.models if agent is not None else (),
        )
        permission_mode = self._cfg.permission.default if self._cfg.permission.enabled else None
        return _dc_replace(
            scanned,
            models=models,
            current_model=self._current_model(adapter, state, session_id),
            permission_mode=permission_mode,
        )

    def _current_model(
        self, adapter: AgentAdapter, state: WorkspaceState, session_id: str
    ) -> str | None:
        """The model the running session is on, from a best-effort activity parse
        (the transcript records it). ``None`` when sessionless or on any read
        failure — never raises into the controls read."""
        if not session_id:
            return None
        try:
            with self.transcript_scope(state):
                return adapter.parse_activity(state.transcript_scan_cwds[0], session_id).model
        except Exception as exc:  # best-effort; a parse miss is not fatal
            logger.debug("current-model read failed for {}: {}", state.id, exc)
            return None

    def latest_todo(self, workspace_id: str) -> TodoList | None:
        """The workspace's current todo/checklist state — the engine
        seam both the issueops sticky-comment publisher (in-process) and the
        ``GET /workspaces/{id}/todo`` daemon route read.

        Resolves the workspace's session through :meth:`_todo_session_id` —
        which is NOT ``agent_session_id`` alone: a codex workspace mints
        no id at all and a rotated/ended claude id is a dead pointer, so keying
        the axis on the mint made it blank for a whole provider. RAISES
        ``AgentSessionNotFound`` only when neither the mint nor discovery names
        a session — the same convention ``_steer_remote``/``_steer_native``
        use, so a caller can tell "no session yet" (404) apart from "a session
        exists but no todo tool has been called yet" (``None``, a real
        answer). The adapter read itself stays best-effort (never raises) like
        every projection here.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        session_id = self._todo_session_id(state)
        if not session_id:
            raise AgentSessionNotFound(f"workspace {state.id} has no recorded agent session")
        return self.latest_todo_for(state, session_id=session_id)

    def _todo_session_id(self, state: WorkspaceState) -> str | None:
        """Which session the per-REQUEST derived reads take for ``state`` —
        the todo axis and :meth:`latest_task` — deliberately not the poll
        path's resolution.

        ``agent_session_id`` answers this only for a claude_code workspace whose
        mint is alive. Two shipped populations it gets wrong, both reproduced
        before this existed: **codex mints nothing** (no launch flag exists, so
        `_mint_agent_session_id` returns ``None`` and the field stays empty for
        the workspace's whole life), and a **rotated or ended claude id is a
        dead pointer** while the live conversation runs under another id in the
        same cwd. Either way the adapter can read the todo perfectly and the
        engine was handing it the wrong key.

        The rule is the one :meth:`resume` already uses to decide whether a
        pinned session is real — did it MATERIALIZE (``locate_transcripts``
        non-empty across ``scan_cwds``, a glob, no parse) — and only otherwise
        does it pay :meth:`SessionExplorer.for_workspace`, the bounded one-cwd
        scan that is already adoption-gated, kind-scoped and newest-first. An
        unmaterialized mint with nothing discovered keeps the mint, so the
        STARTING window still answers ``None`` rather than 404.

        **Do not "DRY" this into** :meth:`latest_todo_for`. That seam runs per
        workspace per ~1 Hz tick, where a discovery scan reproduces the daemon
        CPU bug a full transcript re-parse causes; its caller
        (``ActivityService``) has already resolved the very same session by
        the full adoption path and passes it in. Two resolutions with the same
        OUTCOME and opposite cost profiles — the ``discover_paths``/
        ``discover_all`` shape, one layer up.
        """
        session_id = state.agent_session_id
        adapter = get_adapter(self.effective_kind(state))
        with self.transcript_scope(state):
            if session_id and any(
                adapter.locate_transcripts(cwd, session_id) for cwd in state.transcript_scan_cwds
            ):
                return session_id
        listings = self._session_explorer().for_workspace(state.id)
        return listings[0].summary.session_id if listings else session_id or None

    def latest_todo_for(
        self, state: WorkspaceState, *, session_id: str | None = None
    ) -> TodoList | None:
        """:meth:`latest_todo` for a state a caller ALREADY holds reconciled.

        The ``pane_target``/``pane_target_for`` split, for the same
        reason: the activity poll hands every read its own tick-reconciled
        ``WorkspaceState``, and an id-taking sibling would re-fetch it from the
        store and re-run ``_reconcile_status`` — per workspace, per tick — for
        an answer the caller already has. Degrades to ``None`` for a sessionless
        workspace rather than raising: the 404-vs-``None`` distinction is the
        *id* seam's contract (a caller that named a workspace is owed the
        difference), whereas a render path enumerating every workspace only ever
        wants "nothing to show".

        ``session_id`` is the session a caller has ALREADY resolved, and passing
        it is what keeps this seam cheap AND correct. ``ActivityService``
        hands over the primary its own tick just adopted — which is the codex /
        dead-pointer answer :meth:`_todo_session_id` reaches by scanning, for
        free and one step more accurate (it carries the tick's dead-mint
        promotion). Omitted, this falls back to ``agent_session_id`` and does NO
        discovery of its own: a scan per workspace per ~1 Hz tick is the
        daemon-CPU bug the incremental transcript cache exists to prevent, so
        the expensive arm lives only on the per-request id seam.

        The ``cwd`` handed to the adapter stays ``transcript_scan_cwds[0]``, and
        iterating the union here would be inert surface: both filesystem
        adapters resolve a transcript by GLOBBING the session id (Claude across
        every projects folder, Codex across the date-partitioned tree) and use
        ``cwd`` only to break a tie between files claiming the same id — so a
        session recorded at the worktree root of a nested workspace already
        resolves from the first entry. The union matters where the SESSION is
        being found rather than read, which is ``_scan_workspace``'s job.

        Scoped. This seam has THREE consumers now — the daemon route, the
        issueops sticky-comment publisher in-process, and the dashboard card — so
        an unscoped read here doesn't just blank a panel, it posts an empty
        checklist into a real issue comment. The adapter read stays best-effort
        on its own.
        """
        session_id = session_id or state.agent_session_id
        if not session_id:
            return None
        adapter = get_adapter(self.effective_kind(state))
        with self.transcript_scope(state):
            return adapter.latest_todo(state.transcript_scan_cwds[0], session_id)

    def latest_task(self, workspace_id: str) -> str | None:
        """The workspace's current task text, UNCAPPED — the engine seam the
        issueops sticky-comment publisher reads in-process.

        The :meth:`latest_todo` sibling in every respect: same
        :meth:`_todo_session_id` resolution (NOT ``agent_session_id`` alone —
        codex mints no id and a rotated claude id is a dead pointer), same
        ``AgentSessionNotFound`` on a genuinely sessionless workspace so a
        caller that NAMED a workspace can tell 404 from "a session exists and
        carries no task text yet" (``None``), same best-effort adapter read.

        The text is the one ``AgentActivity.current_task`` carries, minus the
        adapters' ``_TASK_TEXT_CAP`` truncation. **The wire field stays
        capped**: it rides the ~1 Hz activity delta for every workspace on the
        host plus every TUI row and webapp card, where an arbitrarily large
        pasted prompt is a real cost. A reader that renders the text ONCE per
        flush — the sticky comment, inside a collapsed ``<details>`` — pays
        nothing for the whole text, so it reads it here instead of re-deriving
        a truncated one.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        session_id = self._todo_session_id(state)
        if not session_id:
            raise AgentSessionNotFound(f"workspace {state.id} has no recorded agent session")
        return self.latest_task_for(state, session_id=session_id)

    def latest_task_for(
        self, state: WorkspaceState, *, session_id: str | None = None
    ) -> str | None:
        """:meth:`latest_task` for a state a caller ALREADY holds reconciled —
        the :meth:`latest_todo_for` split, for the identical reason.

        Degrades to ``None`` for a sessionless workspace rather than raising
        (the 404-vs-``None`` distinction is the *id* seam's contract), does NO
        discovery of its own (a scan per workspace per tick is the daemon-CPU
        bug the transcript cache exists to prevent), and reads
        ``transcript_scan_cwds[0]`` because both filesystem adapters resolve a
        transcript by globbing the session id. Scoped, like every transcript
        read here: an unscoped read on a profile-pinned workspace answers
        ``None`` and the sticky comment silently drops the task line.
        """
        session_id = session_id or state.agent_session_id
        if not session_id:
            return None
        adapter = get_adapter(self.effective_kind(state))
        with self.transcript_scope(state):
            return adapter.latest_task(state.transcript_scan_cwds[0], session_id)

    # ─── task phase (the third status axis, see grove.core.phase) ──────────

    def phase_for(self, state: WorkspaceState) -> PhaseReport | None:
        """The agent's own claim about how far through its task it is, or ``None``.

        Read from ``<worktree>/.grove/phase/<workspace id>.json`` — **the
        worktree root, never ``agent_cwd``**, and the two differ for a nested
        ``project_subpath``. Three reasons, in ascending order of how
        expensive getting it wrong would be:

        1. ``.grove/`` is already a worktree-root concept (it holds the
           project's committed ``config.json``); a second ``.grove`` inside a
           subdirectory would be a new location a user has to learn.
        2. The worktree root is the one directory that is unique per workspace
           AND is the bind-mount root inside a container, so host and
           containerized agents name the same file.
        3. **The git exclude that keeps ``pause``/``kill`` working is
           ANCHORED.** A pattern containing a slash in ``info/exclude`` matches
           only relative to the working-tree root, so ``.grove/phase/`` excludes
           ``<worktree>/.grove/phase/`` and NOT ``<worktree>/sub/.grove/phase/``
           (verified against real git). Anchoring at ``agent_cwd``
           would therefore leave every nested-project workspace's phase file
           untracked, and ``git worktree remove`` refuses on untracked files —
           pause and kill would break for exactly the workspaces the subpath
           feature exists to serve.

        The per-workspace key is what stops two ROOT workspaces (whose worktree
        IS the shared repo root) from overwriting each other; the legacy single
        file is read only when the keyed one is absent, so a workspace that
        reported before that layout landed keeps its badge.

        Best-effort by contract, like ``peek``: a paused workspace whose
        worktree is gone, an unreadable file, or an agent's typo yields ``None``
        rather than breaking a caller's snapshot.
        """
        worktree = state.worktree_path
        return PhaseFile.read(worktree, PhaseFile.key_for(state.id)) or PhaseFile.read(
            worktree, None
        )

    def phase(self, workspace_id: str) -> PhaseReport | None:
        """:meth:`phase_for` by workspace id — the CLI/MCP read seam."""
        return self.phase_for(self._store.get(workspace_id))

    def set_phase(
        self, workspace_id: str, phase: TaskPhase, note: str | None = None
    ) -> PhaseReport:
        """Record a phase claim for a workspace — the CLI verb / MCP tool seam.

        The in-workspace agent never comes through here; it writes the file
        directly (that is the whole point of the file channel). This is for an
        orchestrator or a human setting or correcting a workspace's phase from
        outside, so it is LOUD where :meth:`phase_for` is best-effort — a caller
        that asked to write is entitled to know it did not happen.

        No event is emitted: the phase is derived per poll tick from the file
        rather than persisted on the record, so the existing
        ``session_activity`` delta already streams it (the fingerprint carries
        it). A ``workspace_changed`` wake-up would only tell clients to re-fetch
        a record that does not carry the phase at all.
        """
        state = self._store.get(workspace_id)
        return PhaseFile.write(state.worktree_path, PhaseFile.key_for(state.id), phase, note)

    def invoke_control(self, workspace_id: str, name: str) -> None:
        """Invoke a named session control — a slash command or a skill.

        Thin trigger: composes the tool's ``/name`` invocation and delivers it
        through the EXISTING steer path (:meth:`send_message` → tmux keystroke /
        native channel / remote), so it reuses the whole dispatch, the settle
        window, and the audit trail rather than adding a second delivery
        mechanism. Kind-gated to the tools that actually expose a slash-control
        surface (``_CONTROL_KINDS``); a generic shell or a remote orchestrator
        raises ``CapabilityUnavailable`` (well-formed, but the runtime can't act
        on it). Best-effort dispatch semantics like ``send_message`` — 204/return
        is "delivered", not "ran"."""
        self._deliver_control(workspace_id, name)

    def switch_model(self, workspace_id: str, model: str) -> None:
        """Switch the running session's model where the agent exposes a switch
        control.

        claude_code/codex expose it as the interactive ``/model <id>`` slash
        command, delivered through the same steer path as :meth:`invoke_control`
        (the provider boundary — the command shape is the tool's, the id forwarded
        verbatim, never interpreted). A kind with no model-switch channel (a bare
        shell, a remote session whose model is fixed at create) raises
        ``CapabilityUnavailable``."""
        cleaned = model.strip()
        if not cleaned:
            raise CapabilityUnavailable("cannot switch to an empty model id")
        self._deliver_control(workspace_id, f"model {cleaned}")

    def _deliver_control(self, workspace_id: str, invocation: str) -> None:
        """Deliver a ``/invocation`` slash control through the steer path —
        the one dispatch both ``invoke_control`` and ``switch_model`` share, so
        they can't drift on gating or event shape. ``/`` is the slash-control
        syntax both enabled tools use; a future tool with a different prefix would
        move this to an adapter seam (YAGNI: two real impls, one prefix)."""
        state = self._reconcile_status(self._store.get(workspace_id))
        kind = self.effective_kind(state)
        cleaned = invocation.strip().lstrip("/").strip()
        if kind not in _CONTROL_KINDS:
            raise CapabilityUnavailable(
                f"agent kind {kind!r} exposes no in-session slash-control surface"
            )
        if not cleaned:
            raise CapabilityUnavailable("cannot invoke an empty control")
        # Reuse send_message wholesale (pane/native/remote dispatch + settle +
        # message_sent audit); add a distinct control_invoked event carrying only
        # the control NAME (its first token) — never trailing args, mirroring the
        # never-log-content rule the steer events already hold.
        self.send_message(workspace_id, f"/{cleaned}")
        self._emit("control_invoked", state.id, {"control": cleaned.split(" ", 1)[0]})

    def _pending_capture(self, session_id: str, tool_use_id: str) -> PendingQuestion:
        """The standing captured question for ``session_id``, or raise.

        Reads the hook sidecar and requires a captured question whose
        ``tool_use_id`` still matches. Raises ``QuestionNotPending`` with a reason
        that distinguishes *absent* (nothing captured — already answered/cleared)
        from *stale* (a different question is now pending)."""
        record = ClaudeHook.read(session_id, sidecar_dir=paths.agent_sidecar_dir())
        pending = record.question if record is not None else None
        if pending is None:
            raise QuestionNotPending(
                f"no pending question for session {session_id!r} "
                "(already answered, cancelled, or never asked)"
            )
        if pending.tool_use_id != tool_use_id:
            raise QuestionNotPending(
                f"pending question for session {session_id!r} is {pending.tool_use_id!r}, "
                f"not the requested {tool_use_id!r} (already answered or superseded)"
            )
        return pending

    def respawn(self, workspace_id: str) -> WorkspaceState:
        """Recreate the tmux session for an OFFLINE workspace.

        OFFLINE means the persisted intent is RUNNING but the tmux session
        has vanished externally (the Grove user closed it, the host rebooted,
        a peer killed it). The worktree is intact, so we don't touch git —
        we only spin up a fresh session in the existing worktree and restart
        the agent. Init script is NOT re-run by default (the worktree was
        already initialized at create time); set `init_script.run_on_resume`
        to opt in for parity with `resume`. Root placement never re-runs init
        on respawn even with `run_on_resume` — init for a root workspace is a
        deliberate create-time choice and must not fire unattended in the user's
        real repo root.
        """
        raw = self._store.get(workspace_id)
        if self._launch_backend.provides_pane:
            state = self._reconcile_status(raw)
            ensure_can_respawn(
                state,
                promotable=self._is_promotable(state),
                # A container workspace whose agent outlived its host session
                # reads ACTIVE/IDLE but has exactly the missing viewport
                # this gate exists to rebuild — and rebuilding it now REATTACHES
                # the same agent rather than starting a new one (tmux's `-A`),
                # so the verb is cheap where it used to be destructive.
                sessionless=not tmux.has_session(state.tmux_session),
            )
        else:
            # Headless: no tmux session can vanish to OFFLINE, so the
            # OFFLINE gate doesn't apply — respawn simply relaunches the detached
            # process. Only a live record (RUNNING intent, worktree recreated at
            # create) qualifies; a PAUSED (no worktree) / ERROR one has no runtime
            # to restart, same spirit as `ensure_can_respawn`.
            if raw.status != WorkspaceStatus.RUNNING:
                raise WorkspaceStateError(
                    f"cannot respawn headless workspace {raw.id}: status is "
                    f"{raw.status}, expected running"
                )
            state = raw
        agent = self._cfg.find_agent(state.agent_name)
        if agent is None:
            raise GroveError(
                f"agent {state.agent_name!r} no longer present in config; "
                "edit your config or kill this workspace"
            )
        worktree = Path(state.worktree_path)
        if not worktree.is_dir():
            # Defensive: reconcile should already have flagged this as
            # ORPHANED, not OFFLINE. Treat as a hard error.
            raise GroveError(
                f"worktree {worktree} is missing; cannot respawn — "
                "use kill to clean up the stranded record"
            )

        # Respawn starts a *new* agent session (the old process vanished), so mint
        # a fresh id — a new transcript/remote session, not a continuation.
        # resume() keeps the id; this is the deliberate other branch. Generic
        # agents (no persisted id) stay untracked.
        respawn_session_id: str | None = None
        if state.agent_session_id:
            try:
                respawn_session_id = self._mint_agent_session_id(
                    agent, worktree=state.agent_cwd, title=state.title
                )
            except MewboError as exc:
                self._emit("error", state.id, {"phase": "respawn.agent_session", "error": str(exc)})
                raise
        launch_decoration = self._compose_launch(agent, respawn_session_id, state=state)

        init_changes: dict[str, object] = {}
        if state.placement is Placement.WORKTREE and self._cfg.init_script.run_on_resume:
            # Gated on the persisted runtime, which respawn re-resolves only
            # AFTER this block (`_ensure_runtime(promote=True)`) — so a promotion
            # takes effect from the NEXT init, never retroactively deciding that
            # this run's script was for the other runtime.
            init_enabled = self._init_enabled(state)
            init_log = paths.init_log_path(state.id)
            init_started = _utcnow()
            init = _InitRun()
            if init_enabled:
                init = self._run_init_script(state=state, worktree=worktree, log_path=init_log)
            abort = self._init_abort_detail(
                init, log_path=init_log, rollback="the respawn stopped before relaunching"
            )
            if abort is not None:
                self._emit("error", state.id, {"phase": "respawn.init_script"})
                raise GroveError(abort)
            init_changes = dict(_init_outcome(init_enabled, init.rc, init_started, init_log))

        # Respawn is the promotion verb: a workspace that fell back to
        # the host re-evaluates the decision tree here, and only here.
        state = self._ensure_runtime(state, promote=True)

        # Respawn's OFFLINE gate normally guarantees the session is already
        # gone, so the launch below could just create one. A fallback workspace
        # breaks that assumption: it is ALIVE on the host, and promoting it is
        # precisely a respawn from a live state — so the stale session has to go
        # first, or `create_session` refuses with "session already exists" and
        # the promotion the user was told to run dies after the container is up.
        # Best-effort: a session that is already gone is the normal case.
        if self._launch_backend.provides_pane:
            with contextlib.suppress(TmuxError):
                tmux.kill_session(state.tmux_session)

        try:
            transcript_context = self._launch(state, agent, launch_decoration)
        except Exception as exc:
            self._emit("error", state.id, {"phase": "respawn.tmux", "error": str(exc)})
            raise GroveError(f"could not start tmux session: {exc}") from exc

        # Persisted intent is already RUNNING; refresh the timestamp, the freshly
        # minted session id, the launch-derived transcript context, and any init
        # changes. Status stays RUNNING; the next list()/peek() promotes it to
        # ACTIVE.
        new_state = _replace(
            self._store.get(workspace_id),
            status=WorkspaceStatus.RUNNING,
            updated_at=_utcnow(),
            error_detail=None,
            agent_session_id=respawn_session_id,
            transcript_context=transcript_context,
            # Re-read from the store above, so the runtime fields this respawn
            # just resolved (a promotion, a re-provisioned container) have to be
            # carried across explicitly or the promotion silently reverts.
            runtime=state.runtime,
            runtime_fallback_reason=state.runtime_fallback_reason,
            container=state.container,
            provision_status=state.provision_status,
            provision_duration_ms=state.provision_duration_ms,
            provision_log_path=state.provision_log_path,
            **init_changes,
        )
        self._store.save(new_state)
        self._emit("respawned", state.id)
        return new_state

    def provision_progress(self, workspace_id: str) -> ProvisionProgress:
        """How far along this workspace's container provision is.

        The id seam over :meth:`ProvisionProgress.read`, which is the state
        seam a caller holding a reconciled state should use instead — the
        ``pane_target_for`` / ``latest_todo_for`` split, for the same reason:
        this one re-fetches from the store.

        Answers for a FINISHED provision too, and deliberately so: a user who
        arrives late still wants the log of the build they waited through, and
        a failed one is exactly when its tail is worth reading.
        """
        return ProvisionProgress.read(self._store.get(workspace_id))

    def peek(self, workspace_id: str) -> WorkspacePeek:
        """Rich snapshot for the rail: branch metrics, recent commits, and a
        one-shot agent-pane capture. Recompute it whenever you want a fresh
        frame; nothing here is cached or animated.

        Failures in the underlying git/tmux helpers degrade to zeros / empty
        rather than raise — peek must never break a render loop. Status
        reconciliation (RUNNING → ACTIVE/IDLE/OFFLINE/ORPHANED, see
        `_reconcile_status`) is applied to the returned `state` but never
        persisted — `list()` does the same promotion for the table.
        """
        state = self._reconcile_status(self._store.get(workspace_id))

        try:
            ahead, behind = self._git.ahead_behind(state.branch, state.base_branch)
            added, removed = self._git.diff_stats(state.branch, state.base_branch)
            commits = self._git.recent_commits(state.branch, limit=3)
        except Exception as exc:  # peek is best-effort; never raise
            logger.debug("peek({}) git stats failed: {}", workspace_id, exc)
            ahead = behind = added = removed = 0
            commits = ()

        try:
            dirty = self._git.dirty_file_count(Path(state.worktree_path))
        except Exception as exc:
            logger.debug("peek({}) dirty count failed: {}", workspace_id, exc)
            dirty = 0

        snapshot, snap_at = self._capture_pane(state)

        return WorkspacePeek(
            state=state,
            base_ahead=ahead,
            base_behind=behind,
            diff_added=added,
            diff_removed=removed,
            dirty_files=dirty,
            recent_commits=commits,
            agent_snapshot=snapshot,
            snapshot_taken_at=snap_at,
        )

    def commits(self, workspace_id: str) -> tuple[CommitSummary, ...]:
        """Comprehensive commit history for a workspace, newest first.

        ``git log base..branch`` — every commit done in this workspace
        since the branch diverged from ``base_branch``. Distinct from
        ``peek.recent_commits`` which walks all of branch history (no
        fork-point filter) and is capped at 3 for the TUI's tight rail.

        Best-effort: degrades to ``()`` on git failure, mirroring the
        peek-helpers' never-raise contract for read paths. The daemon's
        ``GET /workspaces/{id}/commits`` is the wire shape consumers
        receive; the TUI doesn't call this method today (its rail keeps
        the truncated summary).
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        try:
            return self._git.branch_commits(state.branch, state.base_branch)
        except Exception as exc:
            logger.debug("commits({}) git failed: {}", workspace_id, exc)
            return ()

    def peek_pane(
        self, workspace_id: str, *, agent: str = ""
    ) -> tuple[str | None, datetime | None]:
        """Tmux-only fast path: just the agent-pane snapshot. Used by the
        rail's fast pane-tick (~250 ms) so we don't redo git ahead/behind
        and diff stats — those move at human pace, the pane moves at agent
        pace. Best-effort: returns (None, None) on any failure or for
        non-live workspaces.

        ``agent`` names one of the ADDITIONAL agents a container may host;
        empty is the workspace's own agent and is unchanged. Naming one
        on a workspace that cannot host several is a typed refusal rather than a
        silent empty snapshot — this is the one place best-effort would be
        actively misleading, because "that agent printed nothing" and "there is
        no such agent" are different answers a user acts on differently.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        if agent:
            self._container_tmux_or_refuse(state, session=agent)
        return self._capture_pane(state, session=agent)

    def primary_transcript(self, workspace_id: str) -> tuple[Path, ...]:
        """Transcript file(s) for the workspace's agent session, or ``()`` if untracked.

        Resolves the adapter from the agent's ``kind`` and the persisted session
        id, then asks it to locate the file(s) under the worktree cwd. Read-only
        and best-effort — the adapter never raises. Empty for a generic/shell
        agent (no session id), a legacy record, or before the transcript is first
        written (the STARTING window). The ``ActivityService`` builds on
        this to parse activity.

        A tuple (point-in-time snapshot), matching ``list_local_branches`` — the
        files on disk may change after the call, so an immutable return can't
        mislead the caller about live state.

        Honors ``state.transcript_context``: a session launched under
        a different runtime context records a cwd/config dir the host reader
        can't guess, so an override substitutes that recorded cwd and scopes the
        adapter's config-dir env var for this one call. No override (the default)
        is byte-for-byte the behavior that predates the override.

        Kind comes from ``effective_kind`` — the persisted-``agent_kind``-wins
        rule every other read path follows. A bare ``find_agent`` lookup fell to
        ``"generic"`` (blank result) whenever the agent wasn't in this manager's
        config, which ``builtin_agents: false`` and repo-scoped rosters make far
        more reachable than the legacy-record case it was written for.
        """
        state = self._store.get(workspace_id)
        if not state.agent_session_id:
            return ()
        kind = self.effective_kind(state)
        ctx = state.transcript_context
        cwd = Path(ctx.agent_cwd) if ctx is not None else Path(state.worktree_path)
        with self.transcript_config_dir_scope(kind, ctx.config_dir if ctx is not None else None):
            return tuple(get_adapter(kind).locate_transcripts(cwd, state.agent_session_id))

    @staticmethod
    @contextlib.contextmanager
    def transcript_config_dir_scope(kind: str, config_dir: str | None) -> Iterator[None]:
        """Point ``kind``'s config-dir env var at ``config_dir`` for one read.

        Filesystem adapters resolve ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``
        ambiently from ``os.environ`` on every call — never a parameter, since
        threading one through would be adapter parsing, off-limits here —
        so honoring a workspace's ``transcript_context.config_dir`` override
        means scoping the process env around the read itself. A no-op (and
        the true default-behavior path) when there is no override
        (``config_dir is None``) or ``kind`` has no config-dir env
        (``TranscriptContext.CONFIG_DIR_ENV`` — mewbo/generic). Restores the
        prior value (or its absence) on exit. Best-effort like every adapter
        read; callers should hold this for the shortest span — one locate/read
        call, never across a whole request — since the env is process-global.

        Reads the same kind→var map ``TranscriptContext.for_launch`` writes
        from, so the scope can never look up a var the launch didn't record.
        """
        var = TranscriptContext.CONFIG_DIR_ENV.get(kind)
        if config_dir is None or var is None:
            yield
            return
        prior = os.environ.get(var)
        os.environ[var] = config_dir
        try:
            yield
        finally:
            if prior is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prior

    def transcript_scope(self, state: WorkspaceState) -> contextlib.AbstractContextManager[None]:
        """THE scope every adapter read for ``state`` must run inside.

        Wrap each adapter call that resolves a transcript, a rollout, or a
        config-dir-derived surface in this. The workspace's agent may have been
        launched under a pinned ``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` that
        this process — the daemon, the TUI — does not share, and every
        filesystem adapter resolves that env *ambiently* at call time. An
        unscoped read therefore searches the reader's own profile and finds
        nothing.

        **Resolves the kind itself** rather than taking one, so no caller can
        scope a read with a kind that disagrees with the workspace's own
        (``effective_kind`` is already the single answer to "which adapter is
        this workspace's"). Pass the state; there is nothing else to get wrong.

        Returns a context manager instead of being one so the unpinned case —
        no ``transcript_context``, the overwhelming majority — is a bare
        ``nullcontext``: the hot poll path allocates nothing else and behaves
        byte-for-byte as it did before any of this existed. The env it mutates
        is **process-global**, so hold the returned scope around the adapter
        call ONLY, never across a whole request or a whole workspace.

        Why this is a public seam rather than four inline ``with`` blocks: the
        invariant was independently forgotten at four separate read sites
        (the fleet drill-in 404, an empty todo posted to a Gitea issue,
        the wrong profile's slash commands, a blank ``current_model``). Four
        bespoke blocks would distribute the same forgettable rule four more
        ways; one named seam gives it a home a new read path can find.
        """
        ctx = state.transcript_context
        if ctx is None:
            return contextlib.nullcontext()
        return self.transcript_config_dir_scope(self.effective_kind(state), ctx.config_dir)

    def _pinned_session_materialized(self, agent: AgentSpec, state: WorkspaceState) -> bool:
        """Whether ``state``'s pinned session already has a transcript on disk.

        The materialization test the unpause path gates its resume-vs-mint choice
        on: a session is materialized when the adapter locates at least one
        transcript for the pinned id, scanning the ``transcript_scan_cwds`` union
        so a nested project's root-recorded transcript still counts — or,
        with a ``transcript_context`` override, the one recorded cwd it
        names instead, under its scoped config dir. No pinned id → not
        materialized (nothing to continue). Best-effort like every adapter read.
        """
        if not state.agent_session_id:
            return False
        adapter = get_adapter(agent.kind)
        ctx = state.transcript_context
        config_dir = ctx.config_dir if ctx is not None else None
        with self.transcript_config_dir_scope(agent.kind, config_dir):
            return any(
                adapter.locate_transcripts(cwd, state.agent_session_id)
                for cwd in state.transcript_scan_cwds
            )

    def effective_kind(self, state: WorkspaceState) -> AgentKind:
        """The adapter kind for ``state`` — persisted at create, else config.

        Prefers the kind persisted at create (resolves a repo-scoped agent the
        daemon's global config never loaded); falls back to a config lookup for
        legacy records written before ``agent_kind`` existed, then ``generic``.
        THE single definition, so the remap/create kind gate and the dashboard
        can't disagree about what a workspace runs. ``ActivityService`` used to
        carry a verbatim copy (``_effective_kind``); it was deleted in favour of
        calling this, because two implementations of "which adapter reads this
        workspace" is exactly the drift that kind-scoping was cleaning up.
        Public because ``SessionExplorer`` also restricts its per-workspace scan
        to this one kind, so the picker never offers a foreign-kind session
        the remap gate below would reject.
        """
        if state.agent_kind is not None:
            return state.agent_kind
        agent = self._cfg.find_agent(state.agent_name)
        return agent.kind if agent is not None else "generic"

    def _mint_agent_session_id(
        self,
        agent: AgentSpec,
        *,
        worktree: Path,
        title: str,
        model: str | None = None,
        resume_session_id: str | None = None,
    ) -> str | None:
        """Mint the session id for a NEW agent run — the single fork create()
        and respawn() share so the verbs can't drift; resume() deliberately
        skips it (continue = keep the persisted id; mewbo re-engagement is a
        follow-up surface).

        Two opposite minting directions, worth naming:

        - **claude_code** (any local CLI): CLIENT-minted — Grove generates the
          UUID and launches ``--session-id <uuid>``, so the transcript path is
          known by construction. Generic agents mint nothing (empty decoration).
        - **mewbo**: SERVER-minted — ``POST /api/sessions`` creates the remote
          session anchored to the worktree ``cwd`` (which the API validates is
          an existing directory — callers therefore invoke this only after the
          worktree is on disk) and the RETURNED id is what Grove persists.

        ``resume_session_id`` short-circuits both: adopt the chosen id
        verbatim, no mint. The kind-support check already ran at create()'s gate
        (before any side effect), so reaching here means a resumable kind. This
        is why even codex — which normally mints nothing and relies on fs
        discovery — becomes tracked by construction on a resume: its returned id
        is persisted as ``agent_session_id``.

        Local kinds never raise; the remote kind raises the typed ``MewboError``
        and the caller treats it like a fail_fast init (loud, transactional).
        """
        if resume_session_id is not None:
            return resume_session_id
        if agent.kind == "mewbo":
            # `model` (create-only) forwards to the remote session-create —
            # mewbo has no launch `--model` flag (the model is server-side), so
            # this is the one place a per-create model choice can reach it.
            return self._mewbo().create_session(cwd=str(worktree), title=title, model=model)
        session_id = WorkspaceIdentity.new_session_id()
        return session_id if get_adapter(agent.kind).launch_decoration(session_id) else None

    def _devcontainer(self) -> DevcontainerCli:
        """The devcontainer CLI: injected (tests) or built once — the `_mewbo` shape.

        Needed because the multi-agent verbs reach the CLI
        DIRECTLY rather than through the provisioner or the launch backend, both
        of which already tolerate a `None` and build their own.
        """
        if self._devcontainer_cli is None:
            self._devcontainer_cli = DevcontainerCli()
        return self._devcontainer_cli

    def _mewbo(self) -> MewboClient:
        """The Mewbo REST client: injected (tests) or built once from config."""
        if self._mewbo_client is None:
            self._mewbo_client = MewboClient(self._cfg.mewbo)
        return self._mewbo_client

    def _native_steer(self) -> native.NativeSteerClient:
        """The paneless-steering client: injected (tests) or built once.

        Default is the channel-backed :class:`~grove.core.native.ChannelSteerClient`
        — the ``_mewbo`` pattern for the native (non-tmux) delivery path.
        """
        if self._native_steer_client is None:
            self._native_steer_client = native.ChannelSteerClient()
        return self._native_steer_client

    def _launch(
        self, state: WorkspaceState, agent: AgentSpec, decoration: _Argv
    ) -> TranscriptContext | None:
        """Start the agent for ``state`` and return the transcript context to persist.

        The single launch site all three verbs (create/resume/respawn) go
        through, so the persisted context is written ONCE and can't drift — the
        reason it lives here rather than at each call site. Persisting on every
        launch (not just create) is what keeps the context honest: an operator
        who adds, changes, or removes an ``AgentSpec.env`` config-dir pin has it
        picked up at the next relaunch, including the clear-back-to-``None``
        case. Raises whatever the backend raises — each caller keeps its own
        error phase and rollback around this.

        The context itself is the BACKEND's answer, stored verbatim: only
        the runtime that launched the agent knows how its namespace maps onto
        this host's filesystem, so any post-processing here would be the
        manager re-acquiring policy it cannot hold correctly. ``None`` — a
        runtime with nothing reachable to record — persists as ``None``, exactly
        as the old ``host_namespace=False`` gate did, because the read side acts
        on whatever is stored and a wrong context is worse than none.
        """
        backend = self._backend_for(state)
        spec = self._launch_spec(state, agent, decoration)
        # So the path this launch is about to NAME in the agent's env is a plain
        # file write, with no `mkdir` step to spend a line of the instructions
        # string on. Best-effort, and free after the first launch.
        PhaseFile.ensure_dir(state.worktree_path)
        # Before the agent runs, never after: a stale record from the
        # PREVIOUS launch would make the relaunched agent read as dead the
        # instant it started — and `respawn` is the documented remedy for
        # exactly the failure this records, so poisoning it would take the fix
        # and the way out with the same stone.
        if spec.exit_record is not None:
            AgentExit.prepare(spec.exit_record)
        backend.launch(spec)
        return backend.transcript_context(spec)

    def _launch_spec(
        self, state: WorkspaceState, agent: AgentSpec, decoration: _Argv
    ) -> LaunchSpec:
        """Assemble the `LaunchSpec` handed to the launch backend.

        One builder for all three launch sites (create/resume/respawn) so the
        spec can't drift between them. `agent_cwd` roots both the session and the
        layout windows: the worktree/branch anchor at the repo root, the agent
        session only *starts* here (the nested-cwd split). The composed
        `decoration` comes from `_compose_launch` + the adapter — this only
        packages the assembled command as structured data for the backend.
        """
        return LaunchSpec(
            session_name=state.tmux_session,
            cwd=state.agent_cwd,
            command=agent.command,
            decoration=tuple(decoration),
            env=self._launch_env(state, agent),
            env_unset=agent.env_unset,
            cfg=self._cfg,
            # The worktree ROOT, distinct from the agent cwd for a nested
            # project. Passing the cwd for both collapses every nested project
            # to the mount root: a container backend derives the in-container
            # path from the offset between them, and equal values make that
            # offset zero. Host backends root their windows at `cwd`.
            worktree=Path(state.worktree_path),
            kind=agent.kind,
            container=state.container,
            share_plan=self._share_plan(state, agent),
            # A HOST path even for a container launch: the pane runs
            # `devcontainer exec` on this machine and that propagates the
            # in-container exit code, so the record lands where the read side
            # already looks and needs no mount of its own.
            exit_record=paths.agent_exit_path(state.id),
        )

    def _launch_env(
        self, state: WorkspaceState, agent: AgentSpec, *, agent_slot: str | None = None
    ) -> dict[str, str]:
        """The hermetic env an agent of this workspace launches under.

        Split out of `_launch_spec` because it has a SECOND consumer that is not
        a workspace launch: an additional agent started inside an existing
        workspace's container must cross the boundary under
        exactly the same env — the same config-dir pointer, the same
        instrumentation passthroughs, the same user dotenv — or it writes its
        transcript somewhere the read side never looks and reports to nowhere.
        One definition, two callers, no way for them to disagree.

        *agent_slot* names that second consumer's in-container tmux session, and
        it exists for exactly one field: the phase file is per AGENT, not per
        workspace, and this is the one composition both launch roads pass
        through — so publishing it here is what makes a container's extra agents
        report independently, and a container inherits the value through
        `--remote-env` with nothing else to wire.
        """
        # Compose the launch env: the agent's own `env` plus the opt-in
        # instrumentation passthroughs derived at the boundary. Telemetry
        # contributes the generic OTLP endpoint/headers (LangFuse), and —
        # only when that endpoint actually resolves — the adapter's OWN native
        # telemetry switch (`telemetry_env`; provider boundary: Claude Code flips
        # its exporter on, Codex/mewbo/generic no-op), so the tool streams its own
        # usage/cost to LangFuse with no per-agent hand-wiring. Gating the switch
        # on a resolved endpoint means a tool never enables an exporter pointed at
        # nowhere. The gateway proxy points the tool at Grove's loopback
        # proxy. All derive from config + the process env, never stored pre-built;
        # a disabled knob yields `{}` (no-op default). Agent `env` wins any
        # collision (the explicit user override).
        telemetry = (
            self._cfg.telemetry.derive_env(os.environ)
            if agent.kind in self._cfg.telemetry.passthrough_kinds
            else {}
        )
        if telemetry.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            telemetry = {**telemetry, **get_adapter(agent.kind).telemetry_env()}
        share = self._share_plan(state, agent)
        return {
            # Lowest precedence deliberately: this is a convenience Grove offers
            # the agent, not an isolation contract like the config-dir pointer
            # below, so an operator who names it explicitly wins.
            **self._phase_env(state, agent_slot),
            **self._brief_env(state, agent),
            **telemetry,
            **self._cfg.proxy.proxy_env(agent.kind),
            # A container's agent inherits no shell, so anything the host
            # exports for free has to be named. Placed BELOW the share
            # env deliberately: the share's config-dir pointer is Grove's
            # isolation contract and a user's dotenv must not be able to
            # redirect it, while `agent.env` stays the most specific override
            # and still wins. Empty for a host workspace.
            **self._container_env(state).values,
            # The other half of the agent-config share: the provisioner
            # bind-mounts the host's settings/skills/credentials into Grove's
            # per-workspace config dir, and this points the agent's own
            # config-dir env var at it. Mount without env is a share the agent
            # never looks at; env without mount points it at an empty directory
            # — so both come from ONE `share_plan` call, never two derivations.
            # Only for a container workspace: on the host that var would
            # redirect the agent away from the user's real config.
            **(dict(share.env) if share is not None else {}),
            **agent.env,
        }

    def _briefed_by_hook(self, state: WorkspaceState, agent: AgentSpec) -> bool:
        """Can THIS launch's hook deliver the first-turn brief?

        Three facts, and each of them is a hard no on its own. Only Claude
        Code's ``UserPromptSubmit`` hook injects a command's stdout into the
        model's context, so no other kind has the channel at all. `hooks.enabled`
        is what installs the settings file the hook lives in. And a container's
        agent has no ``grove-agent-hook`` on its ``PATH``, so the rendered
        command takes its spool fallback, which moves bytes to the host and
        prints nothing — a real delivery failure that looks exactly like a
        working feature from here.

        Everything that answers ``False`` falls to the create-time prompt
        instead (:meth:`_brief_prompt`, which carries its own exclusions), so
        the option is not silently inert for a kind Grove installs no hook for.
        Read off the PERSISTED runtime like every other runtime-shaped decision,
        so a container create that fell back to the host answers correctly for
        free.
        """
        return (
            agent.kind == "claude_code"
            and self._cfg.hooks.enabled
            and state.runtime is not Runtime.CONTAINER
        )

    def _brief_env(self, state: WorkspaceState, agent: AgentSpec) -> dict[str, str]:
        """Name the rendered brief for a launch whose hook can actually read it.

        The env var is the whole per-workspace mechanism: the hook settings file
        is host-global and shared by every workspace on the machine, so the
        opt-out cannot live there, while this composition is per workspace and
        per launch and the hook process inherits it from the agent.

        Rendering happens HERE rather than in `_ensure_control_files` so the
        file and the variable can never disagree — an unwritable config dir
        yields no variable, instead of pointing the hook at a file it will fail
        to read on every prompt of every session.
        """
        if not (state.brief and self._briefed_by_hook(state, agent)):
            return {}
        rendered = AgentBrief.render(paths.agent_brief_path())
        return {} if rendered is None else {AgentBrief.PATH_ENV: str(rendered)}

    def _brief_prompt(
        self, state: WorkspaceState, agent: AgentSpec, initial_prompt: str | None
    ) -> str | None:
        """The create-time prompt, carrying the brief where no hook can deliver it.

        The fallback for codex and for every containerized agent. It costs no
        extra turn — the prompt already rides the launch — and it lands on turn
        one, which is the whole point of the brief.

        **It cannot brief a create that had no prompt**, and inventing one is
        worse than the gap: an agent handed the brief alone as its task starts a
        turn about nothing. So a workspace created with no initial prompt on a
        kind with no hook is genuinely not briefed — the honest answer rather
        than a silent one.

        **And it inherits `_compose_launch`'s own kind policy for prompts**,
        which is narrower than this seam: the prompt rides the launch as a
        trailing positional for `claude_code` only, and reaches mewbo through
        the remote dispatch. A codex workspace's initial prompt is dropped
        there today — a pre-existing gap in prompt delivery, not in the brief —
        so codex ends up unbriefed until that is closed.

        **A remote-steered kind is excluded outright**, not for want of a
        channel — mewbo's `/message` would carry it perfectly — but because the
        agent is not IN the workspace: it runs on a backend with no worktree, no
        phase file and no `working-in-grove` skill installed, so the brief would
        point it at a contract it cannot keep.
        """
        if (
            not initial_prompt
            or not state.brief
            or agent.kind in _REMOTE_STEERED_KINDS
            or self._briefed_by_hook(state, agent)
        ):
            return initial_prompt
        return f"{AgentBrief.TEXT}\n{initial_prompt}"

    @staticmethod
    def _phase_env(state: WorkspaceState, agent_slot: str | None) -> dict[str, str]:
        """Where THIS agent reports its task phase, absolute, in its own namespace.

        Grove knows the workspace and
        the agent slot at launch, so it composes the path and the agent
        interprets nothing — which is what makes two ROOT workspaces (one shared
        repo root) and two agents in one container report independently, and
        what removes the nested-``project_subpath`` ambiguity that a
        cwd-relative instruction can never lose.

        The path stays under the worktree, so a containerized agent's write is
        bind-mount visible on the host with no new mount; only the ROOT is
        re-anchored, off the container's own reported workspace folder rather
        than a mount root Grove guessed. A container that never reported
        one gets NO variable at all rather than a host path: a foreign-namespace
        path is worse than none, because the agent would act on it, and
        the documented fallback still names a file Grove reads.
        """
        path = PhaseFile.path_for(
            state.worktree_path, PhaseFile.key_for(state.id, agent=agent_slot)
        )
        container = state.container
        if container is None:
            return {PhaseFile.PATH_ENV: str(path)}
        if not container.remote_workspace_folder:
            return {}
        return {
            PhaseFile.PATH_ENV: container.container_path(
                worktree=Path(state.worktree_path), path=path
            )
        }

    def _compose_launch(
        self,
        agent: AgentSpec,
        session_id: str | None,
        *,
        state: WorkspaceState,
        initial_prompt: str | None = None,
        model: str | None = None,
        resume: bool = False,
    ) -> _Argv:
        """Full argv appended to the agent command at launch, for a known session id.

        `state` is here for the control files alone: every Grove-written
        control path is resolved through the workspace's OWN launch backend, so
        a flag can only ever name a file the launched agent can actually open.
        Read from the persisted runtime like every other runtime-shaped decision
        (`_backend_for` / `_share_plan`), never from `cfg.container.enabled`.

        Composes the adapter's base decoration (`--session-id <uuid>` for Claude
        Code) with the opt-in status hook: when `cfg.hooks.enabled` and the agent
        is `claude_code`, append `--settings <grove-hooks-settings>` so the hook
        pushes precise lifecycle status into a sidecar — additive, never
        touching the user's own `.claude/settings.json`. Empty for a generic/shell
        agent or a legacy record with no session id. Centralizes the composition so
        create/resume/respawn can't drift.

        `model` (create-only) appends the adapter's model flag
        (`--model <id>` for claude_code / codex; `[]` for mewbo / generic) so a
        per-create model choice reaches the tool — forwarded verbatim, never
        interpreted (the provider boundary). It rides even when `session_id` is
        None (codex mints no id but still honors `--model`).

        `initial_prompt` (create-only) rides the launch as a trailing
        POSITIONAL arg on a claude_code argv (`claude … "<prompt>"` boots already
        working on it — race-free, unlike post-boot pane typing). It is appended
        last so it stays the positional after every flag, and goes through the
        SAME `shlex.quote` path in `tmux.build_workspace_layout` as the rest of the
        decoration — no second quoting site. Ignored for non-claude_code kinds: a
        generic shell has no prompt concept, and mewbo carries `[]` here and is
        re-engaged through its API instead (manager `create()`).

        `resume` (create-only) flips the base decoration to the tool's
        resume form (`claude --resume <id>` / `codex resume <id>`) — the id is an
        existing session to CONTINUE, not a fresh one to mint. Everything after
        the base decoration (hooks `--settings`, `--model`, the trailing prompt
        positional) is unchanged, so a resume launch composes identically bar the
        one flag.

        `agent.tools_offline` appends the adapter's network-tool-gating
        flags (`--disallowedTools WebFetch,WebSearch` for claude_code; a
        no-network sandbox for codex). Unlike `model`/`initial_prompt` it is not
        a per-create request field but a persisted `AgentSpec` toggle, so it
        rides every launch path (create/resume/respawn) uniformly.
        """
        adapter = get_adapter(agent.kind)
        decoration: _Argv = []
        # Every Grove control file below crosses into the runtime's namespace
        # through ONE seam. Resolved once here, per launch: the backend
        # knows how its namespace maps onto this host, and the share plan is the
        # mount table it answers from. `_share_plan` is pure and deterministic,
        # so asking it here as well as in `_launch_spec` is cheaper and safer
        # than threading one instance through all three verbs (its own docstring
        # makes that call).
        backend = self._backend_for(state)
        share_plan = self._share_plan(state, agent)
        if session_id is not None:
            decoration = adapter.launch_decoration(session_id, resume=resume)
            if decoration and agent.kind == "claude_code" and self._cfg.hooks.enabled:
                settings = self._control_flag(
                    self._settings_file(share_plan), backend=backend, share_plan=share_plan
                )
                if settings is not None:
                    decoration = [*decoration, "--settings", settings]
        # `model` rides the launch INDEPENDENTLY of session correlation:
        # Codex mints no id (empty `launch_decoration`) yet still honors
        # `--model`, so it's appended whether or not a session id exists. Adapters
        # with no launch-time model flag (mewbo, generic) return [] — a no-op.
        if model:
            decoration = [*decoration, *adapter.model_decoration(model)]
        # `--channels` declares Grove as a native Claude Code channel so a
        # RUNNING session can act on messages Grove delivers (and relay a
        # permission decision). Opt-in + auth-gated research preview, claude_code
        # only, mirroring the hook `--settings` append above — additive, never
        # touching the user's own config, a no-op when `cfg.channels.enabled` is
        # False. Appended BEFORE the initial-prompt positional so that stays last.
        if agent.kind == "claude_code" and session_id is not None and self._cfg.channels.enabled:
            channel_settings = self._control_flag(
                self._ensure_channel_settings(), backend=backend, share_plan=share_plan
            )
            if channel_settings is not None:
                decoration = [*decoration, "--channels", channel_settings]
        # `tools_offline` is a persisted per-agent policy, not a per-create
        # request field like `model` — it rides every launch (create/resume/
        # respawn) whenever the configured agent opts in, forwarded verbatim
        # (provider boundary: Grove never decides which tools are "network").
        if agent.tools_offline:
            decoration = [*decoration, *adapter.offline_decoration()]
        # `--permission-prompt-tool` routes Claude Code's "allow this tool
        # call?" gate to a Grove-hosted MCP tool that answers allow/deny JSON — the
        # native replacement for a human typing a permission answer into the pane,
        # essential for a paneless/headless session with no TTY to block on. Opt-in
        # + claude_code only, mirroring the hook `--settings` / channel `--channels`
        # appends above: additive, never touching the user's own config, a no-op
        # when `cfg.permission.enabled` is False. Appended BEFORE the initial-prompt
        # positional so that stays last.
        if agent.kind == "claude_code" and session_id is not None and self._cfg.permission.enabled:
            perm_config = self._control_flag(
                self._ensure_permission_settings(), backend=backend, share_plan=share_plan
            )
            if perm_config is not None:
                decoration = [
                    *decoration,
                    "--mcp-config",
                    perm_config,
                    "--permission-prompt-tool",
                    permission.permission_tool_ref(),
                ]
        # `initial_prompt` stays the trailing POSITIONAL, claude_code only, after
        # every flag (incl. --model) — same race-free launch path as before.
        if agent.kind == "claude_code" and session_id is not None and initial_prompt:
            decoration = [*decoration, initial_prompt]
        return decoration

    def _control_flag(
        self,
        path: Path | None,
        *,
        backend: LaunchBackend,
        share_plan: AgentSharePlan | None,
    ) -> str | None:
        """A Grove control file as the launched agent will see it, or ``None``.

        ``None`` means OMIT the flag: for a container runtime an untranslated
        host path names a file the agent cannot open, and Claude Code treats a
        missing ``--settings`` as fatal — so emitting it costs the whole
        workspace, while dropping it costs only the feature that file carries.

        The one place a control path crosses into a launch argv, which is what
        makes the fix GENERAL rather than a patch for ``--settings``: the two
        opt-in flags (``--channels``, ``--mcp-config``) are off by default and
        would otherwise each have become the same bug the first time an operator
        enabled one in a container. A `None` input (the writer failed, already
        best-effort) short-circuits to `None` so callers keep one check, not two.
        """
        if path is None:
            return None
        return backend.control_path(path, share_plan=share_plan)

    def _ensure_control_files(self) -> None:
        """Render every Grove control file BEFORE a mount plan can name it.

        A sequencing fix, not a relaxation of the mount rule. ``AgentSharePlan``
        drops a bind source that does not exist, and it is right to: Docker
        would materialize a missing one as a **root-owned directory at the exact
        host path Grove's own writer later needs**, poisoning that path for
        every future launch on the machine. But the writers below run at LAUNCH
        while the plan is built at PROVISION, and on ``create`` provision comes
        first — so on a machine where no launch had ever written these, the very
        first container create planned nothing, and the workspace came up with
        no hooks, no channel, no permission tool. It self-heals on the second
        create, which is precisely why no developer machine ever saw it and a
        fresh install, a CI runner or a container-only user hits it immediately.

        Rendering here rather than moving the writers is what keeps ONE
        definition of each file: the launch still rewrites them (so a Grove
        upgrade self-heals a stale render), and it rewrites them **in place**
        via ``write_text``, so a container already bound to that inode sees the
        new content rather than a stale file behind a broken mount.

        Gated on the same config switches the launch composes on, so a disabled
        feature still writes nothing and mounts nothing. Best-effort like each
        writer: a failure logs and costs that one feature, never the workspace.
        """
        if self._cfg.hooks.enabled:
            self._ensure_hook_settings()
            self._ensure_container_settings()
        if self._cfg.channels.enabled:
            self._ensure_channel_settings()
        if self._cfg.permission.enabled:
            self._ensure_permission_settings()

    def _decor_plan(self) -> DecorPlan:
        """Grove's container decor, resolved from config against what is on disk.

        Pure enough to call from either side of a provision: resolving a payload
        is a handful of `exists` probes, and an absent bundle plans nothing. Both
        callers need the same answer for different halves of it — the control
        file wants the statusline command, the provisioner wants the mount and
        the tmux config path — so it is resolved once here rather than twice
        with two chances to disagree.
        """
        decor = self._cfg.container.decor
        return DecorPlan.from_config(
            enabled=decor.enabled,
            statusline=decor.statusline,
            tmux_conf=decor.tmux_conf,
            payload=DecorPayload.resolve(decor.payload),
        )

    def _settings_file(self, share_plan: AgentSharePlan | None) -> Path | None:
        """Which rendered settings file this launch should be handed.

        A container gets the merged variant; a host launch gets the file it has
        always got, byte-identical. The two are rendered by separate writers on
        purpose — sharing one would put a container-only path in front of a host
        agent, silently replacing whatever statusline that user configured.

        The discriminator is the SHARE PLAN, which the caller already holds and
        which already means exactly this: `_share_plan` returns `None` on the
        host and a plan for a container, gated on the persisted runtime rather
        than on `container.enabled` — so a container workspace that fell back to
        the host answers correctly here for free. No new capability sentinel: a
        boolean could only say "my paths are meaningless to you", which is the
        very reason the old one was replaced by `control_path`, and this needs no
        second way to ask a question the manager can already answer.
        """
        if share_plan is None:
            return self._ensure_hook_settings()
        return self._ensure_container_settings()

    def _ensure_container_settings(self) -> Path | None:
        """Write the CONTAINER variant of the hook settings; return its path.

        Not a second feature — the same hook payload plus the one key that
        cannot live in the shared file. ``--settings`` is single-valued (the CLI
        keeps the last occurrence), so Grove cannot pass a container-only layer
        alongside the hook layer; and the hook file is deliberately
        namespace-agnostic, which is exactly what a ``statusLine`` naming a path
        under ``/grove`` is not. Merging here keeps the host file byte-identical
        to what it has always been, so a user's own statusline is never touched.

        Written unconditionally under ``hooks.enabled`` rather than under the
        decor switches, because the mount table names its sources at PROVISION
        while these run at LAUNCH, and on `create` provision comes first: a file
        that does not exist yet is dropped from the plan, and a dropped control
        file is a silently missing feature. With decor off the payload is simply
        the hook settings, which is correct and inert.

        Best-effort like every other control writer: a failure logs and costs
        the statusline, never the workspace.
        """
        path = paths.agent_container_settings_path()
        daemon_url = self._cfg.hooks.daemon_url or DEFAULT_DAEMON_LOOPBACK_URL
        payload: dict[str, object] = dict(ClaudeHook.settings(daemon_url=daemon_url))
        statusline = self._decor_plan().statusline_command
        if statusline:
            payload["statusLine"] = {"type": "command", "command": statusline}
        try:
            paths.ensure_dir(path.parent)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write container settings; launching without decor: {}", exc)
            return None
        return path

    def _ensure_hook_settings(self) -> Path | None:
        """Write Grove's hook-only Claude Code settings file; return its path.

        Best-effort: a write failure logs and returns `None` so the agent still
        launches (just without push status — graceful degradation). Rewritten each
        launch so a Grove upgrade that changes the hook set self-heals.

        `cfg.hooks.daemon_url` overrides where the hook's http handler POSTs;
        unset falls back to the built-in loopback, so the rendered file is
        byte-identical to before the knob existed.
        """
        path = paths.agent_hooks_settings_path()
        daemon_url = self._cfg.hooks.daemon_url or DEFAULT_DAEMON_LOOPBACK_URL
        try:
            paths.ensure_dir(path.parent)
            path.write_text(
                json.dumps(ClaudeHook.settings(daemon_url=daemon_url), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("could not write hook settings; launching without push status: {}", exc)
            return None
        return path

    def _ensure_channel_settings(self) -> Path | None:
        """Write Grove's channel settings file; return its path.

        The channel counterpart of `_ensure_hook_settings`: rendered fresh each
        launch (so a Grove upgrade self-heals the declaration) and best-effort —
        a write failure logs and returns `None`, so the agent still launches,
        just without the channel (graceful degradation, exactly like the hook).
        The rendered shape (`channel.channel_settings`) declares Grove's channel
        MCP server; the manager only owns *when* to write + append the flag.
        """
        path = channel.channel_settings_path()
        try:
            paths.ensure_dir(path.parent)
            path.write_text(json.dumps(channel.channel_settings(), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write channel settings; launching without channel: {}", exc)
            return None
        return path

    def _ensure_permission_settings(self) -> Path | None:
        """Write Grove's permission MCP-config file; return its path.

        The permission counterpart of `_ensure_channel_settings`: rendered fresh
        each launch (so a Grove upgrade self-heals the registration) and
        best-effort — a write failure logs and returns `None`, so the agent still
        launches, just without the prompt tool (graceful degradation, exactly like
        the hook/channel writes). The rendered shape
        (`permission.permission_mcp_config`) registers Grove's permission MCP
        server; the manager only owns *when* to write + append the flags.
        """
        path = permission.permission_mcp_config_path()
        try:
            paths.ensure_dir(path.parent)
            path.write_text(
                json.dumps(permission.permission_mcp_config(), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(
                "could not write permission config; launching without prompt tool: {}", exc
            )
            return None
        return path

    def pane_target(self, workspace_id: str) -> str | None:
        """Resolve the tmux target the rail should capture / resize for `workspace_id`.

        Fetches and reconciles fresh from the store — the right call when a
        caller has only an id. A caller that already holds a `WorkspaceState`
        reconciled THIS tick (`ActivityService.poll_once`/`snapshot`, off
        `mgr.list()`) must use `pane_target_for` instead: re-reconciling here
        redid a full `_reconcile_status` (has_session + list_windows +
        pane_activity_seconds_ago) that `list()` had just paid for one line
        earlier — 3 redundant tmux forks per workspace per poll, ~4 once the
        target resolution's own `list_windows` call is added, at fleet
        scale (24 workspaces, every poll).

        Policy (in order):
        1. Workspace not RUNNING → ``None``.
        2. The agent runs under a tmux INSIDE its container → that
           session, reached through the container. It is the agent's OWN pane
           rather than the host viewport onto it.
        3. Configured ``agent_window_name`` exists → ``"<session>:agent"``.
        4. Any non-``shell`` window exists → ``"<session>:<first-non-shell>"``
           (a renamed agent, an init window, etc. — usually where the live work is).
        5. Only ``shell`` exists → ``"<session>:shell"`` (last-resort fallback
           so the rail at least shows the bare prompt).
        6. Session reports no windows at all → ``None``.

        Best-effort: ``tmux.list_windows`` never raises, so this is safe to
        call from the peek hot path. Returning ``None`` is the contract for
        "no live pane to look at"; callers should render the empty state.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        return self._pane_target(state)

    def pane_target_for(self, state: WorkspaceState) -> str | None:
        """Resolve the tmux target for a `WorkspaceState` ALREADY reconciled this
        tick — the `pane_target(workspace_id)` sibling for a caller
        (`ActivityService._workspace_activity`) that got its state from `list()`
        and would otherwise pay for a second full reconciliation to ask the
        same question `list()` already answered.
        """
        return self._pane_target(state)

    def _pane_target(self, state: WorkspaceState) -> str | None:
        pane = self._agent_pane(state)
        return pane.display if pane is not None else None

    def _agent_pane(self, state: WorkspaceState, *, session: str = "") -> tmux.TmuxPane | None:
        """THE pane-resolution policy site — where the agent's pane is, and how to reach it.

        *session* names an ADDITIONAL in-container agent; empty —
        every caller for the workspace's own agent — resolves the workspace's
        own agent and behaves exactly as it did. A named session is meaningful
        only inside a container, so it never reaches the host arm below: the
        callers that accept a name gate on `_container_tmux_or_refuse` first,
        which is where "this workspace has no container to hold a second
        agent" is phrased.

        For a container workspace whose agent runs under a tmux inside the
        container, the HOST pane is a viewport onto that pane, not the pane
        itself — so every read and every steer addresses the in-container
        session directly. Three consequences, the first of which is a
        regression this closes rather than a preference:

        * **Scrollback.** The host pane holds a tmux *client*, which repaints a
          viewport and keeps no history of its own, so peek's ``-S`` capture
          (the "only ever shows the bottom of the session" fix) returned
          about one screen — measured 22 lines of an agent's 120, plus the
          in-container status bar — while the in-container pane returns all 120
          and no chrome.
        * **Detachment.** Reading and steering no longer need a host pane to
          exist at all, which is the capability stories 1-2 created and could
          not yet use.
        * **Cost.** It is a ``docker exec`` (60 ms measured) rather than a tmux
          fork (3.5 ms). Nothing on the poll path pays it: resolution here is
          pure — the target is config, the argv is the persisted container
          identity — and only an actual capture or steer executes anything.

        This is derivation only, never a probe: a live in-container session is
        NOT verified here, because `peek` must stay cheap and a capture of a
        session that is gone already degrades to the empty snapshot.
        """
        if state.status not in LIVE_STATUSES:
            return None
        container_tmux = self._container_tmux(state, session=session)
        if container_tmux is not None:
            return container_tmux.pane
        host_session = state.tmux_session
        windows = tmux.list_windows(host_session)
        if not windows:
            return None
        preferred = self._cfg.tmux.agent_window_name
        if preferred in windows:
            return tmux.TmuxPane(target=f"{host_session}:{preferred}")
        shell = self._cfg.tmux.shell_window_name
        non_shell = [w for w in windows if w != shell]
        candidate = non_shell[0] if non_shell else windows[0]
        return tmux.TmuxPane(target=f"{host_session}:{candidate}")

    def _container_tmux(self, state: WorkspaceState, *, session: str = "") -> ContainerTmux | None:
        """The in-container tmux reader for *state*, or ``None`` if it has none.

        The single host/container branch for reading and steering, and the
        counterpart to `_container`/`_container_state` on the lifecycle side.
        ``None`` for a host workspace, for a fallback workspace, and for a
        container whose image had no tmux (`tmux_command` empty, the honest
        degradation) — so a host workspace never constructs a reader
        and never reaches for docker, which is what keeps its cost identical.

        *session* selects one of the SEVERAL agents a container may host;
        empty means the workspace's own.
        """
        if state.runtime is not Runtime.CONTAINER:
            return None
        return ContainerTmux.for_container(
            state.container, cfg=self._cfg.container, session=session
        )

    def _container_tmux_or_refuse(
        self, state: WorkspaceState, *, session: str = ""
    ) -> ContainerTmux:
        """`_container_tmux`, but a typed refusal instead of ``None``.

        The gate every multi-agent verb opens with, because those verbs are the
        only ones for which "there is no in-container tmux" is a REFUSAL rather
        than a fall-through: reading and steering the primary agent degrade
        gracefully to the host pane, but "start a second agent in this
        container" has nowhere to degrade to. Phrased once here so five verbs
        cannot phrase it five ways.
        """
        container_tmux = self._container_tmux(state, session=session)
        if container_tmux is not None:
            return container_tmux
        if state.runtime is not Runtime.CONTAINER:
            raise CapabilityUnavailable(
                f"workspace {state.id} runs on the host, so it has no container to hold "
                "several agents — one host workspace hosts one agent; create another "
                "workspace for another agent"
            )
        raise CapabilityUnavailable(
            f"workspace {state.id}'s container has no reachable tmux, so it cannot host "
            "more than the one agent its launch started (see `grove doctor` for Grove's "
            "tmux bundle, or set container.tmux.enabled)"
        )

    def _pane_reading(self, state: WorkspaceState) -> PaneReading | None:
        """Is the agent alive in its container pane? Memoized, ``None`` if unreadable.

        The poll path's only container-pane read, shared by `_reconcile_status`
        (the host session is a viewport, so its absence no longer means
        the agent is gone) and `agent_exit` (a dead pane carries the
        agent's exit status). One memo for both, because they ask one question
        of one boundary and paying twice per tick for it would double the
        subsystem's most expensive read — see
        :class:`~grove.core.container_tmux.ContainerPaneLiveness` for the
        window and the arithmetic behind it.
        """
        container_tmux = self._container_tmux(state)
        if container_tmux is None:
            return None
        if self._pane_liveness is None:
            self._pane_liveness = ContainerPaneLiveness()
        return self._pane_liveness.reading_for(container_tmux)

    def agent_exit(self, state: WorkspaceState) -> AgentExit | None:
        """How this workspace's agent command exited, or ``None`` if it has not.

        THE dead-agent seam the activity blend reads, with one answer per
        runtime because the two record the fact in different places:

        * A pane-hosted agent on this host writes its status into a file the
          launch installed a recorder for.
        * A containerized agent under an in-container tmux is the pane's OWN
          process, so tmux itself holds the fact: ``remain-on-exit`` keeps the
          dead pane, and ``#{pane_dead_status}`` is the code. The shell
          recorder cannot see this — it captures the exit of what the HOST pane
          ran, which under tmux is the tmux *client*, so an agent dying inside a
          live session recorded nothing. That narrowing is what this restores.

        Absence keeps meaning "the agent has not exited" on both roads, which is
        what keeps a slow start from ever reading as a failure: an unreadable
        docker, a session that is simply gone, and a healthy agent all answer
        ``None``. Never raises — the caller is a per-tick render path.
        """
        if self._container_tmux(state) is None:
            return AgentExit.read(paths.agent_exit_path(state.id))
        reading = self._pane_reading(state)
        if reading is None or reading.report is None or not reading.report.dead:
            return None
        return AgentExit(code=reading.report.exit_status or 0)

    def _capture_pane(
        self, state: WorkspaceState, *, session: str = ""
    ) -> tuple[str | None, datetime | None]:
        """Single source of truth for pane capture. Called by both `peek()`
        and `peek_pane()` so the "what counts as a snapshot" rule lives in
        exactly one place. Target resolution is delegated to `_agent_pane`
        so capture and steering always address the same pane.

        Headless workspaces have no pane: return the empty snapshot
        without touching tmux. peek()/peek_pane() stay best-effort (they never
        raise), so a headless card renders its transcript-derived state with no
        pane preview — the loud typed ``CapabilityUnavailable`` is reserved for
        the write path (send_message/interrupt), not this render helper.
        """
        if not self._launch_backend.provides_pane:
            return (None, None)
        pane = self._agent_pane(state, session=session)
        if pane is None:
            return (None, None)
        snap = pane.capture(history_lines=self._cfg.tmux.peek_history_lines)
        if not snap:
            return (None, None)
        return (snap, _utcnow())

    # ─── internal ──────────────────────────────────────────────────────────

    def _reconcile_status(self, state: WorkspaceState) -> WorkspaceState:  # noqa: PLR0911
        """Promote a persisted intent into the user-visible status.

        Policy (in order):
          * ``PAUSED`` / ``ERROR`` → returned as-is (terminal user-visible
            statuses; nothing to derive from live signals).
          * ``RUNNING`` intent: drives a small derivation tree
              ─ worktree dir missing on disk     → ``ORPHANED``
              ─ container provision in flight    → ``PROVISIONING``
              ─ container runtime, container not RUNNING
                                                 → ``OFFLINE``
              ─ tmux session missing             → ``OFFLINE``
              ─ session present + pane activity within threshold
                                                 → ``ACTIVE``
              ─ session present + pane quiet     → ``IDLE``
          * Already a computed status (caller passed an already-promoted
            state, or `respawn` round-tripped one) → returned as-is.

        Pure dispatch over side-effecting helpers — manager is the policy
        layer; tmux.py supplies mechanism. Returns a fresh state with the
        promoted ``status``; never mutates the input.

        **Why the container is a signal here at all, and why it maps onto
        OFFLINE rather than a fifth computed status.** For a container
        workspace the host pane runs ``devcontainer exec``, so the pane outlives
        the thing it is a window onto: a container stopped or removed behind
        Grove's back left the workspace reading ACTIVE and then decaying to IDLE
        while nothing was running in it, and no verb would take it —
        ``resume`` wants PAUSED, ``respawn`` wanted OFFLINE. OFFLINE already
        means exactly this: *the runtime that hosts the agent is gone, the
        worktree is intact, respawn is the remedy* — and respawn re-provisions
        the container before relaunching, so reusing it makes the recovery path
        work with no new vocabulary for any client to learn. A distinct status
        would have to be taught to every surface and would offer the user the
        same single action: visibility that changes no remedy is
        not worth the surface.
        """
        if state.status != WorkspaceStatus.RUNNING:
            # Not a live RUNNING intent: PAUSED/ERROR are terminal user-visible
            # statuses, and an already-computed one (ACTIVE/IDLE/OFFLINE/ORPHANED,
            # e.g. a respawn round-trip) passes through unchanged. Either way
            # there is nothing to derive from live signals.
            return state

        if not Path(state.worktree_path).is_dir():
            return _with_status(state, WorkspaceStatus.ORPHANED)
        if state.provision_status is ProvisionStatus.PROVISIONING:
            # BEFORE the container read, and that ordering is the whole point:
            # during a provision the container legitimately does not exist yet,
            # so `_container_state` answers ABSENT and the next branch would
            # demote a workspace that is building normally to OFFLINE — the
            # status whose advertised remedy is `respawn`, i.e. destroy this
            # build and start it again. It also saves the `docker inspect`,
            # which has nothing to inspect.
            return _with_status(state, WorkspaceStatus.PROVISIONING)
        container = self._container_state(state)
        if container is not None and container is not ContainerState.RUNNING:
            # Everything that is not RUNNING is a workspace the agent cannot be
            # alive in, and every one of them is respawn's job: ABSENT/STOPPED
            # need the container back, MISSING_IMAGE needs a rebuild, and
            # UNPROVISIONED is running but never reported its lifecycle hooks —
            # the one that looks healthy to the engine while the egress firewall
            # is down, so it must not read as attachable or steerable.
            # `None` is "could not read the engine" and deliberately changes
            # nothing.
            return _with_status(state, WorkspaceStatus.OFFLINE)
        if not self._launch_backend.provides_pane:
            # Headless runtime: there is deliberately no tmux session, so
            # `has_session` (→ OFFLINE) and pane activity (→ ACTIVE/IDLE) probe a
            # pane that doesn't exist. Mark the workspace operational (ACTIVE) and
            # let the activity blend derive the live agent state from the
            # transcript/adapter — the remote-adapter precedent where the pane is
            # not authoritative. The worktree/ORPHANED check above still applies.
            return _with_status(state, WorkspaceStatus.ACTIVE)
        if not tmux.has_session(state.tmux_session):
            return self._status_without_a_viewport(state)

        threshold = self._cfg.tmux.activity_threshold_seconds
        target = self._pane_target_for_running(state)
        age = tmux.pane_activity_seconds_ago(target) if target else None
        if age is not None and age <= threshold:
            return _with_status(state, WorkspaceStatus.ACTIVE)
        return _with_status(state, WorkspaceStatus.IDLE)

    def _status_without_a_viewport(self, state: WorkspaceState) -> WorkspaceState:
        """The status of a workspace whose HOST tmux session is gone.

        For a runtime where the agent runs directly in the host pane this was
        one line — no session, no agent, OFFLINE — because the host pane owned
        the agent's lifetime. Once the agent moves under a tmux inside its
        container, the host session is a *viewport*: killing it (a `tmux
        kill-server`, the documented KillMode incident, a user tidying up)
        leaves the agent running and reattachable,
        and reporting that workspace OFFLINE is not a cosmetic error — OFFLINE
        is refused by `ensure_can_attach`/`ensure_can_steer`, so it made a live
        agent unreachable through every verb a user has.

        So the liveness question moves to where the agent now lives, and the
        answer costs one memoized read (see `_pane_reading`) that only a
        viewport-less container workspace ever pays: a host workspace, a
        fallback workspace and a container with no in-container tmux all take
        the unchanged OFFLINE line, having constructed nothing.

        Three answers, and OFFLINE deliberately keeps two of them. A pane whose
        process has DIED is not a workspace to attach to or steer — it is
        exactly what respawn exists for, and respawn requires OFFLINE — while
        the agent's own death is reported on the agent axis (`agent_exit`), the
        two-axis split this design established. And "could not read docker"
        changes nothing, which here means it keeps the pre-existing OFFLINE: a
        reconcile that cannot see must never invent liveness.
        """
        reading = self._pane_reading(state)
        if reading is None or not reading.alive:
            return _with_status(state, WorkspaceStatus.OFFLINE)
        age = reading.age_seconds()
        if age is not None and age <= self._cfg.tmux.activity_threshold_seconds:
            return _with_status(state, WorkspaceStatus.ACTIVE)
        return _with_status(state, WorkspaceStatus.IDLE)

    def _pane_target_for_running(self, state: WorkspaceState) -> str | None:
        """Pane target resolution that ignores reconciled status.

        ``_pane_target`` rejects non-LIVE statuses, but reconciliation runs
        *before* a status is LIVE — chicken/egg. This variant assumes the
        caller has already established the session is up and just needs the
        agent-window target spec.
        """
        session = state.tmux_session
        windows = tmux.list_windows(session)
        if not windows:
            return None
        preferred = self._cfg.tmux.agent_window_name
        if preferred in windows:
            return f"{session}:{preferred}"
        shell = self._cfg.tmux.shell_window_name
        non_shell = [w for w in windows if w != shell]
        candidate = non_shell[0] if non_shell else windows[0]
        return f"{session}:{candidate}"

    def _maybe_emit_status_drift(
        self,
        before: WorkspaceState,
        after: WorkspaceState,
    ) -> None:
        """Emit one-shot drift events for transitions the TUI cares about.

        Today the TUI flashes a hint when reconciliation surfaces a problem
        (workspace went OFFLINE between renders, or worktree was deleted
        externally). Active↔Idle is *not* surfaced — the badge change is
        sufficient and a flash on every idle would be noisy.

        Idempotent across consecutive ``list()`` calls: each workspace's
        last reconciled status is cached, so a workspace that's already
        known-OFFLINE doesn't re-fire on the next reconciliation. This is
        load-bearing — TUI subscribers refresh by re-calling ``list()``
        from inside their event handler, and re-emitting drift on every
        call would recurse until ``RecursionError`` (surfaced to the user
        as "subscriber raised on offline detected event" on every kill).
        """
        if before.status != WorkspaceStatus.RUNNING:
            # Persisted intent isn't RUNNING (PAUSED / ERROR / already a
            # computed view). Drop any cached drift state so a future
            # promotion back to RUNNING starts clean.
            self._last_reconciled_status.pop(before.id, None)
            return

        previous = self._last_reconciled_status.get(before.id)
        self._last_reconciled_status[before.id] = after.status
        if previous == after.status:
            return  # no change since last reconciliation — skip re-emit

        if after.status == WorkspaceStatus.OFFLINE:
            logger.debug("workspace {} flagged OFFLINE (tmux session missing)", before.id)
            self._emit("offline_detected", before.id)
        elif after.status == WorkspaceStatus.ORPHANED:
            logger.debug("workspace {} flagged ORPHANED (worktree missing)", before.id)
            self._emit("orphaned_detected", before.id)

    def _rollback_create(self, state: WorkspaceState) -> None:
        """Best-effort cleanup when create fails partway through.

        Mirrors the placement gating of the create path it unwinds: for root
        placement the worktree is the repo root and the branch is the live
        checkout, so rollback must never remove the directory or delete the
        branch — it only kills any session and forgets the record. Reusing the
        same gate here is what keeps a failed root create from destroying the
        user's repo.

        The branch has a SECOND gate, ``state.grove_owns_branch``: a rollback
        unwinds what create did, and create did not create a branch it merely
        checked out. Placement alone is not enough — a WORKTREE
        workspace attached to the user's existing branch is the ordinary case
        the placement gate lets through.
        """
        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("rollback: kill_session failed: {}", exc)
        # ── container: a create that failed AFTER `up` still left a
        # container on the host — including an `outcome:error` one, whose id the
        # CLI reports and the create path persists before touching tmux for
        # exactly this reason. Skipping it here is how orphans are made.
        container = self._container(state)
        if container is not None:
            try:
                container.teardown()
            except Exception as exc:
                logger.warning("rollback: container teardown failed: {}", exc)
        if state.placement is not Placement.ROOT:
            try:
                self._git.worktree_remove(Path(state.worktree_path), force=True)
            except Exception as exc:
                logger.warning("rollback: worktree_remove failed: {}", exc)
            if state.grove_owns_branch:
                try:
                    self._git.branch_delete(state.branch, force=True)
                except Exception as exc:
                    logger.warning("rollback: branch_delete failed: {}", exc)
        # Deliberately NOT dropping the init log here: rollback fires exactly
        # when a failed init needs diagnosing, and the log is the only artifact
        # that survives the worktree teardown. kill() — intentional
        # teardown — remains the cleanup point.
        try:
            self._store.delete(state.id)
        except Exception as exc:
            logger.warning("rollback: store.delete failed: {}", exc)

    def _record_error(self, state: WorkspaceState, detail: str) -> None:
        errored = _replace(
            state,
            status=WorkspaceStatus.ERROR,
            error_detail=detail,
            updated_at=_utcnow(),
        )
        try:
            self._store.save(errored)
        except Exception as exc:
            logger.warning("could not persist error state: {}", exc)

    def _emit(
        self,
        kind: EventKindStr,
        workspace_id: str,
        detail: dict[str, str] | None = None,
    ) -> None:
        event = WorkspaceEvent(
            kind=kind,
            workspace_id=workspace_id,
            detail=dict(detail) if detail else {},
        )
        for cb in list(self._subs):
            try:
                cb(event)
            except Exception as exc:  # subscriber bugs must not break the manager
                logger.warning("subscriber raised on {} event: {}", kind, exc)


# ─── module-level factory ───────────────────────────────────────────────────


def build(
    repo_root: Path | None = None,
    *,
    cli_overrides: dict[str, object] | None = None,
    store: JsonWorkspaceStore | None = None,
) -> WorkspaceManager:
    """Build a manager bound to `repo_root` (or the cwd's repo if not given).

    With no ``repo_root``, binds to the cwd repo's MAIN worktree root — the
    key the workspace store uses. From inside a *linked* worktree,
    ``detect_root`` returns that worktree's own root, and a manager keyed by
    it would list zero workspaces. The rule lives here so every
    cwd-bound caller (CLI verbs, ``grove ls``, the TUI entry) inherits it.
    """
    resolved: Path
    if repo_root is None:
        detected = GitRepo.detect_root(Path.cwd())
        if detected is None:
            raise GroveError(
                "Grove must be run from inside a git repository "
                "(no repo found at or above the current directory)."
            )
        resolved = GitRepo(detected).worktree_paths()[0]
    else:
        resolved = repo_root
    cfg = load_config(resolved, cli_overrides=cli_overrides)
    return WorkspaceManager(
        repo_root=resolved,
        cfg=cfg,
        store=store if store is not None else JsonWorkspaceStore(),
    )


# ─── helpers ────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _replace(state: WorkspaceState, **changes: object) -> WorkspaceState:
    """Return a copy of `state` with selected fields replaced."""
    return _dc_replace(state, **changes)  # type: ignore[arg-type]


def _init_outcome(
    enabled: bool,
    rc: int,
    started: datetime,
    log_path: Path,
) -> dict[str, object]:
    """Build the init_* fields written onto WorkspaceState after a script run.

    Same shape used by both create() and resume() — defined once so they
    can't drift. Returns a dict to pass directly into `_replace(**...)`.
    """
    if not enabled:
        return {
            "init_status": InitStatus.SKIPPED,
            "init_duration_ms": None,
            "init_log_path": None,
        }
    duration_ms = int((_utcnow() - started).total_seconds() * 1000)
    return {
        "init_status": InitStatus.OK if rc == 0 else InitStatus.FAILED,
        "init_duration_ms": duration_ms,
        "init_log_path": str(log_path) if log_path.exists() else None,
    }


def _provision_outcome(
    provisioned: bool,
    started: datetime,
    log_path: Path,
) -> dict[str, object]:
    """The provision_* fields written after a container provision attempt.

    The deliberate twin of `_init_outcome` — same trio, same shape, same
    `_replace(**...)` call convention — because "did this workspace's
    preparation step succeed, how long did it take, and where is its log" is one
    question asked of two steps, not two different questions.
    """
    duration_ms = int((_utcnow() - started).total_seconds() * 1000)
    return {
        "provision_status": ProvisionStatus.OK if provisioned else ProvisionStatus.FAILED,
        "provision_duration_ms": duration_ms,
        "provision_log_path": str(log_path) if log_path.exists() else None,
    }


def _drop_init_log(workspace_id: str) -> None:
    """Best-effort delete of the per-workspace init log file."""
    log = paths.init_log_path(workspace_id)
    try:
        log.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("could not unlink init log {}: {}", log, exc)


# How much of the init log a fail_fast error carries. Enough to show the
# stderr of the failing command; small enough for a TUI toast / SSE detail.
_INIT_LOG_TAIL_LINES = 20


def _log_failure_detail(prefix: str, log_path: Path) -> str:
    """Build a self-diagnosing init-failure message: prefix + log path + log tail.

    Every fail_fast init raise goes through here so a user (or the rail) sees
    *what* failed without reopening a shell — rollback used to
    delete the log at exactly the moment it was needed. stderr is the last
    section run_init_script writes, so the file tail doubles as the stderr
    tail. Best-effort: an unreadable log degrades to just the path; this must
    never mask the original failure.
    """
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        lines = []
    message = f"{prefix} — init log kept at {log_path}"
    if lines:
        tail = "\n".join(lines[-_INIT_LOG_TAIL_LINES:])
        message = f"{message}\n--- init log tail ---\n{tail}"
    return message


def _touch(state: WorkspaceState) -> WorkspaceState:
    return _replace(state, updated_at=_utcnow())


def _with_status(state: WorkspaceState, status: WorkspaceStatus) -> WorkspaceState:
    return _replace(state, status=status)


_STATUS_RANK = {
    # Live workspaces first — currently doing work or waiting at the agent.
    WorkspaceStatus.ACTIVE: 0,
    WorkspaceStatus.IDLE: 1,
    # `RUNNING` is the unpromoted intent; should rarely surface, but if a
    # caller bypasses the manager's promotion it sorts alongside its kind.
    WorkspaceStatus.RUNNING: 1,
    WorkspaceStatus.PAUSED: 2,
    WorkspaceStatus.OFFLINE: 3,
    WorkspaceStatus.ORPHANED: 4,
    WorkspaceStatus.ERROR: 5,
}


def _list_sort_key(state: WorkspaceState) -> tuple[int, float]:
    rank = _STATUS_RANK.get(state.status, 99)
    # Negative timestamp so newer first within a rank tier.
    return (rank, -state.updated_at.timestamp())


__all__ = [
    "AttachInstruction",
    "WorkspaceEvent",
    "WorkspaceManager",
    "build",
]
