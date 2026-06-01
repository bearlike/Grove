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
from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from loguru import logger

from grove.core import paths, tmux
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.branch_plan import BranchMode, ResolvedBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import (
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    GroveError,
    WorkspaceStateError,
)
from grove.core.git import GitRepo
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import AttachInstruction
from grove.core.workspace import (
    LIVE_STATUSES,
    BranchProvenance,
    CommitSummary,
    InitStatus,
    WorkspaceIdentity,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
    ensure_can_attach,
    ensure_can_kill,
    ensure_can_pause,
    ensure_can_respawn,
    ensure_can_resume,
    ensure_can_update,
)

EventKindStr = Literal[
    "created",
    "paused",
    "resumed",
    "respawned",
    "killed",
    "updated",
    "error",
    "offline_detected",
    "orphaned_detected",
]


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
    ) -> None:
        self._repo_root = repo_root
        self._cfg = cfg
        self._store = store
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

    def create(self, request: CreateWorkspaceRequest) -> WorkspaceState:  # noqa: PLR0915
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

        ts = WorkspaceIdentity.timestamp()
        resolved = request.branch_plan.resolve(self._cfg, request.title, ts)
        self._validate_branch_plan(resolved)

        session = WorkspaceIdentity.session_name(self._cfg, request.title, ts)
        worktree = WorkspaceIdentity.worktree_path(self._cfg, self._repo_root, request.title, ts)
        now = _utcnow()
        # `base_branch` on the persisted record drives the peek's
        # ahead/behind/diff math. For NEW plans it's the explicit base;
        # for TrackRemote it's the upstream we'll set; for CHECKOUT
        # there is no base, so we fall back to "HEAD" — peek tolerates
        # a missing-base (returns zeros) when the branch and base agree.
        base_for_peek = resolved.base_ref or resolved.tracks or "HEAD"
        # Description normalizes empty string → None so the wire and disk
        # values agree on a single representation of "no description".
        description = (request.description or "").strip() or None
        state = WorkspaceState(
            id=WorkspaceIdentity.new_id(),
            title=request.title,
            repo_root=str(self._repo_root),
            branch=resolved.name,
            base_branch=base_for_peek,
            worktree_path=str(worktree),
            tmux_session=session,
            agent_name=request.agent_name,
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
            description=description,
            branch_provenance=resolved.provenance,
        )
        # Persist before side effects so a crash leaves a recoverable record.
        self._store.save(state)

        try:
            if resolved.mode == BranchMode.NEW:
                self._git.worktree_add(
                    worktree,
                    new_branch=resolved.name,
                    base=resolved.base_ref or "HEAD",
                )
                if resolved.tracks:
                    # Setting upstream after `-b` mirrors the most
                    # widely-deployed git's `track` semantics — we don't
                    # rely on `--track` (varies across git versions and
                    # config defaults). Best-effort: a stale remote ref
                    # would already have failed validation upstream.
                    self._git.branch_set_upstream(resolved.name, resolved.tracks)
            else:
                self._git.worktree_add(worktree, existing_branch=resolved.name)
        except Exception as exc:
            self._record_error(state, f"worktree_add failed: {exc}")
            self._emit("error", state.id, {"phase": "worktree_add", "error": str(exc)})
            self._store.delete(state.id)
            raise GroveError(f"failed to create worktree: {exc}") from exc

        init_log = paths.init_log_path(state.id)
        init_started = _utcnow()
        try:
            init_rc = tmux.run_init_script(
                self._cfg.init_script,
                worktree=worktree,
                repo_root=self._repo_root,
                extra_env={
                    "GROVE_REPO": str(self._repo_root),
                    "GROVE_WORKTREE": str(worktree),
                    "GROVE_BRANCH": resolved.name,
                    "GROVE_AGENT": request.agent_name,
                },
                log_path=init_log if self._cfg.init_script.enabled else None,
            )
        except Exception as exc:
            self._rollback_create(state)
            self._emit("error", state.id, {"phase": "init_script", "error": str(exc)})
            raise GroveError(f"init script raised: {exc}") from exc

        state = _replace(
            state,
            **_init_outcome(self._cfg.init_script.enabled, init_rc, init_started, init_log),
        )

        if init_rc != 0:
            if self._cfg.init_script.fail_fast:
                self._rollback_create(state)
                self._emit(
                    "error",
                    state.id,
                    {"phase": "init_script", "exit_code": str(init_rc)},
                )
                raise GroveError(
                    f"init script exited {init_rc}; fail_fast=True so workspace was rolled back"
                )
            logger.warning("init script exited {} but fail_fast=False; continuing", init_rc)

        try:
            tmux.create_session(
                session,
                cwd=worktree,
                history_limit=self._cfg.tmux.history_limit,
            )
            tmux.build_workspace_layout(session, cfg=self._cfg, worktree=worktree, agent=agent)
        except Exception as exc:
            self._rollback_create(state)
            self._emit("error", state.id, {"phase": "tmux", "error": str(exc)})
            raise GroveError(f"failed to set up tmux session: {exc}") from exc

        state = _touch(state)
        self._store.save(state)
        self._emit("created", state.id, {"title": request.title, "agent": request.agent_name})
        return state

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
        """
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
                    raise GroveError(f"init script exited {rc} on resume")
            except GroveError:
                self._git.worktree_remove(worktree, force=True)
                raise
            init_changes = dict(
                _init_outcome(self._cfg.init_script.enabled, rc, init_started, init_log)
            )

        try:
            tmux.create_session(
                state.tmux_session,
                cwd=worktree,
                history_limit=self._cfg.tmux.history_limit,
            )
            tmux.build_workspace_layout(
                state.tmux_session, cfg=self._cfg, worktree=worktree, agent=agent
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
        """Tear down the workspace's worktree (always) and tmux session (always).

        The local branch is deleted only when ``delete_branch`` is True.
        Default (``None``) resolves from ``state.branch_provenance``:
        ``GROVE_CREATED`` → True (Grove made the branch; safe to drop),
        ``USER_ATTACHED`` → False (the user's pre-existing branch stays).

        **Remote branches are never touched.** Period — there is no flag
        to opt into remote deletion. That's ``git push --delete``
        territory and stays in the user's shell, with their own
        credentials. Best-effort on each step; a failure on one stage
        does not prevent later stages from running.
        """
        state = self._store.get(workspace_id)
        ensure_can_kill(state)
        if delete_branch is None:
            delete_branch = state.branch_provenance == BranchProvenance.GROVE_CREATED

        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("kill_session during kill failed: {}", exc)
        try:
            self._git.worktree_remove(Path(state.worktree_path), force=True)
        except Exception as exc:
            logger.warning("worktree_remove during kill failed: {}", exc)
        if delete_branch:
            try:
                self._git.branch_delete(state.branch, force=True)
            except Exception as exc:
                logger.warning("branch_delete during kill failed: {}", exc)
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

    def attach(self, workspace_id: str) -> AttachInstruction:
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_attach(state)
        return tmux.attach_instruction(state.tmux_session)

    def respawn(self, workspace_id: str) -> WorkspaceState:
        """Recreate the tmux session for an OFFLINE workspace.

        OFFLINE means the persisted intent is RUNNING but the tmux session
        has vanished externally (the Grove user closed it, the host rebooted,
        a peer killed it). The worktree is intact, so we don't touch git —
        we only spin up a fresh session in the existing worktree and restart
        the agent. Init script is NOT re-run by default (the worktree was
        already initialized at create time); set `init_script.run_on_resume`
        to opt in for parity with `resume`.
        """
        state = self._reconcile_status(self._store.get(workspace_id))
        ensure_can_respawn(state)
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
                    raise GroveError(f"init script exited {rc} on respawn")
            except GroveError:
                self._emit("error", state.id, {"phase": "respawn.init_script"})
                raise
            init_changes = dict(
                _init_outcome(self._cfg.init_script.enabled, rc, init_started, init_log)
            )

        try:
            tmux.create_session(
                state.tmux_session,
                cwd=worktree,
                history_limit=self._cfg.tmux.history_limit,
            )
            tmux.build_workspace_layout(
                state.tmux_session, cfg=self._cfg, worktree=worktree, agent=agent
            )
        except Exception as exc:
            self._emit("error", state.id, {"phase": "respawn.tmux", "error": str(exc)})
            raise GroveError(f"could not start tmux session: {exc}") from exc

        # Persisted intent is already RUNNING; just refresh the timestamp +
        # any init changes. Status field stays the persisted RUNNING; next
        # list()/peek() promotes it to ACTIVE.
        new_state = _replace(
            self._store.get(workspace_id),
            status=WorkspaceStatus.RUNNING,
            updated_at=_utcnow(),
            error_detail=None,
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
        """
        target = self._pane_target(state)
        if target is None:
            return (None, None)
        snap = tmux.capture_pane_snapshot(target)
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
        if state.status in {WorkspaceStatus.PAUSED, WorkspaceStatus.ERROR}:
            return state
        if state.status != WorkspaceStatus.RUNNING:
            # Already promoted (ACTIVE/IDLE/OFFLINE/ORPHANED) — return as-is.
            return state

        if not Path(state.worktree_path).is_dir():
            return _with_status(state, WorkspaceStatus.ORPHANED)
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
        """Best-effort cleanup when create fails partway through."""
        try:
            tmux.kill_session(state.tmux_session)
        except Exception as exc:
            logger.warning("rollback: kill_session failed: {}", exc)
        try:
            self._git.worktree_remove(Path(state.worktree_path), force=True)
        except Exception as exc:
            logger.warning("rollback: worktree_remove failed: {}", exc)
        try:
            self._git.branch_delete(state.branch, force=True)
        except Exception as exc:
            logger.warning("rollback: branch_delete failed: {}", exc)
        _drop_init_log(state.id)
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
    """Build a manager bound to `repo_root` (or the cwd's repo if not given)."""
    resolved: Path
    if repo_root is None:
        detected = GitRepo.detect_root(Path.cwd())
        if detected is None:
            raise GroveError(
                "Grove must be run from inside a git repository "
                "(no repo found at or above the current directory)."
            )
        resolved = detected
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
