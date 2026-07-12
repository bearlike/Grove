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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.hook import ClaudeHook, PendingQuestion
from grove.core.config import AgentSpec, GroveConfig, load_config
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.branch_plan import AutoBranch, BranchMode, ResolvedBranch
from grove.core.contracts.questions import QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketRef, TicketSelector
from grove.core.errors import (
    AgentSessionNotFound,
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    CapabilityUnavailable,
    GroveError,
    MewboError,
    PaneNotFound,
    QuestionAnswerInvalid,
    QuestionNotPending,
    ResumeNotSupported,
    SteeringUnsupported,
    WorkspaceStateError,
)
from grove.core.git import GitRepo
from grove.core.launch import LaunchBackend, LaunchSpec, TmuxLaunchBackend
from grove.core.mewbo import MewboClient
from grove.core.store import JsonWorkspaceStore
from grove.core.tickets import TicketProviderRegistry
from grove.core.tmux import AttachInstruction
from grove.core.workspace import (
    LIVE_STATUSES,
    BranchProvenance,
    CommitSummary,
    InitStatus,
    Placement,
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
]

# Agent kinds steered over their own API rather than tmux injection. The
# membership check in send_message/interrupt routes these to _steer_remote —
# the single remote dispatch point. frozenset[str] rather than AgentKind:
# the persisted agent_kind being matched is read back from JSON as plain str.
_REMOTE_STEERED_KINDS: frozenset[str] = frozenset({"mewbo"})

# Agent kinds whose tools expose an in-session SLASH-CONTROL surface (#178) — a
# ``/name`` command/skill invocation and the interactive ``/model <id>`` switch,
# delivered through the ordinary steer path. The filesystem CLI adapters
# (claude_code, codex); a remote (mewbo) session or a bare shell has none, so the
# trigger verbs raise `CapabilityUnavailable` for those. frozenset[str] (not
# AgentKind) — matched against the effective kind read back as plain str.
_CONTROL_KINDS: frozenset[str] = frozenset({"claude_code", "codex"})

# Agent kinds that can resume an EXISTING session by explicit id at launch (#120):
# a filesystem CLI adapter that carries a resume handle (`claude --resume <id>`,
# `codex resume <id>`). DERIVED from the adapter layer's own `resumable` flag
# (#F10d) rather than hand-listed, so a future resumable adapter is picked up
# automatically instead of being silently missed. A remote (mewbo) session and a
# generic shell have no launch resume handle, so create() rejects
# `resume_session_id` for anything outside this set before any side effect.
# frozenset[str] (not AgentKind) — matched against `agent.kind`.
_RESUMABLE_KINDS: frozenset[str] = frozenset(a.kind for a in all_adapters() if a.resumable)

