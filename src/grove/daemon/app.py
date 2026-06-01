"""FastAPI factory + lifespan + route handlers for the Grove daemon.

All routes are 1:1 with ``WorkspaceManager`` methods. Multi-repo dispatch
goes through ``RepoRegistry``. No WebSocket — clients poll. No auth —
the daemon listens on loopback only; remote access is via SSH tunnel.
"""

from __future__ import annotations

import getpass
import platform
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from grove import __version__ as _GROVE_VERSION
from grove.core.auth import SessionStore
from grove.core.config import GroveConfig
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.requests import CreateWorkspaceRequest, UpdateWorkspaceRequest
from grove.core.contracts.views import (
    AttachInstructionView,
    CommitSummaryView,
    HealthView,
    WhoamiView,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.errors import (
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchError,
    BranchNotFound,
    GroveError,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.daemon.auth import build_auth_router, make_require_session
from grove.daemon.repos import RepoRegistry


def _build_whoami(started_at: datetime) -> WhoamiView:
    """Snapshot the daemon's identity + uptime.

    Pure-ish: reads stdlib state at call time (``socket.gethostname``,
    ``getpass.getuser``, ``platform.*``), takes ``started_at`` as input
    so tests can pin uptime deterministically. ``int(...)`` truncates
    rather than rounds — uptime is a coarse signal, sub-second precision
    is noise.
    """
    now = datetime.now(UTC)
    return WhoamiView(
        version=_GROVE_VERSION,
        started_at=started_at,
        uptime_seconds=max(0, int((now - started_at).total_seconds())),
        host=socket.gethostname(),
        user=getpass.getuser(),
        platform=platform.system().lower(),
        python_version=platform.python_version(),
    )


class _PauseBody(BaseModel):
    """Pause request body — ``force`` skips the dirty-worktree check.

    Module-scope (not a ``build_app`` closure): under ``from __future__ import
    annotations`` FastAPI / Pydantic can't resolve a closure-defined model
    referenced by string forward-ref in a route handler signature, which
    surfaces as a ``PydanticUserError`` on the first request. Module-top
    keeps the introspection deterministic; the underscore prefix marks the
    class as daemon-internal so it doesn't leak into the public surface.
    """

    force: bool = False


class _KillBody(BaseModel):
    """Kill request body — ``delete_branch=None`` defers to the workspace's branch_provenance.

    Module-scope for the same reason as ``_PauseBody`` above.
    """

    delete_branch: bool | None = None


def build_app(  # noqa: PLR0915
    *,
    cfg: GroveConfig,
    store: JsonWorkspaceStore,
    auth_store: SessionStore | None = None,
) -> FastAPI:
    """Construct the daemon's FastAPI app.

    Tests call this directly; the CLI's ``serve`` calls it via uvicorn.
    ``auth_store`` is constructed from ``cfg.auth`` if not supplied — tests
    inject one with a fake clock when they need to control TTLs.

    The statement count grows linearly with route count (this is FastAPI's
    factory pattern); the function still has one job — register routes —
    so PLR0915 doesn't flag a real concern here.
    """
    registry = RepoRegistry(cfg=cfg, store=store)
    if auth_store is None:
        auth_store = SessionStore(
            session_ttl=timedelta(seconds=cfg.auth.session_ttl_seconds),
            pairing_ttl=timedelta(seconds=cfg.auth.pairing_ttl_seconds),
            pair_init_per_minute=cfg.auth.pair_init_per_minute,
            pair_poll_per_minute=cfg.auth.pair_poll_per_minute,
        )
    require_session = make_require_session(auth_store=auth_store, enabled=cfg.auth.enabled)
    auth_dep = [Depends(require_session)]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = registry
        app.state.auth_store = auth_store
        # Captured once at lifespan-entry — every ``/whoami`` request
        # diffs against this to compute uptime. UTC throughout so the
        # subtraction is timezone-correct regardless of the host's
        # local clock.
        app.state.started_at = datetime.now(UTC)
        yield
        # No explicit shutdown work in V1: Managers hold no async resources;
        # the store flushes on each save; tmux/git subprocesses are short-lived.

    app = FastAPI(
        title="Grove daemon",
        version="0.0.1",
        lifespan=lifespan,
    )

    # Pairing + sessions router. Mounts before the gated routes so its own
    # per-route auth decisions stay local to ``build_auth_router``.
    app.include_router(build_auth_router(auth_store=auth_store, require_session=require_session))

    def _grove_error_to_http(exc: GroveError) -> HTTPException:
        """Translate engine error subclasses to RFC-shaped HTTP errors.

        Lives inside the factory because every handler in this app uses
        the same envelope shape: ``{"error": <code>, "message": <text>}``
        wrapped under FastAPI's default ``{"detail": ...}`` key.
        """
        # Subclass entries MUST precede their parent — the loop is a linear
        # ``isinstance`` scan, so the first matching key wins. ``BranchError``
        # is the catch-all for any future subclass we forgot to enumerate.
        code_map: dict[type[GroveError], tuple[int, str]] = {
            BranchConflict: (409, "branch_conflict"),
            BranchAlreadyCheckedOut: (409, "branch_already_checked_out"),
            BranchNotFound: (404, "branch_not_found"),
            BranchError: (409, "branch_error"),
            WorkspaceNotFound: (404, "workspace_not_found"),
            WorkspaceStateError: (409, "workspace_state_error"),
        }
        for cls, (status, code) in code_map.items():
            if isinstance(exc, cls):
                return HTTPException(
                    status_code=status,
                    detail={"error": code, "message": str(exc)},
                )
        return HTTPException(
            status_code=500,
            detail={"error": "grove_error", "message": str(exc)},
        )

    def _manager_for(ws_id: str) -> WorkspaceManager:
        """Resolve the workspace's repo and return its Manager.

        Raises HTTPException 404 if no workspace exists with this id, so
        every lifecycle handler gets the same envelope without repeating
        the lookup.
        """
        try:
            state = store.get(ws_id)
        except WorkspaceNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace with id {ws_id!r}",
                },
            ) from exc
        return registry.get(Path(state.repo_root))

    @app.get("/healthz", response_model=HealthView)
    async def healthz() -> HealthView:
        """Public liveness probe — minimal, no host identity, no auth.

        Two fields: ``status`` and ``version``. Hostname / username /
        uptime live behind ``/whoami`` because they identify *who*
        runs the daemon.
        """
        return HealthView(version=_GROVE_VERSION)

    @app.get("/whoami", response_model=WhoamiView, dependencies=auth_dep)
    async def whoami() -> WhoamiView:
        """Authenticated daemon identity + uptime.

        Distinct from ``/auth/sessions/me`` (caller session) — this
        endpoint describes the daemon process itself.
        """
        return _build_whoami(app.state.started_at)

    @app.get("/workspaces", response_model=list[WorkspaceStateView], dependencies=auth_dep)
    async def list_workspaces() -> list[WorkspaceStateView]:
        out: list[WorkspaceStateView] = []
        for repo_root in registry.known_roots():
            mgr = registry.get(repo_root)
            for state in mgr.list():
                out.append(WorkspaceStateView.from_state(state))
        return out

    @app.post("/workspaces", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def create_workspace(req: CreateWorkspaceRequest) -> WorkspaceStateView:
        if req.repo_root is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "repo_root_required",
                    "message": "POST /workspaces requires repo_root in the request body",
                },
            )
        mgr = registry.get(req.repo_root)
        try:
            state = mgr.create(req)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.get("/workspaces/{ws_id}", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def get_workspace(ws_id: str) -> WorkspaceStateView:
        mgr = _manager_for(ws_id)
        try:
            state = mgr.get(ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/pause",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def pause_workspace(ws_id: str, body: _PauseBody) -> WorkspaceStateView:
        mgr = _manager_for(ws_id)
        try:
            state = mgr.pause(ws_id, force=body.force)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/resume",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def resume_workspace(ws_id: str) -> WorkspaceStateView:
        mgr = _manager_for(ws_id)
        try:
            state = mgr.resume(ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/respawn",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def respawn_workspace(ws_id: str) -> WorkspaceStateView:
        mgr = _manager_for(ws_id)
        try:
            state = mgr.respawn(ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post("/workspaces/{ws_id}/kill", status_code=204, dependencies=auth_dep)
    async def kill_workspace(ws_id: str, body: _KillBody) -> None:
        mgr = _manager_for(ws_id)
        try:
            mgr.kill(ws_id, delete_branch=body.delete_branch)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.patch(
        "/workspaces/{ws_id}",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def update_workspace(ws_id: str, body: UpdateWorkspaceRequest) -> WorkspaceStateView:
        """Partial metadata update — title and/or description.

        Wire semantics: ``null`` / omitted = "do not change". Empty
        string in ``description`` clears it; title cannot be cleared.
        Mapping wire → engine kwargs: an absent field translates to
        "kwarg not passed" so the manager's ``_UNSET`` sentinel works.
        """
        mgr = _manager_for(ws_id)
        # Build kwargs dict with str values only — body.title / body.description
        # are str|None, but the `is not None` guards mean we only ever pass
        # strings into the dict. ``str`` typing keeps the **kwargs splat
        # compatible with the manager's ``str | _Unset`` parameter type.
        kwargs: dict[str, str] = {}
        if body.title is not None:
            kwargs["title"] = body.title
        if body.description is not None:
            kwargs["description"] = body.description
        try:
            state = mgr.update(ws_id, **kwargs)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.get(
        "/workspaces/{ws_id}/attach",
        response_model=AttachInstructionView,
        dependencies=auth_dep,
    )
    async def attach_workspace(ws_id: str) -> AttachInstructionView:
        mgr = _manager_for(ws_id)
        try:
            instr = mgr.attach(ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return AttachInstructionView.from_instruction(instr)

    @app.get(
        "/workspaces/{ws_id}/peek",
        response_model=WorkspacePeekView,
        dependencies=auth_dep,
    )
    async def peek_workspace(ws_id: str) -> WorkspacePeekView:
        # peek() is best-effort by contract; it does not raise (CLAUDE.md).
        mgr = _manager_for(ws_id)
        peek = mgr.peek(ws_id)
        return WorkspacePeekView.from_peek(peek)

    @app.get(
        "/workspaces/{ws_id}/commits",
        response_model=list[CommitSummaryView],
        dependencies=auth_dep,
    )
    async def workspace_commits(ws_id: str) -> list[CommitSummaryView]:
        """Comprehensive branch history (``git log base..branch``).

        Distinct from ``peek.recent_commits`` which is a tight 3-row
        rail summary walking all of branch history. This route returns
        every commit done in the workspace since fork from base, newest
        first, uncapped — the detail-page consumer wants the full log.
        Best-effort like peek; never raises, returns ``[]`` on failure.
        """
        mgr = _manager_for(ws_id)
        commits = mgr.commits(ws_id)
        return [CommitSummaryView.from_summary(c) for c in commits]

    @app.get(
        "/branches",
        response_model=list[BranchInfo],
        dependencies=auth_dep,
    )
    async def list_branches(
        repo: Annotated[Path, Query()],
        scope: Annotated[Literal["local", "remote"], Query()],
    ) -> list[BranchInfo]:
        mgr = registry.get(repo)
        try:
            branches = mgr.list_local_branches() if scope == "local" else mgr.list_remote_branches()
        except GroveError as exc:
            # A non-repo ``repo`` query parameter surfaces as ``GitError``
            # from the engine; route it through the same envelope the rest
            # of the daemon uses so clients see a consistent error shape.
            raise _grove_error_to_http(exc) from exc
        return list(branches)

    return app
