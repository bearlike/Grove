"""FastAPI factory + lifespan + route handlers for the Grove daemon.

All routes are 1:1 with ``WorkspaceManager`` methods. Multi-repo dispatch
goes through ``RepoRegistry``. No WebSocket — clients poll. No auth —
the daemon listens on loopback only; remote access is via SSH tunnel.
"""

from __future__ import annotations

import asyncio
import getpass
import platform
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from grove import __version__ as _GROVE_VERSION
from grove.core.activity import ActivityService
from grove.core.agents import resolve_models
from grove.core.agents.hook import HOOK_INGEST_ROUTE
from grove.core.auth import SessionStore
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.activity import DashboardEvent, DashboardSnapshotView
from grove.core.contracts.agents import AgentSummaryView
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.issueops import IssueOpsEvent, IssueOpsOutcome
from grove.core.contracts.questions import QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest, UpdateWorkspaceRequest
from grove.core.contracts.sessions import (
    RemapSessionRequest,
    SessionControlsView,
    SessionDetailView,
    SessionSummaryView,
    TodoListView,
)
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.contracts.views import (
    AttachInstructionView,
    CommitSummaryView,
    HealthView,
    WhoamiView,
    WorkspacePaneView,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.errors import (
    AgentSessionNotFound,
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchError,
    BranchNotFound,
    CapabilityUnavailable,
    GroveError,
    PaneNotFound,
    QuestionAnswerInvalid,
    QuestionNotPending,
    ResumeNotSupported,
    SteeringUnsupported,
    TicketProviderError,
    TicketProviderNotConfigured,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.issueops import IssueOpsEngine, TicketStatusPublisher
from grove.core.manager import WorkspaceManager
from grove.core.notifications import NotificationBroker
from grove.core.release import ReleaseChecker, ReleaseStatus
from grove.core.sessions import SessionExplorer, SessionListing
from grove.core.store import JsonWorkspaceStore
from grove.daemon._pane_stream import _PaneStreamer
from grove.daemon._sse import _SseHub
from grove.daemon.auth import build_auth_router, make_require_hook_token, make_require_session
from grove.daemon.repos import RepoRegistry

# How often the lifespan task recomputes activity and emits ``session_activity``
# deltas. Transcript/pane changes aren't lifecycle events, so this poll is what
# streams them; lifecycle changes (create/kill) arrive promptly via the bus. A
# couple seconds matches the dashboard's slow-tick feel without hammering git/tmux.
_POLL_INTERVAL_SECONDS = 2.0


def _build_whoami(started_at: datetime, release: ReleaseStatus) -> WhoamiView:
    """Snapshot the daemon's identity + uptime + release skew.

    Pure: reads stdlib state at call time (``socket.gethostname``,
    ``getpass.getuser``, ``platform.*``), takes ``started_at`` and the
    pre-resolved ``release`` status as input so tests can pin both
    deterministically. ``int(...)`` truncates rather than rounds — uptime is a
    coarse signal, sub-second precision is noise. ``release`` is resolved off
    the loop (executor) at the route edge; mapping it here keeps this builder
    free of I/O.
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
        latest_version=release.latest,
        update_available=release.update_available,
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


class _SendMessageBody(BaseModel):
    """Steer request body — the follow-up text typed into the agent pane.

    ``min_length=1``: an empty steer is always a client bug; refusing it
    at validation (422) keeps the engine's typed-error surface for real
    state problems. Module-scope for the same forward-ref reason as
    ``_PauseBody`` above.
    """

    text: str = Field(min_length=1)


class _InvokeControlBody(BaseModel):
    """Trigger a named session control — a slash command or a skill (#178).

    ``name`` is a control name from ``GET .../controls`` (a command/skill is
    invoked as ``/name``); the leading slash is optional (the engine strips it).
    Module-scope for the same forward-ref reason as ``_SendMessageBody``.
    """

    name: str = Field(min_length=1)


class _SwitchModelBody(BaseModel):
    """Switch the running session's model (#178) — ``model`` is any id, forwarded
    verbatim (the provider boundary; the engine never validates it against the
    offered catalog). Module-scope for the same forward-ref reason above."""

    model: str = Field(min_length=1)


class _HookIngestBody(BaseModel):
    """Native Claude Code http-hook payload (#171) — permissive by design.

    The payload shape varies per event (``session_id``/``cwd``/``tool_name``/
    ``tool_use_id``/...); this route only needs enough to confirm a real hook
    fired, never a full parse — that's the sidecar the command handler
    already wrote. ``extra="allow"`` lets every other Claude Code field ride
    through untouched rather than pinning a contract that drifts with each
    event type Anthropic adds. Module-scope for the same forward-ref reason as
    ``_PauseBody`` above.
    """

    model_config = ConfigDict(extra="allow")

    session_id: str = Field(min_length=1)


def _sse_frame(event: DashboardEvent) -> str:
    """Format one ``DashboardEvent`` as an SSE wire frame.

    ``id:`` is the monotonic seq the client echoes back as ``Last-Event-ID``;
    ``event:`` is the kind a browser ``EventSource`` listener dispatches on;
    ``data:`` is the JSON body. The blank line terminates the frame.
    """
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n"


def _parse_last_event_id(raw: str | None) -> int | None:
    """Parse the ``Last-Event-ID`` header to an int seq, tolerating junk → ``None``."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _poll_loop(
    service: ActivityService,
    interval: float,
    stop_event: asyncio.Event,
) -> None:
    """Drive ``ActivityService.poll_once`` on a slow interval until shutdown.

    ``poll_once`` does blocking git/tmux I/O, so it runs in the default executor
    to keep the event loop responsive. Failures are logged and swallowed — one bad
    tick must not kill the stream (best-effort, like peek). The wait races the
    ``stop_event`` so shutdown is prompt rather than blocking out the interval.
    """
    loop = asyncio.get_running_loop()
    while not stop_event.is_set():
        try:
            await loop.run_in_executor(None, service.poll_once)
        except Exception as exc:  # a bad tick must never tear down the lifespan task
            logger.warning("activity poll_once failed: {}", exc)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)


def build_app(  # noqa: PLR0915
    *,
    cfg: GroveConfig,
    store: JsonWorkspaceStore,
    auth_store: SessionStore | None = None,
    notification_broker: NotificationBroker | None = None,
    release_checker: ReleaseChecker | None = None,
    issue_ops_engine: IssueOpsEngine | None = None,
    status_publisher: TicketStatusPublisher | None = None,
) -> FastAPI:
    """Construct the daemon's FastAPI app.

    Tests call this directly; the CLI's ``serve`` calls it via uvicorn.
    ``auth_store`` is constructed from ``cfg.auth`` if not supplied — tests
    inject one with a fake clock when they need to control TTLs.
    ``notification_broker`` is built from ``cfg.notifications`` if not supplied —
    tests inject one with a capturing channel to assert the edge-trigger wiring.
    ``status_publisher`` is built from ``cfg.issueops`` if not supplied (``None``
    when issue-ops is disabled) — tests inject a capturing one to assert the
    lifespan bind/close wiring, mirroring ``notification_broker``.
    ``release_checker`` defaults to a real GitHub-backed one — tests inject one
    with a fake fetcher so ``/whoami`` never touches the network.

    The statement count grows linearly with route count (this is FastAPI's
    factory pattern); the function still has one job — register routes —
    so PLR0915 doesn't flag a real concern here.
    """
    # Resolve each repo's own cascade at first access — `cfg` here is the
    # global daemon config (loaded `repo_root=None`, the auth/daemon source);
    # a project's agents + init_script live in `<repo>/.grove/config.json`
    # and would be invisible to `create` without the per-repo loader (#46/#47).
    registry = RepoRegistry(cfg=cfg, store=store, config_loader=load_config)
    activity_service = ActivityService(registry=registry)
    # The outbound face (#197): the live sticky status comment. Built from
    # `cfg.issueops` (None when disabled), bound to the activity bus in the
    # lifespan like `notification_broker`, and injected into the engine below so
    # the `@grove status` verb forces an immediate re-render. Injectable for tests.
    if status_publisher is None:
        status_publisher = TicketStatusPublisher.from_config(cfg.issueops, registry=registry)
    # The issue-ops router (#196): resolves an event's repo through the SAME
    # per-repo registry every other route dispatches on, so a forwarded comment
    # steers/creates against the target repo's own cascade. The status publisher
    # rides its `StatusPublisher` seam (structural, no engine↔publisher import).
    # Injectable for tests.
    if issue_ops_engine is None:
        issue_ops_engine = IssueOpsEngine(registry=registry, status_publisher=status_publisher)
    sse_hub = _SseHub(activity_service)
    if notification_broker is None:
        notification_broker = NotificationBroker.from_config(cfg.notifications)
    if release_checker is None:
        release_checker = ReleaseChecker()
    if auth_store is None:
        auth_store = SessionStore(
            session_ttl=timedelta(seconds=cfg.auth.session_ttl_seconds),
            pairing_ttl=timedelta(seconds=cfg.auth.pairing_ttl_seconds),
            pair_init_per_minute=cfg.auth.pair_init_per_minute,
            pair_poll_per_minute=cfg.auth.pair_poll_per_minute,
        )
    require_session = make_require_session(auth_store=auth_store, enabled=cfg.auth.enabled)
    auth_dep = [Depends(require_session)]
    # Same config flag, a DIFFERENT mechanism (#171): the hook-ingest route
    # can't ask a human to approve a pairing challenge (see `make_require_hook_token`).
    require_hook_token = make_require_hook_token(enabled=cfg.auth.enabled)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = registry
        app.state.auth_store = auth_store
        app.state.activity = activity_service
        app.state.sse_hub = sse_hub
        # Captured once at lifespan-entry — every ``/whoami`` request
        # diffs against this to compute uptime. UTC throughout so the
        # subtraction is timezone-correct regardless of the host's
        # local clock.
        app.state.started_at = datetime.now(UTC)
        # Bridge the sync activity bus to the loop and start the slow activity
        # poll. The hub must bind the *running* loop so its cross-thread
        # ``call_soon_threadsafe`` targets the right one.
        sse_hub.start(asyncio.get_running_loop())
        # Subscribe the notification broker to the SAME activity bus the SSE hub
        # rides — a debounced edge-trigger, no new status computation (#70). Its
        # dispatch worker keeps channel HTTP off the activity poll thread.
        if notification_broker is not None:
            notification_broker.bind(activity_service.subscribe)
            app.state.notification_broker = notification_broker
        # The issue-ops status publisher (#197) is the bus's third subscriber
        # (alongside the SSE hub + notification broker) — same discipline: bind
        # after `sse_hub.start`, close on shutdown. None when issue-ops is off.
        if status_publisher is not None:
            status_publisher.bind(activity_service.subscribe)
            app.state.status_publisher = status_publisher
        stop_event = asyncio.Event()
        poll_task = asyncio.create_task(
            _poll_loop(activity_service, _POLL_INTERVAL_SECONDS, stop_event)
        )
        try:
            yield
        finally:
            stop_event.set()
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task
            if notification_broker is not None:
                notification_broker.close()
            if status_publisher is not None:
                status_publisher.close()
            sse_hub.stop()
            activity_service.close()

    app = FastAPI(
        title="Grove daemon",
        # Derive from the package version so OpenAPI's ``info.version`` tracks
        # ``grove.__version__`` (and ``pyproject``) instead of drifting on a
        # hand-edited literal — the same single source ``/healthz`` + ``/whoami``
        # report.
        version=_GROVE_VERSION,
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
            # Steering refusals (#37). PaneNotFound is 409 like the state
            # errors: the live session's current shape conflicts with the
            # request and a respawn can fix it. SteeringUnsupported is 501:
            # a capability gap (the agent kind has no implementation for
            # the op) — no state change makes a retry succeed, which is
            # exactly the false promise a 409 would make.
            PaneNotFound: (409, "pane_not_found"),
            SteeringUnsupported: (501, "steering_unsupported"),
            # Control triggers (#178). Capability-based like SteeringUnsupported —
            # the runtime (a generic shell / remote session) has no in-session
            # slash-control surface, so no state change makes a retry succeed. 501.
            CapabilityUnavailable: (501, "capability_unavailable"),
            # Live-question answering (#109). QuestionNotPending is 409, like the
            # state errors: the request was well-formed, the live question just
            # moved on (answered in the terminal, or superseded) — the client
            # drops its pending card. QuestionAnswerInvalid is 422: the plan is
            # malformed for the captured questions (bad length/index/kind), which
            # no state change fixes.
            QuestionNotPending: (409, "question_not_pending"),
            QuestionAnswerInvalid: (422, "question_answer_invalid"),
            # Agent-transcript sessions; the auth domain's `session_not_found`
            # (revoked bearer sessions) lives in the auth router. Also the
            # remap verb's not-found/ambiguous session-ref (#120), re-raised in
            # this domain by remap_session so it never falls through to 500.
            AgentSessionNotFound: (404, "agent_session_not_found"),
            # Resume-into-workspace (#120): a create named resume_session_id for
            # an agent kind with no resume handle (mewbo/generic). 422 — the
            # request is well-formed but semantically invalid for this agent, no
            # state change fixes it (mirrors question_answer_invalid).
            ResumeNotSupported: (422, "resume_not_supported"),
            # Ticket providers (#7). NotConfigured is 404 — the named tracker
            # simply isn't enabled for this repo (nothing went wrong on the
            # wire). TicketProviderError is 502 — the upstream tracker API
            # failed (transport, auth, malformed), which is not the client's
            # fault. NotConfigured is a sibling of (not a subclass of)
            # TicketProviderError, so order between them is immaterial.
            TicketProviderNotConfigured: (404, "ticket_provider_not_configured"),
            TicketProviderError: (502, "ticket_provider_error"),
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

        The release-skew check rides the executor: ``check()`` is cached for
        hours, so this is a no-op cache read on all but the occasional refresh
        tick — and a refresh's blocking GitHub GET runs off the event loop,
        never stalling the handler (best-effort, like every other side effect).
        """
        loop = asyncio.get_running_loop()
        release = await loop.run_in_executor(None, release_checker.check)
        return _build_whoami(app.state.started_at, release)

    @app.get("/activity", response_model=DashboardSnapshotView, dependencies=auth_dep)
    async def activity() -> DashboardSnapshotView:
        """One-shot cross-project dashboard snapshot.

        ``snapshot()`` does blocking git/tmux I/O, so it runs in the executor to
        keep the loop responsive under concurrent requests.
        """
        loop = asyncio.get_running_loop()
        snap = await loop.run_in_executor(None, activity_service.snapshot)
        return DashboardSnapshotView.from_snapshot(snap)

    @app.post(
        HOOK_INGEST_ROUTE,
        status_code=204,
        dependencies=[Depends(require_hook_token)],
    )
    async def ingest_agent_hook(body: _HookIngestBody) -> None:
        """Native Claude Code http-hook push (#171) — the live half of the #18 sidecar.

        Claude Code dispatches the ``command`` and ``http`` handlers registered
        on the SAME event independently (`ClaudeHook.settings`), so by the time
        this request lands the command handler has already written the
        sidecar — this route's only job is collapsing the ~2s poll-tick lag
        into an immediate recompute, never a second sidecar write (this
        payload carries no ``$TMUX_PANE``, so writing here would race the
        command handler's more complete record). ``poll_once`` already diffs
        per-workspace by fingerprint and emits a delta only for what changed,
        so this is a scoped refresh by construction, not a blanket resnapshot.

        Gated by the same-host hook-ingest token (`make_require_hook_token`),
        not the `SessionStore` pairing bearer every other route uses — see its
        docstring for why.
        """
        del body  # session_id only justifies the call; poll_once() rescans everyone
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, activity_service.poll_once)

    @app.get(
        "/events",
        dependencies=auth_dep,
        responses={
            200: {
                "model": DashboardEvent,
                "description": (
                    "text/event-stream of DashboardEvent JSON objects. The first "
                    "frame is a `snapshot` (or a `Last-Event-ID` replay); subsequent "
                    "frames are `session_activity` / `workspace_changed` deltas."
                ),
            }
        },
    )
    async def events(request: Request) -> StreamingResponse:
        """SSE activity stream: a snapshot on connect, then live deltas.

        Bridges the per-connection bounded queue (via ``_SseHub``) to the wire.
        Reconnects carrying ``Last-Event-ID`` replay missed deltas from the ring
        buffer when the gap is small enough; otherwise they get a fresh snapshot.
        A wedged client only ever loses its own buffered events (drop-oldest),
        never back-pressures the engine.

        Auth is the normal bearer dependency: a browser ``EventSource`` can't set
        headers, so the webapp's BFF calls this server-side with the token and the
        browser authenticates to the BFF by cookie — the token never reaches the
        browser.
        """
        loop = asyncio.get_running_loop()
        last_event_id = _parse_last_event_id(request.headers.get("last-event-id"))

        async def stream() -> AsyncIterator[str]:
            # Starlette's StreamingResponse runs a disconnect watcher that cancels
            # this generator when the client goes away — so there is no manual
            # is_disconnected() poll; the `finally` (unregister) runs on that
            # cancellation. The keepalive comment fires when no event arrives
            # within the window, so proxies and the browser see a live connection.
            queue = sse_hub.register()
            try:
                if last_event_id is not None and sse_hub.can_replay(last_event_id):
                    for missed in sse_hub.replay_since(last_event_id):
                        yield _sse_frame(missed)
                else:
                    snap = await loop.run_in_executor(None, activity_service.snapshot)
                    yield _sse_frame(
                        DashboardEvent.snapshot_event(snap, seq=activity_service.next_seq())
                    )
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse_frame(event)
            finally:
                sse_hub.unregister(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/workspaces", response_model=list[WorkspaceStateView], dependencies=auth_dep)
    async def list_workspaces(
        repo: Annotated[Path, Query()] | None = None,
        ticket: Annotated[str, Query()] | None = None,
    ) -> list[WorkspaceStateView]:
        """Cross-repo (default) or single-repo (``repo=``) workspace listing.

        ``repo`` dispatches like ``/branches``: given, it scopes to that repo
        and validates it — an unrecognized root is 404 ``unknown_repo_root``
        (the ``/sessions`` precedent) rather than an empty list, so a typo'd
        path can't masquerade as "no workspaces". ``ticket`` (wire format
        ``<provider>:<id>``) narrows to the single workspace
        ``WorkspaceManager.find_by_ticket`` resolves for that ticket — the
        issue-ops "does a workspace already exist for this ticket" lookup —
        scanned within ``repo`` when given, else across every known repo.
        """
        ticket_filter: tuple[str, str] | None = None
        if ticket is not None:
            provider, sep, ticket_id = ticket.partition(":")
            if not sep or not provider or not ticket_id:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_ticket_filter",
                        "message": f"ticket filter must be '<provider>:<id>', got {ticket!r}",
                    },
                )
            ticket_filter = (provider, ticket_id)

        if repo is not None:
            root = repo.resolve()
            if root not in {known.resolve() for known in registry.known_roots()}:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "unknown_repo_root",
                        "message": f"no Grove workspaces recorded under {repo}",
                    },
                )
            roots = [root]
        else:
            roots = list(registry.known_roots())

        out: list[WorkspaceStateView] = []
        for repo_root in roots:
            mgr = registry.get(repo_root)
            if ticket_filter is not None:
                match = mgr.find_by_ticket(*ticket_filter)
                if match is not None:
                    out.append(WorkspaceStateView.from_state(match))
                continue
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

    @app.post(
        "/issue-ops/events",
        status_code=202,
        response_model=IssueOpsOutcome,
        dependencies=auth_dep,
    )
    async def ingest_issue_ops_event(event: IssueOpsEvent) -> IssueOpsOutcome:
        """Ingest one forwarded issue-comment event and route it to a workspace action (#196).

        The CI forwarder (a stateless composite action on a ``:host`` runner)
        POSTs a normalized :class:`IssueOpsEvent`; the engine dedupes, gates, and
        routes it to the target repo's Manager, returning an
        :class:`IssueOpsOutcome` the action reflects into a comment reaction. 202
        (accepted-and-acted) because the real work — a create/steer, a reply — is
        already done synchronously in the engine; the code only signals the CI
        that this is a fire-and-forget ingest, not a resource creation with a
        canonical URL.

        ``handle`` does blocking git/tmux/network I/O (``find_by_ticket`` scans,
        ``create``, the reply comment), so it runs in the executor to keep the
        loop responsive — the same discipline as ``/activity``. It catches its own
        lifecycle errors and turns them into ``refused`` outcomes, so it returns an
        outcome rather than raising for an ordinary refusal.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, issue_ops_engine.handle, event)

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

    @app.post("/workspaces/{ws_id}/message", status_code=204, dependencies=auth_dep)
    async def send_workspace_message(ws_id: str, body: _SendMessageBody) -> None:
        """Steer the workspace's agent with a follow-up message (issue #37).

        Empty 204 on success — the injection has no meaningful response
        body. Refusals ride the typed-error envelope: 409
        ``workspace_state_error`` / ``pane_not_found``, 501
        ``steering_unsupported``.
        """
        mgr = _manager_for(ws_id)
        try:
            mgr.send_message(ws_id, body.text)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/interrupt", status_code=204, dependencies=auth_dep)
    async def interrupt_workspace(ws_id: str) -> None:
        """Interrupt the workspace's agent, where its adapter supports it.

        Today every kind refuses (501 ``steering_unsupported``) — there is
        no safe generic interrupt for a tmux-hosted CLI, and the mewbo API
        arm lands with issue #36. The route exists now so clients code
        against the final surface.
        """
        mgr = _manager_for(ws_id)
        try:
            mgr.interrupt(ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/question-answer", status_code=204, dependencies=auth_dep)
    async def answer_question(ws_id: str, body: QuestionAnswerRequest) -> None:
        """Answer a pending AskUserQuestion by driving the agent's TUI (#109).

        Dispatch semantics — 204 the instant the keystrokes are sent; the
        resolution arrives later on the activity stream (the sidecar clears and
        the transcript flushes). Refusals ride the typed-error envelope: 404
        ``workspace_not_found``, 409 ``question_not_pending`` (stale/absent
        ``tool_use_id``) / ``pane_not_found``, 422 ``question_answer_invalid``
        (plan doesn't fit the captured questions). The wire model rejects a
        structurally-malformed body (422) before the handler runs.
        """
        mgr = _manager_for(ws_id)
        try:
            mgr.answer_question(ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/controls",
        response_model=SessionControlsView,
        dependencies=auth_dep,
    )
    async def workspace_controls(ws_id: str) -> SessionControlsView:
        """Enumerate the session's available input controls (#178).

        Slash commands, skills, MCP servers, the model catalog + current model,
        and the permission posture — the read behind the webapp's control panel.
        Fetch-on-demand by design (never SSE, like the session-history reads). The
        scan touches disk and the current-model read parses a transcript, so it
        runs in the executor like ``/sessions``. ``session_controls`` is
        best-effort (the ``peek`` discipline): a fs/parse hiccup yields an empty
        surface rather than a 500 — a bad workspace id is still the 404 from
        ``_manager_for``.
        """
        mgr = _manager_for(ws_id)
        loop = asyncio.get_running_loop()
        controls = await loop.run_in_executor(None, mgr.session_controls, ws_id)
        return SessionControlsView.from_controls(controls)

    @app.post("/workspaces/{ws_id}/controls/invoke", status_code=204, dependencies=auth_dep)
    async def invoke_workspace_control(ws_id: str, body: _InvokeControlBody) -> None:
        """Invoke a named session control — a slash command or a skill (#178).

        Composes the tool's ``/name`` invocation and delivers it through the same
        steer path as ``/message`` — 204 on dispatch (delivered, not "ran"; the
        result rides the transcript later). Refusals ride the typed envelope: 501
        ``capability_unavailable`` (a shell/remote kind has no slash-control
        surface), 409 ``pane_not_found`` / ``workspace_state_error``.
        """
        mgr = _manager_for(ws_id)
        try:
            mgr.invoke_control(ws_id, body.name)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/controls/model", status_code=204, dependencies=auth_dep)
    async def switch_workspace_model(ws_id: str, body: _SwitchModelBody) -> None:
        """Switch the running session's model (#178).

        Delivered as the interactive ``/model <id>`` control through the steer
        path — 204 on dispatch. The id is forwarded verbatim (the provider
        boundary). Refusals: 501 ``capability_unavailable`` (a kind with no
        model-switch channel), 409 ``pane_not_found`` / ``workspace_state_error``.
        """
        mgr = _manager_for(ws_id)
        try:
            mgr.switch_model(ws_id, body.model)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/session",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def remap_workspace_session(ws_id: str, body: RemapSessionRequest) -> WorkspaceStateView:
        """Pin an existing agent session as this workspace's tracked primary (#120).

        The manual counterpart to Grove's automatic discovery/adoption: the
        operator names a session (id or unique prefix, resolved in the
        workspace's project scope) and it becomes the persisted
        ``agent_session_id``. Trusted — no birth-gate, mirroring
        ``attach_ticket``; idempotent by resolved id. Returns the updated
        workspace. Refusals ride the envelope: 404 ``workspace_not_found`` /
        ``agent_session_not_found`` (the ref resolves nowhere, or is ambiguous),
        409 ``workspace_state_error`` (ORPHANED).
        """
        mgr = _manager_for(ws_id)
        # remap_session resolves the ref through SessionExplorer, which
        # full-parses every transcript across every worktree — blocking I/O, so
        # off-load it to the executor exactly like the sibling session-scan
        # endpoints (#F3) rather than stalling the event loop.
        loop = asyncio.get_running_loop()
        try:
            state = await loop.run_in_executor(None, mgr.remap_session, ws_id, body.session_ref)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

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
        "/workspaces/{ws_id}/pane",
        response_model=WorkspacePaneView,
        dependencies=auth_dep,
    )
    async def workspace_pane(ws_id: str) -> WorkspacePaneView:
        """One-shot agent-pane ANSI snapshot for the dashboard's focused live pane (#19).

        The web dashboard polls this for the single expanded card (status-gated to
        WORKING) instead of mounting N live terminals — the peer-validated "summary
        wall + one live focus" shape. Best-effort like peek; never raises (returns
        ``ansi: null`` when the session isn't live).
        """
        mgr = _manager_for(ws_id)
        snapshot, taken_at = mgr.peek_pane(ws_id)
        return WorkspacePaneView.from_capture(ws_id, snapshot, taken_at)

    @app.get(
        "/workspaces/{ws_id}/pane/stream",
        dependencies=auth_dep,
        responses={
            200: {
                "model": DashboardEvent,
                "description": (
                    "text/event-stream of `pane_snapshot` DashboardEvent frames for "
                    "this one workspace's agent pane (#19). A push upgrade of "
                    "`GET .../pane`: the daemon captures ~1 Hz and emits a frame only "
                    "when the pane changed (else a keepalive comment). The client opens "
                    "this for the single focused WORKING card and closes it on blur, so "
                    "off-screen/idle panes cost nothing."
                ),
            }
        },
    )
    async def workspace_pane_stream(ws_id: str) -> StreamingResponse:
        """Live focused-pane SSE push for one workspace (#19, the streaming wall).

        Resolves the workspace once (404 if unknown), then self-paces: each tick
        captures the agent pane via the same best-effort ``peek_pane`` seam the
        one-shot route uses, off-loaded to the executor so the blocking tmux read
        never stalls the loop. A workspace killed mid-stream degrades to an empty
        pane (best-effort, like peek) rather than tearing the connection down — the
        client drops the focus on the next activity tick and closes the stream.
        """
        mgr = _manager_for(ws_id)
        loop = asyncio.get_running_loop()

        async def _capture() -> tuple[str | None, datetime | None]:
            def _read() -> tuple[str | None, datetime | None]:
                try:
                    return mgr.peek_pane(ws_id)
                except GroveError:
                    # Killed/vanished mid-stream — empty pane, never raise.
                    return None, None

            return await loop.run_in_executor(None, _read)

        streamer = _PaneStreamer(
            workspace_id=ws_id, capture=_capture, next_seq=activity_service.next_seq
        )

        async def stream() -> AsyncIterator[str]:
            async for event in streamer.events():
                yield _sse_frame(event) if event is not None else ": keepalive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

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
        "/workspaces/{ws_id}/sessions",
        response_model=list[SessionSummaryView],
        dependencies=auth_dep,
    )
    async def workspace_sessions(
        ws_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        candidates: Annotated[bool, Query()] = False,
    ) -> list[SessionSummaryView]:
        """Every agent session recorded for the workspace's directory, newest-first.

        Fetch-on-demand by design — session history never rides the SSE stream.
        The scan full-parses each transcript in one cwd (the documented
        ``list_sessions`` cost model), so it runs in the executor like
        ``/activity``.

        ``candidates=true`` flips the scan to the UNGATED
        :meth:`SessionExplorer.candidates_for` — the remap-picker set (#132),
        which keeps a session the adoption gate rejects (a dead-minted-pointer's
        pre-birth successor, a foreign session in a shared ROOT cwd) so a UI can
        offer it to pin via ``POST .../session``. The default gated view stays
        the workspace's own attributed history.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)
        scan = explorer.candidates_for if candidates else explorer.for_workspace
        loop = asyncio.get_running_loop()
        try:
            listings = await loop.run_in_executor(None, scan, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return [SessionSummaryView.from_listing(ls) for ls in listings[:limit]]

    @app.get(
        "/workspaces/{ws_id}/sessions/{session_id}/turns",
        response_model=SessionDetailView,
        dependencies=auth_dep,
    )
    async def workspace_session_turns(
        ws_id: str,
        session_id: str,
        last: Annotated[int | None, Query(ge=1)] = None,
    ) -> SessionDetailView:
        """The session's conversation, oldest-first; ``last`` keeps only the tail.

        ``session_id`` must be the full id (clients hold it from the sessions
        listing) — prefix resolution stays a CLI affordance. 404
        ``agent_session_not_found`` when the id isn't recorded for this
        workspace.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _read() -> SessionDetailView:
            listings = explorer.for_workspace(ws_id)
            listing: SessionListing | None = next(
                (ls for ls in listings if ls.summary.session_id == session_id), None
            )
            if listing is None:
                raise AgentSessionNotFound(
                    f"no session {session_id!r} recorded for workspace {ws_id!r}"
                )
            return SessionDetailView.from_listing_turns(
                listing, explorer.turns_for(listing, last=last)
            )

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/todo",
        response_model=TodoListView,
        dependencies=auth_dep,
    )
    async def workspace_todo(ws_id: str) -> TodoListView:
        """The workspace's current todo/checklist state (#194).

        Fetch-on-demand like the sibling session-history routes above, bounded
        to one workspace and resolved through ``WorkspaceManager.latest_todo``
        — the same engine seam the issueops sticky-comment publisher calls
        in-process. That method folds a full transcript parse, so it runs in
        the executor. 404 ``agent_session_not_found`` when the workspace has
        no recorded agent session; a session with no todo/Task tool called yet
        answers 200 with an empty ``TodoListView`` (a real, not-yet-populated
        state — never conflated with the 404).
        """
        mgr = _manager_for(ws_id)
        loop = asyncio.get_running_loop()
        try:
            todo = await loop.run_in_executor(None, mgr.latest_todo, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return TodoListView.from_todo(todo) if todo is not None else TodoListView()

    @app.get("/sessions", response_model=list[SessionSummaryView], dependencies=auth_dep)
    async def project_sessions(
        repo: Annotated[Path, Query()],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[SessionSummaryView]:
        """Every agent session across one project's worktrees, newest-first.

        The project-landing analogue of ``GET /workspaces/{id}/sessions``: spans
        every scan root (Grove-managed and hand-staged worktrees alike), so rows
        carry the ``workspace_*`` attribution trio when Grove owns the directory
        and ``None`` when staged by hand. ``repo`` follows the ``/branches``
        convention for repo dispatch; an unknown root is a 404 rather than an
        empty list so a typo'd path can't masquerade as "no sessions yet".

        ``SessionExplorer.list`` full-parses every transcript across every
        worktree — heavy per request, acceptable here because the UI fetches it
        only when the user expands the collapsed sessions section. Blocking I/O,
        so it runs in the executor like the sibling sessions endpoints.
        """
        root = repo.resolve()
        if root not in {known.resolve() for known in registry.known_roots()}:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "unknown_repo_root",
                    "message": f"no Grove workspaces recorded under {repo}",
                },
            )
        explorer = SessionExplorer(registry.get(root))
        loop = asyncio.get_running_loop()
        try:
            listings = await loop.run_in_executor(None, lambda: explorer.list(limit=limit))
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return [SessionSummaryView.from_listing(ls) for ls in listings]

    @app.get("/agents", response_model=list[AgentSummaryView], dependencies=auth_dep)
    async def list_agents(repo: Annotated[Path, Query()]) -> list[AgentSummaryView]:
        """Configured agents for one repo's cascade — the new-workspace picker source.

        The TUI reads ``cfg.agents`` in-process to build its create-modal dropdown;
        a remote create form can't, so this returns the same merged list. ``repo``
        dispatches like ``/branches`` (per-repo cascade), so a project-scoped agent
        defined in ``<repo>/.grove/config.json`` shows up here too. Read-only and
        non-git, so it can't raise — an arbitrary path just yields the default
        cascade. Each row's ``models`` catalog is resolved via the single
        ``resolve_models`` seam (config override, else live adapter discovery);
        Codex discovery shells out (``codex debug models``), so the whole list is
        built in the executor to keep that subprocess off the event loop.
        """
        mgr = registry.get(repo)
        agents = mgr.config.agents

        def _build() -> list[AgentSummaryView]:
            return [
                AgentSummaryView.from_spec(
                    spec,
                    models=resolve_models(
                        kind=spec.kind, command=spec.command, configured=spec.models
                    ),
                )
                for spec in agents
            ]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _build)

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

    @app.get(
        "/tickets/providers",
        response_model=list[TicketProviderView],
        dependencies=auth_dep,
    )
    async def ticket_providers(repo: Annotated[Path, Query()]) -> list[TicketProviderView]:
        """The repo's enabled ticket providers — the client's picker source (#7).

        ``repo`` dispatches per-repo cascade like ``/branches``: a project
        enables its tracker in ``<repo>/.grove/config.json``. Pure (no network);
        each row's ``configured`` flag tells a client to gray out a provider that
        is enabled but missing its token rather than offer a dead picker.
        """
        mgr = registry.get(repo)
        return mgr.ticket_providers.provider_views()

    @app.get(
        "/tickets/assigned",
        response_model=list[TicketRef],
        dependencies=auth_dep,
    )
    async def tickets_assigned(
        repo: Annotated[Path, Query()],
        provider: TicketProviderName | None = None,
        status: str | None = None,
    ) -> list[TicketRef]:
        """Tickets assigned to the authenticated user (#7).

        With ``provider`` given, query exactly that tracker. Without it,
        aggregate across every enabled provider, SKIPPING any whose credential
        is absent (``configured`` is False) — so a half-configured repo still
        returns its working providers' tickets instead of failing the whole
        request on one unconfigured tracker. A configured provider whose API
        call fails still surfaces its 502.
        """
        mgr = registry.get(repo)
        try:
            if provider is not None:
                return mgr.ticket_providers.get(provider).list_assigned(status=status)
            out: list[TicketRef] = []
            for p in mgr.ticket_providers.providers():
                if not p.configured:
                    continue
                out.extend(p.list_assigned(status=status))
            return out
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/tickets/{provider}/{ticket_id}",
        response_model=TicketRef,
        dependencies=auth_dep,
    )
    async def get_ticket(
        provider: TicketProviderName,
        ticket_id: str,
        repo: Annotated[Path, Query()],
    ) -> TicketRef:
        """Fetch one ticket by its canonical key (#7).

        404 ``ticket_provider_not_configured`` when the named tracker isn't
        enabled; 502 ``ticket_provider_error`` when the upstream API fails.
        """
        mgr = registry.get(repo)
        try:
            return mgr.ticket_providers.get(provider).get_ticket(ticket_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/tickets",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def attach_ticket(ws_id: str, body: TicketSelector) -> WorkspaceStateView:
        """Manually associate a ticket with a workspace (#7).

        Pure association (no network): the selector reuses the contract
        ``TicketSelector`` as the body. Idempotent by ``(provider, id)`` in the
        engine. Returns the updated workspace with its refreshed ``ticket_refs``.
        """
        mgr = _manager_for(ws_id)
        try:
            state = mgr.attach_ticket(ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.delete(
        "/workspaces/{ws_id}/tickets/{provider}/{ticket_id}",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def detach_ticket(
        ws_id: str,
        provider: TicketProviderName,
        ticket_id: str,
    ) -> WorkspaceStateView:
        """Remove a ticket association (#7). Idempotent — a missing ref is a no-op."""
        mgr = _manager_for(ws_id)
        try:
            state = mgr.detach_ticket(ws_id, provider, ticket_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    return app