# The env var each filesystem adapter resolves its config-dir cascade from —
# a provider-protocol fact (see grove.core.agents.claude_code/codex), not user
# policy, so it's fine to name here rather than in config. Backs
# `transcript_config_dir_scope` (#147): a kind absent from this map (mewbo,
# generic) has no config-dir concept, so a context override is a no-op for it.
_TRANSCRIPT_CONFIG_DIR_ENV: dict[str, str] = {
    "claude_code": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}


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
    ) -> None:
        self._repo_root = repo_root
        self._cfg = cfg
        self._store = store
        # Injected for tests (DI at the I/O boundary); production passes None
        # and the first mewbo launch builds one from cfg.mewbo.
        self._mewbo_client = mewbo_client
        # The paneless-steering delivery seam (#172): a workspace with no tmux
        # pane routes send/answer/interrupt here instead of into a pane. Injected
        # for tests like `mewbo_client`; production builds the channel-backed
        # default lazily on first native steer.
        self._native_steer_client = native_steer
        # The swappable "start the assembled command in the workspace" seam
        # (#145). Default is tmux; a container/headless runtime injects its own
        # backend without touching the AgentSpec/adapter/decoration composition.
        self._launch_backend = launch_backend or TmuxLaunchBackend()
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
        """Whether the launch backend hosts a tmux pane (False = headless, #146).

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
        """
        if self._ticket_registry is None:
            self._ticket_registry = TicketProviderRegistry(self._cfg.tickets)
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
        so a symlinked or relative ``project_cwd`` still maps correctly (#101).
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
        # Resume-into-workspace (#120) is gated + validated HERE, before any side
        # effect, so a bad request fails clean with no rollback work (mint runs
        # post-worktree, too late). Three checks, in order:
        #   1. the kind must carry a launch resume handle (claude_code/codex);
        #   2. the ref (id OR unique prefix, #F8) must resolve in this project —
        #      an unknown/ambiguous ref used to run ALL side effects and then
        #      strand a fully-provisioned workspace pinned to a bogus id;
        #   3. the resolved session's adapter kind must equal the agent's (#F4
        #      parity) — a cross-kind pin is a permanent dead pointer.
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
        # Agent cwd vs worktree placement (#101): the worktree (above) and branch
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
                self._store.delete(state.id)
                raise GroveError(f"failed to create worktree: {exc}") from exc

        # `skip_init` is a per-create override of `init_script.enabled`; either
        # one being off means the script never runs and the outcome is SKIPPED.
        # Gating the call here (rather than relying on run_init_script's internal
        # enabled-check) is what lets a single create opt out without touching
        # config — and keeps the risky "init in the real repo root" path off by
        # default for root workspaces, which auto-check skip in the UI.
        init_enabled = self._cfg.init_script.enabled and not request.skip_init
        init_log = paths.init_log_path(state.id)
        init_started = _utcnow()
        init_rc = 0
        if init_enabled:
            try:
                init_rc = tmux.run_init_script(
                    self._cfg.init_script,
                    worktree=worktree,
                    repo_root=self._repo_root,
                    extra_env={
                        "GROVE_REPO": str(self._repo_root),
                        "GROVE_WORKTREE": str(worktree),
                        "GROVE_BRANCH": branch,
                        "GROVE_AGENT": request.agent_name,
                    },
                    log_path=init_log,
                )
            except Exception as exc:
                self._rollback_create(state)
                self._emit("error", state.id, {"phase": "init_script", "error": str(exc)})
                raise GroveError(f"init script raised: {exc}") from exc

        state = _replace(
            state,
            **_init_outcome(init_enabled, init_rc, init_started, init_log),
        )

        if init_rc != 0:
            if self._cfg.init_script.fail_fast:
                self._rollback_create(state)
                self._emit(
                    "error",
                    state.id,
                    {
                        "phase": "init_script",
                        "exit_code": str(init_rc),
                        "log_path": str(init_log),
                    },
                )
                raise GroveError(
                    _init_failure_detail(
                        f"init script exited {init_rc}; fail_fast=True "
                        "so workspace was rolled back",
                        init_log,
                    )
                )
            logger.warning("init script exited {} but fail_fast=False; continuing", init_rc)

        # Deterministic session correlation (#11 §2): mint the agent session id
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
        # `initial_prompt` (#48) is create-only — never threaded into resume/respawn.
        # For claude_code it rides the launch argv (race-free); for mewbo it is
        # delivered after the workspace is persisted (below), so it isn't passed
        # here (mewbo's decoration is empty anyway). `resume` (#120) flips the
        # session-id flag to the tool's resume form (`--resume` / `resume <uuid>`).
        launch_decoration = self._compose_launch(
            agent,
            agent_session_id,
            initial_prompt=request.initial_prompt,
            model=request.model,
            resume=resume_session_id is not None,
        )

        try:
            self._launch_backend.launch(
                self._launch_spec(session, agent_cwd, agent, launch_decoration)
            )
        except Exception as exc:
            self._rollback_create(state)
            self._emit("error", state.id, {"phase": "tmux", "error": str(exc)})
            raise GroveError(f"failed to set up tmux session: {exc}") from exc

        state = _touch(state)
        self._store.save(state)
        self._emit("created", state.id, {"title": request.title, "agent": request.agent_name})
        self._deliver_remote_initial_prompt(state, agent, request.initial_prompt)
        return state

    def _deliver_remote_initial_prompt(
        self, state: WorkspaceState, agent: AgentSpec, initial_prompt: str | None
    ) -> None:
        """Re-engage a freshly-created remote (mewbo) session with the user's first
        task (#48), where the prompt can't ride a launch argv.

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

    def pause(self, workspace_id: str, *, force: bool = False) -> WorkspaceState:
        state = self._store.get(workspace_id)
        ensure_can_pause(state)
        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("kill_session during pause failed: {}", exc)
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
        # One rule (#F2): continue what EXISTS, mint what doesn't. Flip to the
        # tool's resume flag only when the pinned session has ALREADY materialized
        # (a transcript is on disk) and the kind can resume by id — else keep the
        # mint form, so a resume-created codex workspace doesn't relaunch a bare
        # `codex` (new thread, stale pin) and a never-materialized minted claude id
        # keeps `--session-id` so it can still mint fresh. Materialization is
        # checked BEFORE the worktree is recreated; transcripts outlive worktrees
        # (they live under the encoded-cwd projects folder), so the check is valid.
        resume = agent.kind in _RESUMABLE_KINDS and self._pinned_session_materialized(agent, state)
        launch_decoration = self._compose_launch(agent, state.agent_session_id, resume=resume)

        worktree = Path(state.worktree_path)
        try:
            self._git.worktree_add(worktree, existing_branch=state.branch)
        except Exception as exc:
            self._emit("error", state.id, {"phase": "resume.worktree_add", "error": str(exc)})
            raise GroveError(f"could not recreate worktree: {exc}") from exc

        init_changes: dict[str, object] = {}
        if self._cfg.init_script.run_on_resume:
            init_log = paths.init_log_path(state.id)
            init_started = _utcnow()
            try:
                rc = tmux.run_init_script(
                    self._cfg.init_script,
                    worktree=worktree,
                    repo_root=self._repo_root,
                    log_path=init_log if self._cfg.init_script.enabled else None,
                )
                if rc != 0 and self._cfg.init_script.fail_fast:
                    raise GroveError(
                        _init_failure_detail(f"init script exited {rc} on resume", init_log)
                    )
            except GroveError:
                self._git.worktree_remove(worktree, force=True)
                raise
            init_changes = dict(
                _init_outcome(self._cfg.init_script.enabled, rc, init_started, init_log)
            )

        try:
            self._launch_backend.launch(
                self._launch_spec(state.tmux_session, state.agent_cwd, agent, launch_decoration)
            )
        except Exception as exc:
            self._git.worktree_remove(worktree, force=True)
            self._emit("error", state.id, {"phase": "resume.tmux", "error": str(exc)})
            raise GroveError(f"could not start tmux session: {exc}") from exc

        new_state = _replace(
            state,
            status=WorkspaceStatus.RUNNING,
            paused_at=None,
            updated_at=_utcnow(),
            error_detail=None,
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
        does not prevent later stages from running.
        """
        state = self._store.get(workspace_id)
        ensure_can_kill(state)
        is_root = state.placement is Placement.ROOT
        if delete_branch is None:
            delete_branch = state.branch_provenance == BranchProvenance.GROVE_CREATED
        if is_root:
            # Hard override: the repo root is never Grove's to remove and the
            # live branch is never Grove's to delete, whatever the caller asks.
            delete_branch = False

        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("kill_session during kill failed: {}", exc)
        if not is_root:
            try:
                self._git.worktree_remove(Path(state.worktree_path), force=True)
            except Exception as exc:
                logger.warning("worktree_remove during kill failed: {}", exc)
        if delete_branch:
            try:
                self._git.branch_delete(state.branch, force=True)
            except Exception as exc:
                logger.warning("branch_delete during kill failed: {}", exc)
        if not is_root:
            try:
                self._git.worktree_prune()
            except Exception as exc:
                logger.warning("worktree_prune during kill failed: {}", exc)
        _drop_init_log(state.id)
        self._store.delete(state.id)
        self._last_reconciled_status.pop(state.id, None)
        self._emit(
            "killed",
            state.id,
            {"branch_deleted": "true" if delete_branch else "false"},
        )

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
        no-op. The ref is stored bare (provider + id) — display enrichment
        (title/status) is the daemon's on-demand fetch, never persisted here, so
        attach stays pure and offline-safe. Permitted in any status except
        ORPHANED (same gate as ``update`` — a doomed record gains nothing).
        """
        persisted = self._store.get(workspace_id)
        ensure_can_update(self._reconcile_status(persisted))
        if any(
            r.provider == selector.provider and r.id == selector.id for r in persisted.ticket_refs
        ):
            return persisted
        new_refs = [*persisted.ticket_refs, TicketRef(provider=selector.provider, id=selector.id)]
        new_state = _replace(persisted, updated_at=_utcnow(), ticket_refs=new_refs)
        self._store.save(new_state)
        self._emit(
            "updated",
            new_state.id,
            {"ticket_attached": f"{selector.provider}:{selector.id}"},
        )
        return new_state

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
        """Manually pin an existing agent session as this workspace's primary (#120).

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
        adapter-kind equality (#F4): pinning a codex session onto a claude_code
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
        """A read-only :class:`SessionExplorer` over this same manager (#120).

        Local import: ``sessions.py`` imports ``manager.py``, so a module-level
        import here would cycle. Construction is cheap (no I/O — the explorer
        only holds the manager); building one per ``remap_session`` call keeps the
        session-ref resolution DRY with ``grove sessions`` instead of duplicating
        the unique-prefix scan.
        """
        from grove.core.sessions import SessionExplorer  # noqa: PLC0415

        return SessionExplorer(self)

    def attach(self, workspace_id: str) -> AttachInstruction:
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_attach(state)
        return tmux.attach_instruction(state.tmux_session)

    def send_message(self, workspace_id: str, text: str) -> None:
        """Type ``text`` into the workspace's agent pane and submit it.

        Grove's follow-up/steer surface for tmux-hosted agents (issue #37).
        Policy lives here; the literal-safe injection mechanism is
        ``tmux.send_text``. Gates, in order: remote-steered kinds dispatch
        to the adapter arm; the session must be live (OFFLINE/PAUSED →
        typed ``WorkspaceStateError``); a pane must resolve via the same
        ``pane_target`` policy peek captures from (None → ``PaneNotFound``).

        The ``message_sent`` audit event carries the resolved target and
        the text *length*, never the content — steering text can hold
        secrets and events fan out to every subscriber and log sink.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        if state.agent_kind is not None and state.agent_kind in _REMOTE_STEERED_KINDS:
            self._steer_remote(state, "message", text)
            return
        if not self._launch_backend.provides_pane:
            # Paneless runtime (#146): no tmux pane to type into, so deliver over
            # the agent's native channel instead of raising (#172). Same
            # dispatch-point pattern as the remote arm — one seam, not a fork.
            self._steer_native(state, "message", text)
            return
        ensure_can_steer(state)
        target = self._pane_target(state)
        if target is None:
            raise PaneNotFound(
                f"no tmux pane resolved for workspace {state.id} "
                f"(session {state.tmux_session!r} reports no windows)"
            )
        tmux.send_text(target, text, settle_ms=self._cfg.tmux.steer_settle_ms)
        self._emit(
            "message_sent",
            state.id,
            {"target": target, "text_length": str(len(text))},
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
            # Paneless runtime (#146): no pane to signal, so route the interrupt
            # over the native channel (#172) instead of raising. Best-effort — a
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
        """THE single dispatch point for paneless (headless) agents (#172).

        The ``_steer_remote`` mirror for a runtime that has no tmux pane
        (``provides_pane`` False, #146): deliver over the agent's native channel
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
        """Drive a pending ``AskUserQuestion`` to resolution by keystroke (#109).

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
            # Paneless runtime (#146/#172): no pane to keystroke the picker, so
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
        target = self._pane_target(state)
        if target is None:
            raise PaneNotFound(
                f"no tmux pane resolved for workspace {state.id} "
                f"(session {state.tmux_session!r} reports no windows)"
            )
        # Re-check the capture right before the send to shrink the terminal race.
        self._pending_capture(request.session_id, request.tool_use_id)
        tmux.send_keys(target, ops, settle_ms=self._cfg.tmux.steer_settle_ms)
        self._emit(
            "question_answered",
            state.id,
            {
                "target": target,
                "tool_use_id": pending.tool_use_id,
                "answers": str(len(selections)),
            },
        )

    def session_controls(self, workspace_id: str) -> SessionControls:
        """Enumerate the input controls available to this workspace's session (#178).

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
            scanned = adapter.session_controls(state.agent_cwd, session_id)
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
            return adapter.parse_activity(state.agent_cwd, session_id).model
        except Exception as exc:  # best-effort; a parse miss is not fatal
            logger.debug("current-model read failed for {}: {}", state.id, exc)
            return None

    def latest_todo(self, workspace_id: str) -> TodoList | None:
        """The workspace's current todo/checklist state (#194) — the engine
        seam both the issueops sticky-comment publisher (in-process) and the
        ``GET /workspaces/{id}/todo`` daemon route read.

        Resolves the workspace's primary session exactly like
        :meth:`session_controls` (state → effective kind → adapter), but
        RAISES ``AgentSessionNotFound`` for a sessionless workspace instead of
        degrading — the same convention ``_steer_remote``/``_steer_native``
        use, so a caller can tell "no session yet" (404) apart from "a session
        exists but no todo tool has been called yet" (``None``, a real
        answer). The adapter read itself stays best-effort (never raises) like
        every projection here.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        session_id = state.agent_session_id
        if not session_id:
            raise AgentSessionNotFound(f"workspace {state.id} has no recorded agent session")
        adapter = get_adapter(self.effective_kind(state))
        return adapter.latest_todo(state.agent_cwd, session_id)

    def invoke_control(self, workspace_id: str, name: str) -> None:
        """Invoke a named session control — a slash command or a skill (#178).

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
        control (#178).

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
        """Deliver a ``/invocation`` slash control through the steer path (#178) —
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
        """The standing captured question for ``session_id``, or raise (#109).

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
            ensure_can_respawn(state)
        else:
            # Headless (#146): no tmux session can vanish to OFFLINE, so the
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
        launch_decoration = self._compose_launch(agent, respawn_session_id)

        init_changes: dict[str, object] = {}
        if state.placement is Placement.WORKTREE and self._cfg.init_script.run_on_resume:
            init_log = paths.init_log_path(state.id)
            init_started = _utcnow()
            try:
                rc = tmux.run_init_script(
                    self._cfg.init_script,
                    worktree=worktree,
                    repo_root=self._repo_root,
                    log_path=init_log if self._cfg.init_script.enabled else None,
                )
                if rc != 0 and self._cfg.init_script.fail_fast:
                    raise GroveError(
                        _init_failure_detail(f"init script exited {rc} on respawn", init_log)
                    )
            except GroveError:
                self._emit("error", state.id, {"phase": "respawn.init_script"})
                raise
            init_changes = dict(
                _init_outcome(self._cfg.init_script.enabled, rc, init_started, init_log)
            )

        try:
            self._launch_backend.launch(
                self._launch_spec(state.tmux_session, state.agent_cwd, agent, launch_decoration)
            )
        except Exception as exc:
            self._emit("error", state.id, {"phase": "respawn.tmux", "error": str(exc)})
            raise GroveError(f"could not start tmux session: {exc}") from exc

        # Persisted intent is already RUNNING; refresh the timestamp, the freshly
        # minted session id, and any init changes. Status stays RUNNING; the next
        # list()/peek() promotes it to ACTIVE.
        new_state = _replace(
            self._store.get(workspace_id),
            status=WorkspaceStatus.RUNNING,
            updated_at=_utcnow(),
            error_detail=None,
            agent_session_id=respawn_session_id,
            **init_changes,
        )
        self._store.save(new_state)
        self._emit("respawned", state.id)
        return new_state

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

    def peek_pane(self, workspace_id: str) -> tuple[str | None, datetime | None]:
        """Tmux-only fast path: just the agent-pane snapshot. Used by the
        rail's fast pane-tick (~250 ms) so we don't redo git ahead/behind
        and diff stats — those move at human pace, the pane moves at agent
        pace. Best-effort: returns (None, None) on any failure or for
        non-live workspaces.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        return self._capture_pane(state)

    def primary_transcript(self, workspace_id: str) -> tuple[Path, ...]:
        """Transcript file(s) for the workspace's agent session, or ``()`` if untracked.

        Resolves the adapter from the agent's ``kind`` and the persisted session
        id, then asks it to locate the file(s) under the worktree cwd. Read-only
        and best-effort — the adapter never raises. Empty for a generic/shell
        agent (no session id), a legacy record, or before the transcript is first
        written (the STARTING window). The ``ActivityService`` (#14) builds on
        this to parse activity.

        A tuple (point-in-time snapshot), matching ``list_local_branches`` — the
        files on disk may change after the call, so an immutable return can't
        mislead the caller about live state.

        Honors ``state.transcript_context`` (#147): a container-launched session
        records a cwd the host's own ``worktree_path`` can never equal, so an
        override substitutes that recorded cwd and scopes the adapter's
        config-dir env var to the override's host directory for this one call.
        No override (the default) is byte-for-byte the pre-#147 behavior.
        """
        state = self._store.get(workspace_id)
        if not state.agent_session_id:
            return ()
        agent = self._cfg.find_agent(state.agent_name)
        kind = agent.kind if agent is not None else "generic"
        ctx = state.transcript_context
        cwd = Path(ctx.agent_cwd) if ctx is not None else Path(state.worktree_path)
        with self.transcript_config_dir_scope(kind, ctx.config_dir if ctx is not None else None):
            return tuple(get_adapter(kind).locate_transcripts(cwd, state.agent_session_id))

    @staticmethod
    @contextlib.contextmanager
    def transcript_config_dir_scope(kind: str, config_dir: str | None) -> Iterator[None]:
        """Point ``kind``'s config-dir env var at ``config_dir`` for one read (#147).

        Filesystem adapters resolve ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``
        ambiently from ``os.environ`` on every call — never a parameter, since
        threading one through would be adapter parsing, off-limits for #147 —
        so honoring a workspace's ``transcript_context.config_dir`` override
        means scoping the process env around the read itself. A no-op (and
        the true default-behavior path) when there is no override
        (``config_dir is None``) or ``kind`` has no config-dir env
        (``_TRANSCRIPT_CONFIG_DIR_ENV`` — mewbo/generic). Restores the prior
        value (or its absence) on exit. Best-effort like every adapter read;
        callers should hold this for the shortest span — one locate/read call,
        never across a whole request — since the env is process-global.
        """
        var = _TRANSCRIPT_CONFIG_DIR_ENV.get(kind)
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

    def _pinned_session_materialized(self, agent: AgentSpec, state: WorkspaceState) -> bool:
        """Whether ``state``'s pinned session already has a transcript on disk (#F2).

        The materialization test the unpause path gates its resume-vs-mint choice
        on: a session is materialized when the adapter locates at least one
        transcript for the pinned id, scanning the ``transcript_scan_cwds`` union
        (#F7) so a nested project's root-recorded transcript still counts — or,
        with a ``transcript_context`` override (#147), the one recorded cwd it
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

    def effective_kind(self, state: WorkspaceState) -> str:
        """The adapter kind for ``state`` — persisted at create, else config (#F4).

        Prefers the kind persisted at create (resolves a repo-scoped agent the
        daemon's global config never loaded); falls back to a config lookup for
        legacy records written before ``agent_kind`` existed, then ``generic``.
        Mirrors ``ActivityService._effective_kind`` — the read path's copy — so
        the remap/create kind gate matches what the dashboard will actually read.
        Public because ``SessionExplorer`` also restricts its per-workspace scan
        to this one kind (#164), so the picker never offers a foreign-kind session
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
        skips it (continue = keep the persisted id; mewbo re-engagement is the
        follow-up surface, #37).

        Two opposite minting directions, worth naming:

        - **claude_code** (any local CLI): CLIENT-minted — Grove generates the
          UUID and launches ``--session-id <uuid>``, so the transcript path is
          known by construction. Generic agents mint nothing (empty decoration).
        - **mewbo**: SERVER-minted — ``POST /api/sessions`` creates the remote
          session anchored to the worktree ``cwd`` (which the API validates is
          an existing directory — callers therefore invoke this only after the
          worktree is on disk) and the RETURNED id is what Grove persists.

        ``resume_session_id`` (#120) short-circuits both: adopt the chosen id
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
            # `model` (create-only, #98) forwards to the remote session-create —
            # mewbo has no launch `--model` flag (the model is server-side), so
            # this is the one place a per-create model choice can reach it.
            return self._mewbo().create_session(cwd=str(worktree), title=title, model=model)
        session_id = WorkspaceIdentity.new_session_id()
        return session_id if get_adapter(agent.kind).launch_decoration(session_id) else None

    def _mewbo(self) -> MewboClient:
        """The Mewbo REST client: injected (tests) or built once from config."""
        if self._mewbo_client is None:
            self._mewbo_client = MewboClient(self._cfg.mewbo)
        return self._mewbo_client

    def _native_steer(self) -> native.NativeSteerClient:
        """The paneless-steering client (#172): injected (tests) or built once.

        Default is the channel-backed :class:`~grove.core.native.ChannelSteerClient`
        — the ``_mewbo`` pattern for the native (non-tmux) delivery path.
        """
        if self._native_steer_client is None:
            self._native_steer_client = native.ChannelSteerClient()
        return self._native_steer_client

    def _launch_spec(
        self, session_name: str, agent_cwd: Path, agent: AgentSpec, decoration: _Argv
    ) -> LaunchSpec:
        """Assemble the `LaunchSpec` handed to the launch backend (#145).

        One builder for all three launch sites (create/resume/respawn) so the
        spec can't drift between them. `agent_cwd` roots both the session and the
        layout windows: the worktree/branch anchor at the repo root, the agent
        session only *starts* here (the nested-cwd split, #101). The composed
        `decoration` comes from `_compose_launch` + the adapter — this only
        packages the assembled command as structured data for the backend.
        """
        # Compose the launch env: the agent's own `env` plus the opt-in
        # instrumentation passthroughs derived at the boundary (#170). Telemetry
        # (#176) contributes the generic OTLP endpoint/headers (LangFuse), and —
        # only when that endpoint actually resolves — the adapter's OWN native
        # telemetry switch (`telemetry_env`; provider boundary: Claude Code flips
        # its exporter on, Codex/mewbo/generic no-op), so the tool streams its own
        # usage/cost to LangFuse with no per-agent hand-wiring. Gating the switch
        # on a resolved endpoint means a tool never enables an exporter pointed at
        # nowhere. The gateway proxy (#177) points the tool at Grove's loopback
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
        env = {
            **telemetry,
            **self._cfg.proxy.proxy_env(agent.kind),
            **agent.env,
        }
        return LaunchSpec(
            session_name=session_name,
            cwd=agent_cwd,
            command=agent.command,
            decoration=tuple(decoration),
            env=env,
            env_unset=agent.env_unset,
            cfg=self._cfg,
            worktree=agent_cwd,
        )

    def _compose_launch(
        self,
        agent: AgentSpec,
        session_id: str | None,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        resume: bool = False,
    ) -> _Argv:
        """Full argv appended to the agent command at launch, for a known session id.

        Composes the adapter's base decoration (`--session-id <uuid>` for Claude
        Code) with the opt-in status hook: when `cfg.hooks.enabled` and the agent
        is `claude_code`, append `--settings <grove-hooks-settings>` so the hook
        pushes precise lifecycle status into a sidecar (#18) — additive, never
        touching the user's own `.claude/settings.json`. Empty for a generic/shell
        agent or a legacy record with no session id. Centralizes the composition so
        create/resume/respawn can't drift.

        `model` (create-only, #96) appends the adapter's model flag
        (`--model <id>` for claude_code / codex; `[]` for mewbo / generic) so a
        per-create model choice reaches the tool — forwarded verbatim, never
        interpreted (the provider boundary). It rides even when `session_id` is
        None (codex mints no id but still honors `--model`).

        `initial_prompt` (create-only, #48) rides the launch as a trailing
        POSITIONAL arg on a claude_code argv (`claude … "<prompt>"` boots already
        working on it — race-free, unlike post-boot pane typing). It is appended
        last so it stays the positional after every flag, and goes through the
        SAME `shlex.quote` path in `tmux.build_workspace_layout` as the rest of the
        decoration — no second quoting site. Ignored for non-claude_code kinds: a
        generic shell has no prompt concept, and mewbo carries `[]` here and is
        re-engaged through its API instead (manager `create()`).

        `resume` (create-only, #120) flips the base decoration to the tool's
        resume form (`claude --resume <id>` / `codex resume <id>`) — the id is an
        existing session to CONTINUE, not a fresh one to mint. Everything after
        the base decoration (hooks `--settings`, `--model`, the trailing prompt
        positional) is unchanged, so a resume launch composes identically bar the
        one flag.

        `agent.tools_offline` (#148) appends the adapter's network-tool-gating
        flags (`--disallowedTools WebFetch,WebSearch` for claude_code; a
        no-network sandbox for codex). Unlike `model`/`initial_prompt` it is not
        a per-create request field but a persisted `AgentSpec` toggle, so it
        rides every launch path (create/resume/respawn) uniformly.
        """
        adapter = get_adapter(agent.kind)
        decoration: _Argv = []
        if session_id is not None:
            decoration = adapter.launch_decoration(session_id, resume=resume)
            if decoration and agent.kind == "claude_code" and self._cfg.hooks.enabled:
                settings = self._ensure_hook_settings()
                if settings is not None:
                    decoration = [*decoration, "--settings", str(settings)]
        # `model` (#96) rides the launch INDEPENDENTLY of session correlation:
        # Codex mints no id (empty `launch_decoration`) yet still honors
        # `--model`, so it's appended whether or not a session id exists. Adapters
        # with no launch-time model flag (mewbo, generic) return [] — a no-op.
        if model:
            decoration = [*decoration, *adapter.model_decoration(model)]
        # `--channels` (#182) declares Grove as a native Claude Code channel so a
        # RUNNING session can act on messages Grove delivers (and relay a
        # permission decision). Opt-in + auth-gated research preview, claude_code
        # only, mirroring the hook `--settings` append above — additive, never
        # touching the user's own config, a no-op when `cfg.channels.enabled` is
        # False. Appended BEFORE the initial-prompt positional so that stays last.
        if agent.kind == "claude_code" and session_id is not None and self._cfg.channels.enabled:
            channel_settings = self._ensure_channel_settings()
            if channel_settings is not None:
                decoration = [*decoration, "--channels", str(channel_settings)]
        # `tools_offline` (#148) is a persisted per-agent policy, not a per-create
        # request field like `model` — it rides every launch (create/resume/
        # respawn) whenever the configured agent opts in, forwarded verbatim
        # (provider boundary: Grove never decides which tools are "network").
        if agent.tools_offline:
            decoration = [*decoration, *adapter.offline_decoration()]
        # `--permission-prompt-tool` (#172) routes Claude Code's "allow this tool
        # call?" gate to a Grove-hosted MCP tool that answers allow/deny JSON — the
        # native replacement for a human typing a permission answer into the pane,
        # essential for a paneless/headless session with no TTY to block on. Opt-in
        # + claude_code only, mirroring the hook `--settings` / channel `--channels`
        # appends above: additive, never touching the user's own config, a no-op
        # when `cfg.permission.enabled` is False. Appended BEFORE the initial-prompt
        # positional so that stays last.
        if agent.kind == "claude_code" and session_id is not None and self._cfg.permission.enabled:
            perm_config = self._ensure_permission_settings()
            if perm_config is not None:
                decoration = [
                    *decoration,
                    "--mcp-config",
                    str(perm_config),
                    "--permission-prompt-tool",
                    permission.permission_tool_ref(),
                ]
        # `initial_prompt` stays the trailing POSITIONAL, claude_code only, after
        # every flag (incl. --model) — same race-free launch path as before.
        if agent.kind == "claude_code" and session_id is not None and initial_prompt:
            decoration = [*decoration, initial_prompt]
        return decoration

    def _ensure_hook_settings(self) -> Path | None:
        """Write Grove's hook-only Claude Code settings file; return its path.

        Best-effort: a write failure logs and returns `None` so the agent still
        launches (just without push status — graceful degradation). Rewritten each
        launch so a Grove upgrade that changes the hook set self-heals.
        """
        path = paths.agent_hooks_settings_path()
        try:
            paths.ensure_dir(path.parent)
            path.write_text(json.dumps(ClaudeHook.settings(), indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write hook settings; launching without push status: {}", exc)
            return None
        return path

    def _ensure_channel_settings(self) -> Path | None:
        """Write Grove's channel settings file; return its path (#182).

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
        """Write Grove's permission MCP-config file; return its path (#172).

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

        Public so the TUI's resize-on-selection can target the same window
        we'll capture from — without it, capture and resize drift apart
        when an agent window has been removed externally.

        Policy (in order):
        1. Workspace not RUNNING → ``None``.
        2. Configured ``agent_window_name`` exists → ``"<session>:agent"``.
        3. Any non-``shell`` window exists → ``"<session>:<first-non-shell>"``
           (a renamed agent, an init window, etc. — usually where the live work is).
        4. Only ``shell`` exists → ``"<session>:shell"`` (last-resort fallback
           so the rail at least shows the bare prompt).
        5. Session reports no windows at all → ``None``.

        Best-effort: ``tmux.list_windows`` never raises, so this is safe to
        call from the peek hot path. Returning ``None`` is the contract for
        "no live pane to look at"; callers should render the empty state.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        return self._pane_target(state)

    def _pane_target(self, state: WorkspaceState) -> str | None:
        if state.status not in LIVE_STATUSES:
            return None
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

    def _capture_pane(self, state: WorkspaceState) -> tuple[str | None, datetime | None]:
        """Single source of truth for pane capture. Called by both `peek()`
        and `peek_pane()` so the "what counts as a snapshot" rule lives in
        exactly one place. Target resolution is delegated to `_pane_target`
        so capture and resize stay symmetric on reorganized sessions.

        Headless workspaces (#146) have no pane: return the empty snapshot
        without touching tmux. peek()/peek_pane() stay best-effort (they never
        raise), so a headless card renders its transcript-derived state with no
        pane preview — the loud typed ``CapabilityUnavailable`` is reserved for
        the write path (send_message/interrupt), not this render helper.
        """
        if not self._launch_backend.provides_pane:
            return (None, None)
        target = self._pane_target(state)
        if target is None:
            return (None, None)
        snap = tmux.capture_pane_snapshot(target, history_lines=self._cfg.tmux.peek_history_lines)
        if not snap:
            return (None, None)
        return (snap, _utcnow())

    # ─── internal ──────────────────────────────────────────────────────────

    def _reconcile_status(self, state: WorkspaceState) -> WorkspaceState:
        """Promote a persisted intent into the user-visible status.

        Policy (in order):
          * ``PAUSED`` / ``ERROR`` → returned as-is (terminal user-visible
            statuses; nothing to derive from live signals).
          * ``RUNNING`` intent: drives a small derivation tree
              ─ worktree dir missing on disk     → ``ORPHANED``
              ─ tmux session missing             → ``OFFLINE``
              ─ session present + pane activity within threshold
                                                 → ``ACTIVE``
              ─ session present + pane quiet     → ``IDLE``
          * Already a computed status (caller passed an already-promoted
            state, or `respawn` round-tripped one) → returned as-is.

        Pure dispatch over side-effecting helpers — manager is the policy
        layer; tmux.py supplies mechanism. Returns a fresh state with the
        promoted ``status``; never mutates the input.
        """
        if state.status != WorkspaceStatus.RUNNING:
            # Not a live RUNNING intent: PAUSED/ERROR are terminal user-visible
            # statuses, and an already-computed one (ACTIVE/IDLE/OFFLINE/ORPHANED,
            # e.g. a respawn round-trip) passes through unchanged. Either way
            # there is nothing to derive from live signals.
            return state

        if not Path(state.worktree_path).is_dir():
            return _with_status(state, WorkspaceStatus.ORPHANED)
        if not self._launch_backend.provides_pane:
            # Headless runtime (#146): there is deliberately no tmux session, so
            # `has_session` (→ OFFLINE) and pane activity (→ ACTIVE/IDLE) probe a
            # pane that doesn't exist. Mark the workspace operational (ACTIVE) and
            # let the activity blend derive the live agent state from the
            # transcript/adapter — the remote-adapter precedent where the pane is
            # not authoritative. The worktree/ORPHANED check above still applies.
            return _with_status(state, WorkspaceStatus.ACTIVE)
        if not tmux.has_session(state.tmux_session):
            return _with_status(state, WorkspaceStatus.OFFLINE)

        threshold = self._cfg.tmux.activity_threshold_seconds
        target = self._pane_target_for_running(state)
        age = tmux.pane_activity_seconds_ago(target) if target else None
        if age is not None and age <= threshold:
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
        """
        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("rollback: kill_session failed: {}", exc)
        if state.placement is not Placement.ROOT:
            try:
                self._git.worktree_remove(Path(state.worktree_path), force=True)
            except Exception as exc:
                logger.warning("rollback: worktree_remove failed: {}", exc)
            try:
                self._git.branch_delete(state.branch, force=True)
            except Exception as exc:
                logger.warning("rollback: branch_delete failed: {}", exc)
        # Deliberately NOT dropping the init log here: rollback fires exactly
        # when a failed init needs diagnosing, and the log is the only artifact
        # that survives the worktree teardown (issue #9). kill() — intentional
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
    it would list zero workspaces (the #51 bug). The rule lives here so every
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


def _init_failure_detail(prefix: str, log_path: Path) -> str:
    """Build a self-diagnosing init-failure message: prefix + log path + log tail.

    Every fail_fast init raise goes through here so a user (or the rail) sees
    *what* failed without reopening a shell — issue #9's rollback used to
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
