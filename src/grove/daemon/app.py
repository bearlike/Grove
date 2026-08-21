"""FastAPI factory + lifespan + route handlers for the Grove daemon.

All routes are 1:1 with ``WorkspaceManager`` methods. Multi-repo dispatch
goes through ``RepoRegistry``. No WebSocket — clients poll. No auth —
the daemon listens on loopback only; remote access is via SSH tunnel.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import platform
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from grove import __version__ as _GROVE_VERSION
from grove.core import paths as core_paths
from grove.core.activity import ActivityService
from grove.core.agents import get_adapter, resolve_models
from grove.core.agents.hook import HOOK_INGEST_ROUTE, ClaudeHook
from grove.core.auth import SessionStore
from grove.core.config import (
    DefaultsScope,
    GroveConfig,
    WorkspaceDefaults,
    load_config,
    save_workspace_defaults,
    user_defaults_keys,
)
from grove.core.container_infra import ProjectInfra
from grove.core.contracts.activity import (
    DashboardEvent,
    DashboardSnapshotView,
    SubagentActivityView,
    SubagentFleetView,
)
from grove.core.contracts.agents import AgentSummaryView
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.issueops import IssueOpsEvent, IssueOpsOutcome
from grove.core.contracts.phase import PhaseView, SetPhaseRequest
from grove.core.contracts.public import PublicWorkspaceView
from grove.core.contracts.questions import QuestionAnswerRequest
from grove.core.contracts.requests import CreateWorkspaceRequest, UpdateWorkspaceRequest
from grove.core.contracts.sessions import (
    QueuedMessageView,
    RemapSessionRequest,
    SessionControlsView,
    SessionDetailView,
    SessionQueryView,
    SessionSummaryView,
    TodoListView,
    WorkspaceQueueView,
)
from grove.core.contracts.share_policy import SharePolicyUpdateRequest, SharePolicyView
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
    ProjectView,
    ProvisionProgressView,
    WhoamiView,
    WorkspaceDefaultsSaveView,
    WorkspaceDefaultsView,
    WorkspaceDiffView,
    WorkspacePaneView,
    WorkspacePeekView,
    WorkspaceStateView,
    attach_instruction_view,
)
from grove.core.errors import (
    AgentSessionNotFound,
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchError,
    BranchNotFound,
    CapabilityUnavailable,
    ConfigError,
    GroveError,
    PaneNotFound,
    QuestionAnswerInvalid,
    QuestionNotPending,
    ResumeNotSupported,
    SteeringUnsupported,
    TicketLinkAmbiguous,
    TicketLinkError,
    TicketNotAttached,
    TicketProviderError,
    TicketProviderNotConfigured,
    TicketPullRequestsUnsupported,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.issueops import AssigneePoller, IssueOpsEngine, TicketStatusPublisher
from grove.core.manager import WorkspaceManager
from grove.core.notifications import NotificationBroker
from grove.core.release import ReleaseChecker, ReleaseStatus
from grove.core.sessions import SessionCatalog, SessionExplorer, SessionListing
from grove.core.share_policy import SharePolicy, SharePolicyStore
from grove.core.store import JsonWorkspaceStore
from grove.core.trace_forwarder import TraceForwarder
from grove.daemon._audience import _PollAudience
from grove.daemon._catalog import _CatalogMemo
from grove.daemon._lifecycle import _LifecycleRunner
from grove.daemon._pane_stream import _PaneStreamer
from grove.daemon._poll_coalescer import _PollCoalescer
from grove.daemon._public import PublicWorkspaceReader, ShareNotFound
from grove.daemon._public_tickets import _PublicTicketMemo
from grove.daemon._sse import _SseHub
from grove.daemon._turns import turn_window
from grove.daemon.auth import build_auth_router, make_require_hook_token, make_require_session
from grove.daemon.repos import RepoRegistry
from grove.daemon.usage import _default_usage_service, build_usage_router

if TYPE_CHECKING:
    # Type-only: the real import is deferred into `build_app`, gated on
    # `cfg.telemetry.receiver.enabled` — see the construction site for why an
    # unconditional module-scope import is wrong here (unlike `trace_forwarder`,
    # importing `grove.core.telemetry.receiver` unconditionally pulls in
    # `opentelemetry-proto` even for a daemon that never turns the receiver on).
    from grove.core.telemetry.receiver import OtlpIngest

# How often the lifespan task recomputes activity and emits ``session_activity``
# deltas. Transcript/pane changes aren't lifecycle events, so this poll is what
# streams them; lifecycle changes (create/kill) arrive promptly via the bus. A
# couple seconds matches the dashboard's slow-tick feel without hammering git/tmux.
_POLL_INTERVAL_SECONDS = 2.0


def _close_best_effort(name: str, close: Callable[[], None]) -> None:
    """Run one shutdown hook without preventing independent owners from closing."""
    try:
        close()
    except Exception as exc:
        logger.warning("{} shutdown failed: {}", name, type(exc).__name__)


async def _aclose_best_effort(name: str, aclose: Callable[[], Awaitable[None]]) -> None:
    """Async sibling of :func:`_close_best_effort`, for a coroutine ``close``.

    Kept separate rather than a sync/async branch inside one function: a
    coroutine handed to the sync version would be built and discarded without
    ever being awaited — closing nothing while looking wired.
    """
    try:
        await aclose()
    except Exception as exc:
        logger.warning("{} shutdown failed: {}", name, type(exc).__name__)


def _build_whoami(
    started_at: datetime, release: ReleaseStatus, *, langfuse_host: str | None = None
) -> WhoamiView:
    """Snapshot the daemon's identity + uptime + release skew.

    Pure: reads stdlib state at call time (``socket.gethostname``,
    ``getpass.getuser``, ``platform.*``), takes ``started_at``, the
    pre-resolved ``release`` status and the pre-resolved ``langfuse_host`` as
    input so tests can pin all three deterministically. ``int(...)`` truncates
    rather than rounds — uptime is a coarse signal, sub-second precision is
    noise. Both ``release`` and ``langfuse_host`` are resolved off the loop
    (executor) at the route edge; mapping them here keeps this builder free
    of I/O.
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
        langfuse_host=langfuse_host,
    )


def _resolve_langfuse_host(cfg: GroveConfig) -> str | None:
    """The Langfuse UI host, only when a real launch would also export there.

    Reuses the exact resolution ``grove doctor``'s telemetry check performs
    (:mod:`grove.core.preflight`) — ``derive_env`` then ``unresolved`` — so a
    host name only reaches the wire once host/public/secret ALL resolve.
    Reporting the host alone (partial credentials) would render a button that
    opens Langfuse for a deployment that never actually exports a trace there.
    Best-effort like every other telemetry read on this path: an unusable
    ``env_file`` degrades to ``None`` (already logged by ``derive_env``),
    never raises into the route.
    """
    telemetry = cfg.telemetry
    if not telemetry.enabled:
        return None
    derived = telemetry.derive_env(os.environ)
    if telemetry.unresolved(derived):
        return None
    return derived.get("LANGFUSE_HOST")


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
    """Trigger a named session control — a slash command or a skill.

    ``name`` is a control name from ``GET .../controls`` (a command/skill is
    invoked as ``/name``); the leading slash is optional (the engine strips it).
    Module-scope for the same forward-ref reason as ``_SendMessageBody``.
    """

    name: str = Field(min_length=1)


class _SwitchModelBody(BaseModel):
    """Switch the running session's model — ``model`` is any id, forwarded
    verbatim (the provider boundary; the engine never validates it against the
    offered catalog). Module-scope for the same forward-ref reason above."""

    model: str = Field(min_length=1)


class _HookIngestBody(BaseModel):
    """Native Claude Code http-hook payload — permissive by design.

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


class _TicketLinkBody(BaseModel):
    """Attach-by-reference body — a human-typed ref instead of a resolved selector.

    The sibling ``POST .../tickets`` accepts alongside ``TicketSelector``:
    resolution runs SERVER-SIDE, through
    ``TicketProviderRegistry.resolve_link`` (``WorkspaceManager.attach_link``,
    the same engine seam ``grove tickets attach`` already uses) — never
    re-parsed here. This is what lets a wire-only client (MCP) attach a
    ticket from a URL / ``#42`` / ``42`` / ``owner/repo#42`` without
    importing or reimplementing ``grove.core.tickets.link``. Module-scope
    for the same forward-ref reason as ``_PauseBody`` above.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1)


# The attach body is either a resolved selector or a raw ref — Pydantic's
# smart union mode picks the right member unambiguously because the two
# shapes are structurally disjoint (`extra="forbid"` on both, and neither's
# required fields overlap the other's). Module-scope like the Annotated
# union rule above: a caller resolving `TicketSelector | _TicketLinkBody` by
# string forward-ref needs both names in this module's globals.
_TicketAttachBody = TicketSelector | _TicketLinkBody


# How long the activity stream waits for a real event before beating.
# Load-bearing against the WEBAPP's own staleness bound: `useActivityStream`'s
# stale-tab self-heal reconnects when the last event is older than 20s, so this
# must stay comfortably under that or a healthy quiet stream reads as dead.
_HEARTBEAT_INTERVAL_SECONDS = 15.0

# Kinds that carry no state to resume from, so their frames are written WITHOUT
# an `id:` line. Verified against the HTML spec and a real Chromium: the
# last-event-ID buffer "does not get reset" between events, so an id-less frame
# leaves `lastEventId` — and therefore the `Last-Event-ID` reconnect header — at
# the last REAL event's seq. Stamping a heartbeat with an id would instead move
# the client's resume point onto a frame `_SseHub`'s ring never held, so a
# reconnect would ask to replay from an id `can_replay` cannot honour.
_ID_LESS_KINDS = frozenset({"heartbeat"})


def _sse_frame(event: DashboardEvent) -> str:
    """Format one ``DashboardEvent`` as an SSE wire frame.

    ``id:`` is the monotonic seq the client echoes back as ``Last-Event-ID``,
    omitted for the non-resumable kinds above; ``event:`` is the kind a browser
    ``EventSource`` listener dispatches on; ``data:`` is the JSON body. The blank
    line terminates the frame.

    **``data:`` is never optional, even where the payload says nothing.** The
    spec's dispatch algorithm returns early when the data buffer is empty, so a
    frame of just ``event: heartbeat`` fires NO listener — confirmed in a real
    browser, where such a frame arrived and was silently dropped. A "beat" with
    no body would therefore reproduce the very bug this exists to fix, and
    ``model_dump_json`` is what keeps that from being expressible here.
    """
    id_line = "" if event.kind in _ID_LESS_KINDS else f"id: {event.seq}\n"
    return f"{id_line}event: {event.kind}\ndata: {event.model_dump_json()}\n\n"


def _parse_last_event_id(raw: str | None) -> int | None:
    """Parse the ``Last-Event-ID`` header to an int seq, tolerating junk → ``None``."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _poll_loop(
    coalescer: _PollCoalescer,
    interval: float,
    stop_event: asyncio.Event,
    audience: _PollAudience,
) -> None:
    """Drive ``ActivityService.poll_once`` on a slow interval until shutdown.

    Runs through the same ``_PollCoalescer`` the hook-ingest route triggers —
    a hook event arriving mid-tick joins THIS run rather than
    scheduling a second one, so the timer and the hook trigger can never pile
    up concurrent executor calls. Failures are logged and swallowed — one bad
    tick must not kill the stream (best-effort, like peek). The wait races the
    ``stop_event`` so shutdown is prompt rather than blocking out the interval.

    The ``audience`` await is the empty-room gate: with no consumer this parks
    on an ``asyncio.Event`` (zero wakeups) rather than re-checking a flag every
    two seconds, and the first consumer to arrive releases it into a tick
    immediately. Gating BEFORE the tick rather than after the interval is what
    makes that arrival prompt. Cancellation still ends the task while parked,
    which is how the lifespan stops it.
    """
    while not stop_event.is_set():
        await audience.wait()
        try:
            await coalescer.run()
        except Exception as exc:  # a bad tick must never tear down the lifespan task
            logger.warning("activity poll_once failed: {}", exc)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)


def build_app(  # noqa: PLR0915
    *,
    cfg: GroveConfig,
    store: JsonWorkspaceStore,
    auth_store: SessionStore | None = None,
    share_policy_store: SharePolicyStore | None = None,
    notification_broker: NotificationBroker | None = None,
    release_checker: ReleaseChecker | None = None,
    issue_ops_engine: IssueOpsEngine | None = None,
    status_publisher: TicketStatusPublisher | None = None,
    assignee_poller: AssigneePoller | None = None,
) -> FastAPI:
    """Construct the daemon's FastAPI app.

    Tests call this directly; the CLI's ``serve`` calls it via uvicorn.
    ``auth_store`` is constructed from ``cfg.auth`` if not supplied — tests
    inject one with a fake clock when they need to control TTLs. ``share_policy_store``
    is likewise injectable so tests do not write user state.
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
    # and would be invisible to `create` without the per-repo loader.
    # Prebuild each project's devcontainer image the first time its Manager is
    # minted, so the minutes-long cold build happens on project access
    # rather than inside somebody's `create`. The daemon is the only surface
    # that wires this: it is the one with a long-lived event loop for the build
    # to run on, and the hook is best-effort — it can never block or fail
    # registration (see `ProjectInfra.registration_hook`).
    registry = RepoRegistry(
        cfg=cfg,
        store=store,
        config_loader=load_config,
        on_project_registered=ProjectInfra.registration_hook(cfg),
    )
    activity_service = ActivityService(registry=registry)
    usage_service = _default_usage_service(cfg=cfg, registry=registry)
    # Host-wide session catalog, request-scoped behind a short TTL — never
    # polled, never per-row (see `_catalog.py`). Shared by the host-scoped
    # listing and its drill-in so the pair costs one scan.
    catalog = _CatalogMemo(SessionCatalog(registry))
    # Ticket enrichment for the PUBLIC share view, memoized per process.
    #
    # It has to live here rather than at module scope for the reason every other
    # cache in this file does: a module global outlives the app, so two apps in
    # one test process would share it and a test's resolved ticket would leak
    # into the next. Owned by `build_app`, it dies with the app.
    #
    # The `/public` overview is POLLED by every open shared page, and resolving
    # a ticket is an upstream HTTP call against somebody else's forge with the
    # host's own credential. Without a memo, N anonymous readers polling would
    # be N x refs upstream requests — an amplification vector pointed at a third
    # party, driven by callers Grove never authenticated.
    public_ticket_memo = _PublicTicketMemo()
    # Shared by the lifespan timer and the hook-ingest route below — the
    # single choke point that keeps their two independent triggers from ever
    # running `poll_once` concurrently.
    poll_coalescer = _PollCoalescer(activity_service.poll_once)
    # The outbound face: the live sticky status comment. Built from
    # `cfg.issueops` (None when disabled), bound to the activity bus in the
    # lifespan like `notification_broker`, and injected into the engine below so
    # the `@grove status` verb forces an immediate re-render. Injectable for tests.
    if status_publisher is None:
        status_publisher = TicketStatusPublisher.from_config(
            cfg.issueops,
            registry=registry,
            # Reachability is answered by the very reader a link would send
            # someone to — the same memoized catalog `/sessions/{id}/turns`
            # resolves against — so the comment cannot link a transcript that
            # route would 404. Passed from here because the catalog is the
            # daemon's, and `core` must not learn about it.
            transcript_probe=lambda kind, cwd, session_id: (
                catalog.find(kind=kind, cwd=cwd, session_id=session_id) is not None
            ),
        )
    # The issue-ops router: resolves an event's repo through the SAME
    # per-repo registry every other route dispatches on, so a forwarded comment
    # steers/creates against the target repo's own cascade. The status publisher
    # rides its `StatusPublisher` seam (structural, no engine↔publisher import).
    # Injectable for tests.
    if issue_ops_engine is None:
        issue_ops_engine = IssueOpsEngine(registry=registry, status_publisher=status_publisher)
    # The assignee work queue. Unlike the publisher it rides NO bus — the
    # tracker pushes nothing to a loopback daemon, so this is the one place in
    # the daemon that owns a timer of its own, on its own bounded worker. `None`
    # unless a deployment turned on the outbound assignment, the inbound pickup,
    # or both; each is off by default because between them they write to
    # somebody else's tracker and spawn real agents.
    if assignee_poller is None:
        assignee_poller = AssigneePoller.from_config(cfg.issueops, registry=registry)
    # Assignment is edge-triggered as well as polled, and BOTH go through the
    # poller so there is exactly ONE ownership memo. The publisher assigns as it
    # upserts a sticky comment — the moment a ticket is deterministically known
    # to be Grove's work — instead of waiting up to a poll interval; the poller
    # still sweeps, which is what releases an assignment when the workspace ends.
    # Wired here rather than in `from_config` because it is the daemon that holds
    # both objects, and `core` must not have the publisher import the poller.
    if status_publisher is not None and assignee_poller is not None:
        status_publisher.set_assigner(assignee_poller.assign_now)
    # Every lifecycle verb (create/pause/resume/respawn/kill) goes through this
    # one seam instead of reaching for a raw executor handle: it owns the
    # dedicated bounded pool AND the per-workspace serialization the event loop
    # used to provide for free (see `_lifecycle.py` for both rationales).
    lifecycle = _LifecycleRunner()
    # Who wants activity deltas. The 2s poll waits on it, so an idle daemon that
    # nobody and nothing is listening to does no per-workspace git/transcript
    # work at all (see `_audience.py`).
    audience = _PollAudience()
    sse_hub = _SseHub(activity_service, audience=audience)
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
    if share_policy_store is None:
        share_policy_store = SharePolicyStore()
    require_session = make_require_session(auth_store=auth_store, enabled=cfg.auth.enabled)
    auth_dep = [Depends(require_session)]
    # Same config flag, a DIFFERENT mechanism: the hook-ingest route
    # can't ask a human to approve a pairing challenge (see `make_require_hook_token`).
    require_hook_token = make_require_hook_token(enabled=cfg.auth.enabled)
    # The OTLP/HTTP receiver — `grove.core.telemetry.receiver`, built and tested
    # but never mounted until now. Off by default (`cfg.telemetry.receiver.enabled`),
    # and the import is deliberately INSIDE this branch: the module imports
    # `opentelemetry-proto` at its own module scope (unlike `trace.py`/
    # `trace_forwarder.py`, which import OTel lazily so `grove.core` stays
    # importable without the `telemetry` extra), so an unconditional top-level
    # import here would make that extra mandatory for every `[daemon]`-only
    # install, receiver on or off. With it off, nothing below this branch runs
    # and the app is byte-identical to before this route existed.
    otlp_ingest: OtlpIngest | None = None
    if cfg.telemetry.receiver.enabled:
        from grove.core.telemetry.receiver import OtlpIngest as _OtlpIngest  # noqa: PLC0415

        otlp_ingest = _OtlpIngest(
            queue_capacity=cfg.telemetry.receiver.queue_capacity,
            workers=cfg.telemetry.receiver.workers,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: PLR0915
        # Statement count grows linearly with subscriber/owner count (bind on
        # entry, close in `finally`) — the same justification `build_app`
        # itself already carries the identical suppression for.
        app.state.registry = registry
        app.state.auth_store = auth_store
        app.state.share_policy_store = share_policy_store
        app.state.activity = activity_service
        app.state.usage = usage_service
        app.state.sse_hub = sse_hub
        if otlp_ingest is not None:
            app.state.otlp_ingest = otlp_ingest
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
        # rides — a debounced edge-trigger, no new status computation. Its
        # dispatch worker keeps channel HTTP off the activity poll thread.
        # `audience.join()` with no matching leave is deliberate: these two are
        # consumers for the whole process lifetime, and they are exactly the
        # ones that matter with no dashboard open — a push notification exists
        # to reach a user who is NOT watching. So a daemon with notifications or
        # issue-ops configured polls continuously; only one with no consumer at
        # all goes quiet.
        if notification_broker is not None:
            notification_broker.bind(activity_service.subscribe)
            audience.join()
            app.state.notification_broker = notification_broker
        # The issue-ops status publisher is the bus's third subscriber
        # (alongside the SSE hub + notification broker) — same discipline: bind
        # after `sse_hub.start`, close on shutdown. None when issue-ops is off.
        if status_publisher is not None:
            status_publisher.bind(activity_service.subscribe)
            audience.join()
            app.state.status_publisher = status_publisher
        # The trace forwarder is the bus's fourth subscriber. It DOES
        # `audience.join()`, on the notification broker's argument rather than
        # by copying its line: it consumes deltas, so an unjoined forwarder is
        # subscribed to a bus that stops ticking the moment the last dashboard
        # closes — and a fleet running unattended overnight is exactly the run
        # whose trace someone reads the next morning. The cost is stated plainly:
        # a daemon with telemetry configured polls the fleet continuously, the
        # same bargain notifications and issue-ops already make. `None` when
        # telemetry is off or its credentials did not resolve, so a disabled
        # forwarder is indistinguishable from an absent one.
        # `pricing` is what makes a replayed generation carry `cost_details`.
        # Passed HERE because this is the only composition point holding both
        # halves — the telemetry config and the usage catalog — and a seam
        # wired everywhere except its one real call site is the failure this
        # tier already shipped once: fully built, fully tested, and reached by
        # nothing.
        trace_forwarder = TraceForwarder.from_config(
            cfg.telemetry, registry=registry, pricing=cfg.usage.pricing
        )
        if trace_forwarder is not None:
            trace_forwarder.bind(activity_service.subscribe)
            audience.join()
            app.state.trace_forwarder = trace_forwarder
        # No `audience.join()` here on purpose: the audience gates the ACTIVITY
        # poll, and the assignee poller consumes no deltas — it drives its own
        # timer against the trackers. Joining would make it hold the fleet's
        # git/tmux scan open for work it never reads.
        if assignee_poller is not None:
            assignee_poller.bind()
            app.state.assignee_poller = assignee_poller
        stop_event = asyncio.Event()
        poll_task = asyncio.create_task(
            _poll_loop(poll_coalescer, _POLL_INTERVAL_SECONDS, stop_event, audience)
        )
        try:
            yield
        finally:
            stop_event.set()
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task
            if notification_broker is not None:
                _close_best_effort("notification broker", notification_broker.close)
            if status_publisher is not None:
                _close_best_effort("status publisher", status_publisher.close)
            if trace_forwarder is not None:
                _close_best_effort("trace forwarder", trace_forwarder.close)
            if assignee_poller is not None:
                _close_best_effort("assignee poller", assignee_poller.close)
            if otlp_ingest is not None:
                await _aclose_best_effort("otlp ingest", otlp_ingest.aclose)
            _close_best_effort("session catalog", catalog.close)
            _close_best_effort("SSE hub", sse_hub.stop)
            _close_best_effort("activity service", activity_service.close)
            try:
                await asyncio.to_thread(usage_service.close)
            except Exception as exc:
                logger.warning("usage service shutdown failed: {}", type(exc).__name__)
            _close_best_effort("lifecycle", lifecycle.shutdown)

    app = FastAPI(
        title="Grove daemon",
        # Derive from the package version so OpenAPI's ``info.version`` tracks
        # ``grove.__version__`` (and ``pyproject``) instead of drifting on a
        # hand-edited literal — the same single source ``/healthz`` + ``/whoami``
        # report.
        version=_GROVE_VERSION,
        lifespan=lifespan,
    )

    if otlp_ingest is not None:
        # A MOUNT, never routes re-declared here: `build_receiver_app` is what
        # keeps the receiver a separately-runnable ASGI app (the standalone
        # deployment gets that for free) — re-registering its routes on `app`
        # would forfeit it for good. **Unauthenticated on purpose:** the daemon
        # binds loopback only, which is the same load-bearing constraint that
        # already defers auth on every other route (see `daemon/CLAUDE.md`) —
        # the agents posting OTLP here are local processes with no daemon
        # session token, so `auth_dep` would make the mount unusable for the
        # only callers it has. A mounted sub-app's OWN lifespan never runs
        # (`_asgi.py`), which is why draining happens from THIS app's
        # `lifespan` above instead, on `otlp_ingest` directly.
        from grove.core.telemetry.receiver import build_receiver_app  # noqa: PLC0415

        app.mount(cfg.telemetry.receiver.path, build_receiver_app(ingest=otlp_ingest))

    # Pairing + sessions router. Mounts before the gated routes so its own
    # per-route auth decisions stay local to ``build_auth_router``.
    app.include_router(build_auth_router(auth_store=auth_store, require_session=require_session))

    # The historical usage-audit router — bounded reads over the SQLite cache,
    # the past-tense sibling of `/activity` + `/events` above. Auth is applied
    # HERE at inclusion (like every other gated route) rather than inside the
    # router, which is what keeps this a two-line integration.
    app.include_router(
        build_usage_router(cfg=cfg, registry=registry, usage_service=usage_service),
        dependencies=auth_dep,
    )

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
            # Steering refusals. PaneNotFound is 409 like the state
            # errors: the live session's current shape conflicts with the
            # request and a respawn can fix it. SteeringUnsupported is 501:
            # a capability gap (the agent kind has no implementation for
            # the op) — no state change makes a retry succeed, which is
            # exactly the false promise a 409 would make.
            PaneNotFound: (409, "pane_not_found"),
            SteeringUnsupported: (501, "steering_unsupported"),
            # Control triggers. Capability-based like SteeringUnsupported —
            # the runtime (a generic shell / remote session) has no in-session
            # slash-control surface, so no state change makes a retry succeed. 501.
            CapabilityUnavailable: (501, "capability_unavailable"),
            # Live-question answering. QuestionNotPending is 409, like the
            # state errors: the request was well-formed, the live question just
            # moved on (answered in the terminal, or superseded) — the client
            # drops its pending card. QuestionAnswerInvalid is 422: the plan is
            # malformed for the captured questions (bad length/index/kind), which
            # no state change fixes.
            QuestionNotPending: (409, "question_not_pending"),
            QuestionAnswerInvalid: (422, "question_answer_invalid"),
            # Agent-transcript sessions; the auth domain's `session_not_found`
            # (revoked bearer sessions) lives in the auth router. Also the
            # remap verb's not-found/ambiguous session-ref, re-raised in
            # this domain by remap_session so it never falls through to 500.
            AgentSessionNotFound: (404, "agent_session_not_found"),
            # Resume-into-workspace: a create named resume_session_id for
            # an agent kind with no resume handle (mewbo/generic). 422 — the
            # request is well-formed but semantically invalid for this agent, no
            # state change fixes it (mirrors question_answer_invalid).
            ResumeNotSupported: (422, "resume_not_supported"),
            # Ticket providers. NotConfigured is 404 — the named tracker
            # simply isn't enabled for this repo (nothing went wrong on the
            # wire). TicketProviderError is 502 — the upstream tracker API
            # failed (transport, auth, malformed), which is not the client's
            # fault. NotConfigured is a sibling of (not a subclass of)
            # TicketProviderError, so order between them is immaterial.
            TicketProviderNotConfigured: (404, "ticket_provider_not_configured"),
            TicketProviderError: (502, "ticket_provider_error"),
            # A per-ticket phase claim (`POST .../phase` with `ticket=`) named
            # a key the workspace has no ref for. 404, mirroring
            # AgentSessionNotFound: well-formed request, and the fix (attach
            # that ticket first) is a state change the client can make.
            TicketNotAttached: (404, "ticket_not_attached"),
            # Human-typed ticket references (`ref`, workspace-links story).
            # TicketLinkAmbiguous MUST precede its parent TicketLinkError — the
            # map is a linear isinstance scan, same rule as BranchError above.
            # Ambiguous is 409: the ref itself is well-formed and DID resolve —
            # just to more than one enabled provider — so this is a conflict
            # the caller resolves by resupplying a qualified ref (a full URL or
            # owner/repo#id), the same "well-formed request, current state
            # can't satisfy it as given" shape as pane_not_found/
            # question_not_pending above. TicketLinkError is 422: the ref
            # didn't parse as any recognized shape at all, or named a provider
            # this repo has none enabled for — question_answer_invalid's
            # "well-formed request, semantically invalid" bucket. Both
            # messages (from resolve_link) are already actionable: ambiguous
            # names every candidate provider, invalid names the expected shapes.
            TicketLinkAmbiguous: (409, "ticket_link_ambiguous"),
            TicketLinkError: (422, "ticket_link_invalid"),
            # A provider was asked for PR metadata it has no concept of
            # (Linear). Capability-based, not state-based — no retry succeeds
            # regardless of the ref. 422, following ResumeNotSupported's
            # precedent just above: a well-formed request that is semantically
            # invalid for this provider, not a 501 capability gap, because the
            # caller's fix is "attach a different tracker's ref", not "wait".
            TicketPullRequestsUnsupported: (422, "ticket_pull_requests_unsupported"),
            # A repo whose `.grove/config.json` will not parse. 500 and
            # not 4xx: the request was fine, the server's own state is not, and
            # no client retry fixes it — but it is NAMED, because the message
            # carries the file and the parse position an operator needs.
            ConfigError: (500, "config_error"),
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

    @app.exception_handler(GroveError)
    async def _unhandled_grove_error(request: Request, exc: Exception) -> JSONResponse:
        """Give a GroveError raised OUTSIDE a route's own `try` the same envelope.

        Every per-repo route resolves its manager with `registry.get(...)`
        *before* entering its `try/except GroveError`, and that call resolves the
        repo's config cascade — so an unparseable `.grove/config.json` escaped as
        a bare 500 with no code and no message. Fixed here rather than by
        adding a `try` to each of the eight call sites: one seam covers them and
        every route added later, and the mapping it applies is the one those
        routes already use, so a caught and an uncaught GroveError cannot answer
        differently.
        """
        del request
        assert isinstance(exc, GroveError)
        http = _grove_error_to_http(exc)
        logger.warning("daemon: unhandled {}: {}", type(exc).__name__, exc)
        return JSONResponse(status_code=http.status_code, content={"detail": http.detail})

    def _known_root(repo: Path) -> Path:
        """Resolve a caller-supplied ``repo`` to a REGISTERED root, or 404.

        Every repo-dispatched route funnels through this, because ``repo`` is
        not a lookup key — it *selects configuration*. ``registry.get(path)``
        resolves that directory's own cascade, and the config it finds names
        the binaries and trackers the daemon then acts on: ``GET /agents``
        resolves each row's model catalog by EXECUTING the configured command
        (``codex debug models``), and the ticket routes read their base URLs
        and credentials from the same cascade. So an unvalidated path let any
        directory on the host — an untrusted checkout, a download — choose what
        a read-only-looking GET runs and where it talks. Restricting
        it to roots the user already registered is the whole fix; a path the
        user vouched for naming its own agent command is the documented
        feature.

        Two lesser symptoms close with it: a nonexistent path used to reach
        ``subprocess(cwd=…)`` and surface as a bare 500 (``OSError`` is not a
        ``GroveError``, so neither the route's handler nor the app-level one
        sees it), and an existing non-repo directory used to answer ``200 []``
        — a typo'd path masquerading as "this repo has no branches", exactly
        what ``/sessions`` refuses to do.
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
        return root

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

    # ─── /public — the unauthenticated share namespace ──────────────────────
    #
    # THE ONLY ROUTES IN THIS FILE WITHOUT `auth_dep`, apart from `/healthz`
    # above and the pairing handshake. That is the whole security design and it
    # is why they live together here rather than beside the `/workspaces` routes
    # they mirror: a reviewer reading this block sees the entire public attack
    # surface at once, and nothing under `/workspaces` can drift into it.
    #
    # Every one is a GET, resolves its workspace ONLY through the token in its
    # own path, and answers a flat 404 for every failure so a wrong token learns
    # nothing. `PublicWorkspaceReader` (`_public.py`) owns what may be read;
    # these three are the shells that offload it. **Anything added here needs
    # the same three properties, and a fourth route is a design decision rather
    # than a convenience.**

    def _share_reader(token: str, passcode: str | None) -> PublicWorkspaceReader:
        """Resolve a share token AND clear its project's passcode, together.

        THE PASSCODE CHECK LIVES HERE RATHER THAN IN EACH ROUTE, and that is a
        structural choice rather than a tidy-up. As three separate calls it was
        correct but forgettable: a fourth `/public/**` route added later would
        be unauthenticated BY OMISSION — nothing raises, no test breaks, the
        route simply serves without a passcode. That is the very failure this
        whole namespace exists to prevent, one layer in.

        Folded in, a route cannot obtain a reader without the check having run,
        so the guarantee is carried by the type rather than by memory. Every
        route below already had to call this to get anywhere.
        """
        reader = PublicWorkspaceReader.for_token(
            token,
            registry=registry,
            activity=activity_service,
            version=_GROVE_VERSION,
            ticket_memo=public_ticket_memo,
        )
        _require_share_passcode(reader, passcode)
        return reader

    def _share_404(exc: ShareNotFound) -> HTTPException:
        return HTTPException(
            status_code=404,
            detail={"error": "share_not_found", "message": str(exc)},
        )

    def _require_share_passcode(reader: PublicWorkspaceReader, passcode: str | None) -> None:
        """Reject a missing or wrong project passcode with one flat response."""
        policy = share_policy_store.get(Path(reader.state.repo_root))
        if not policy.verifies(passcode):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "share_passcode_required",
                    "message": "a share passcode is required",
                },
            )

    def _share_policy_view(repo_root: Path) -> SharePolicyView:
        policy = share_policy_store.get(repo_root)
        return SharePolicyView(
            ttl_seconds=policy.ttl_seconds,
            passcode_set=policy.passcode_hash is not None,
        )

    @app.get("/share-policy", response_model=SharePolicyView, dependencies=auth_dep)
    async def get_share_policy(repo: Annotated[Path, Query()]) -> SharePolicyView:
        """The authenticated project's public-share policy, never its hash."""
        root = _known_root(repo)
        return await asyncio.to_thread(_share_policy_view, root)

    @app.put("/share-policy", response_model=SharePolicyView, dependencies=auth_dep)
    async def save_share_policy(
        body: SharePolicyUpdateRequest,
        repo: Annotated[Path, Query()],
    ) -> SharePolicyView:
        """Replace the authenticated project's public-share policy."""
        root = _known_root(repo)

        def _save() -> SharePolicyView:
            policy = SharePolicy.for_repo(
                root,
                passcode=body.passcode,
                ttl_seconds=body.ttl_seconds,
            )
            saved = share_policy_store.save(policy)
            return SharePolicyView(
                ttl_seconds=saved.ttl_seconds,
                passcode_set=saved.passcode_hash is not None,
            )

        return await asyncio.to_thread(_save)

    @app.get("/public/{token}", response_model=PublicWorkspaceView)
    async def public_workspace(
        token: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
    ) -> PublicWorkspaceView:
        """Everything a shared page renders except its transcript and its diff.

        The payload is an explicit allowlist built in ``contracts/public.py`` —
        no host path, no container identity, no pane, no token. It is also what
        the page POLLS, since the public view has no SSE to ride (``/events`` is
        a cross-project fan-out over every workspace on the host).

        Runs off the loop: it peeks the worktree (git) and resolves the session
        activity (transcript parse), which is exactly the "no route calls a
        manager method on the loop" rule this file holds everywhere else.
        """
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
            return await asyncio.to_thread(reader.overview)
        except ShareNotFound as exc:
            raise _share_404(exc) from exc
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get("/public/{token}/turns", response_model=SessionDetailView | None)
    async def public_turns(
        token: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
        last: Annotated[int | None, Query(ge=1)] = None,
        after_turn: Annotated[int | None, Query(ge=0)] = None,
    ) -> SessionDetailView | None:
        """The shared workspace's transcript, windowed — the LIVE half.

        Takes no session id: the token names a workspace and the daemon picks
        the session, so an unauthenticated caller holds no coordinate it could
        tamper with. ``after_turn`` and ``last`` mean exactly what they mean on
        the authenticated route (one ``turn_window``, shared), which is what
        lets the browser reuse its whole cursor-merge path unchanged.

        ``null`` means this workspace has no readable transcript yet — a real
        state for a workspace shared right after it was created, not an error.
        """
        if last is not None and after_turn is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_turn_window",
                    "message": "`last` and `after_turn` are mutually exclusive",
                },
            )
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
            return await asyncio.to_thread(reader.turns, last=last, after_turn=after_turn)
        except ShareNotFound as exc:
            raise _share_404(exc) from exc
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get("/public/{token}/diff", response_model=WorkspaceDiffView)
    async def public_diff(
        token: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
        path: Annotated[str | None, Query()] = None,
    ) -> WorkspaceDiffView:
        """The shared workspace's working-tree patch, same scope and bounds as
        the authenticated route.

        The changed code is the thing a shared link exists to show, so it is not
        trimmed for being public — ``?path=`` is the same per-file drill-in the
        Changes tab already uses.
        """
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
            return await asyncio.to_thread(reader.diff, path=path)
        except ShareNotFound as exc:
            raise _share_404(exc) from exc
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get("/whoami", response_model=WhoamiView, dependencies=auth_dep)
    async def whoami() -> WhoamiView:
        """Authenticated daemon identity + uptime.

        Distinct from ``/auth/sessions/me`` (caller session) — this
        endpoint describes the daemon process itself.

        The release-skew check rides the executor: ``check()`` is cached for
        hours, so this is a no-op cache read on all but the occasional refresh
        tick — and a refresh's blocking GitHub GET runs off the event loop,
        never stalling the handler (best-effort, like every other side effect).
        The Langfuse host resolution can read an ``env_file`` off disk, so it
        rides the executor too.
        """
        release = await asyncio.to_thread(release_checker.check)
        langfuse_host = await asyncio.to_thread(_resolve_langfuse_host, cfg)
        return _build_whoami(app.state.started_at, release, langfuse_host=langfuse_host)

    @app.get("/activity", response_model=DashboardSnapshotView, dependencies=auth_dep)
    async def activity() -> DashboardSnapshotView:
        """One-shot cross-project dashboard snapshot.

        ``snapshot()`` does blocking git/tmux I/O, so it runs in the executor to
        keep the loop responsive under concurrent requests.
        """
        snap = await asyncio.to_thread(activity_service.snapshot)
        return DashboardSnapshotView.from_snapshot(snap)

    @app.post(
        HOOK_INGEST_ROUTE,
        status_code=204,
        dependencies=[Depends(require_hook_token)],
    )
    async def ingest_agent_hook(body: _HookIngestBody) -> None:
        """Native Claude Code http-hook push — the live half of the command-hook sidecar.

        Claude Code dispatches the ``command`` and ``http`` handlers registered
        on the SAME event independently (`ClaudeHook.settings`), so by the time
        this request lands the command handler has already written the
        sidecar — this route's only job is collapsing the ~2s poll-tick lag
        into an immediate recompute, never a second sidecar write (this
        payload carries no ``$TMUX_PANE``, so writing here would race the
        command handler's more complete record). ``poll_once`` already diffs
        per-workspace by fingerprint and emits a delta only for what changed,
        so the wire cost is scoped by construction — the *computation* still
        walks every workspace, which is why this goes through the shared
        ``poll_coalescer`` rather than dispatching its own executor call:
        Claude fires this on every tracked event with no debounce, so
        without coalescing, a burst of hook events during active fleet coding
        ran that many full-fleet scans at once.

        Gated by the same-host hook-ingest token (`make_require_hook_token`),
        not the `SessionStore` pairing bearer every other route uses — see its
        docstring for why.
        """
        del body  # session_id only justifies the call; poll_once() rescans everyone
        await poll_coalescer.run()

    @app.get(
        "/events",
        dependencies=auth_dep,
        responses={
            200: {
                "model": DashboardEvent,
                "description": (
                    "text/event-stream of DashboardEvent JSON objects. The first "
                    "frame is a `snapshot` (or a `Last-Event-ID` replay); subsequent "
                    "frames are `session_activity` / `workspace_changed` deltas. A "
                    "quiet stream beats every 15s with a `heartbeat` frame, which "
                    "carries no `id:` so it never moves the client's resume point."
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
        last_event_id = _parse_last_event_id(request.headers.get("last-event-id"))

        async def stream() -> AsyncIterator[str]:
            # Starlette's StreamingResponse runs a disconnect watcher that cancels
            # this generator when the client goes away — so there is no manual
            # is_disconnected() poll; the `finally` (unregister) runs on that
            # cancellation. A `heartbeat` frame fires when no event arrives within
            # the window, so proxies see a live connection AND the browser can tell
            # a quiet stream from a dead one — a bare `: keepalive`
            # comment keeps a proxy happy but fires no named listener, so
            # the webapp's stale-tab heal would reconnect on every quiet tab return.
            queue = sse_hub.register()
            # The last REAL event's seq, i.e. exactly where a reconnect resumes.
            # Heartbeats report it rather than minting their own: a beat is not a
            # position in the stream, and the frame carries no `id:` to move one.
            last_seq = 0
            try:
                if last_event_id is not None and sse_hub.can_replay(last_event_id):
                    for missed in sse_hub.replay_since(last_event_id):
                        last_seq = missed.seq
                        yield _sse_frame(missed)
                else:
                    snap = await asyncio.to_thread(activity_service.snapshot)
                    snapshot_event = DashboardEvent.snapshot_event(
                        snap, seq=activity_service.next_seq()
                    )
                    last_seq = snapshot_event.seq
                    yield _sse_frame(snapshot_event)
                while True:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                        )
                    except TimeoutError:
                        yield _sse_frame(DashboardEvent.heartbeat(seq=last_seq))
                        continue
                    last_seq = event.seq
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

        roots = [_known_root(repo)] if repo is not None else list(registry.known_roots())
        # Managers are resolved HERE, on the loop, not inside the executor
        # closure: `registry.get` mints a project's Manager and its
        # `on_project_registered` hook schedules the image prebuild on the
        # RUNNING loop — off a loop thread it finds none and silently skips.
        managers = [registry.get(repo_root) for repo_root in roots]

        def _scan() -> list[WorkspaceStateView]:
            out: list[WorkspaceStateView] = []
            for mgr in managers:
                if ticket_filter is not None:
                    match = mgr.find_by_ticket(*ticket_filter)
                    if match is not None:
                        out.append(WorkspaceStateView.from_state(match))
                    continue
                out.extend(WorkspaceStateView.from_state(state) for state in mgr.list())
            return out

        # `list()` reconciles every workspace against live tmux + the
        # filesystem — measured ~500 ms for 23 workspaces — and this is the
        # most-polled route in the product, so it belongs in the executor with
        # the other scans rather than stalling the loop on every dashboard tick.
        return await asyncio.to_thread(_scan)

    @app.post("/workspaces", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def create_workspace(req: CreateWorkspaceRequest) -> WorkspaceStateView:
        """Create a workspace and return it once it is fully provisioned.

        Runs on the lifecycle runner's own pool, never the event loop: with containers
        the default runtime, `create` pays a full `devcontainer up` (image pull,
        build, features, lifecycle hooks) plus the init script and the agent
        launch — minutes, during which an event-loop call froze every SSE
        stream, every other repo's dashboard and the activity poll.
        WHEN it returns is unchanged: the response still means "provisioned,
        init script run, agent launched", which is what `grove create` and the
        MCP tool promise their callers. Unkeyed, unlike every other verb —
        the id it would serialize on is minted *by* this call (see
        ``_LifecycleRunner.run``).
        """
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
            state = await lifecycle.run(None, mgr.create, req)
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
        """Ingest one forwarded issue-comment event and route it to a workspace action.

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
        return await asyncio.to_thread(issue_ops_engine.handle, event)

    @app.get("/workspaces/{ws_id}", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def get_workspace(ws_id: str) -> WorkspaceStateView:
        """One workspace's reconciled state — in the executor, since `get`
        reconciles against live tmux and the filesystem like `list`."""
        mgr = _manager_for(ws_id)
        try:
            state = await asyncio.to_thread(mgr.get, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/pause",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def pause_workspace(ws_id: str, body: _PauseBody) -> WorkspaceStateView:
        """Pause the workspace — through the lifecycle runner, like every verb.

        A container workspace runs a bounded in-container shutdown here, so this
        is slow blocking work even though it reads like a state flip — and it is
        keyed on ``ws_id``, so it can never interleave with a kill or respawn of
        the same workspace.
        """
        mgr = _manager_for(ws_id)
        try:
            state = await lifecycle.run(ws_id, lambda: mgr.pause(ws_id, force=body.force))
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/resume",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def resume_workspace(ws_id: str) -> WorkspaceStateView:
        """Resume the workspace — lifecycle runner: re-provisioning a
        container and re-running the init script is minutes of blocking work,
        serialized against every other verb on this workspace."""
        mgr = _manager_for(ws_id)
        try:
            state = await lifecycle.run(ws_id, mgr.resume, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post(
        "/workspaces/{ws_id}/respawn",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def respawn_workspace(ws_id: str) -> WorkspaceStateView:
        """Respawn the workspace's session — lifecycle runner, same
        re-provisioning cost and same per-workspace serialization as ``resume``."""
        mgr = _manager_for(ws_id)
        try:
            state = await lifecycle.run(ws_id, mgr.respawn, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.post("/workspaces/{ws_id}/kill", status_code=204, dependencies=auth_dep)
    async def kill_workspace(ws_id: str, body: _KillBody) -> None:
        """Tear the workspace down — lifecycle runner: removing the
        container and the worktree is slow blocking I/O, and keying it on
        ``ws_id`` is what stops it racing a respawn mid-``devcontainer up``."""
        mgr = _manager_for(ws_id)
        try:
            await lifecycle.run(ws_id, lambda: mgr.kill(ws_id, delete_branch=body.delete_branch))
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/message", status_code=204, dependencies=auth_dep)
    async def send_workspace_message(ws_id: str, body: _SendMessageBody) -> None:
        """Steer the workspace's agent with a follow-up message.

        Empty 204 on success — the injection has no meaningful response
        body. Refusals ride the typed-error envelope: 409
        ``workspace_state_error`` / ``pane_not_found``, 501
        ``steering_unsupported``. ``send_message`` shells a blocking tmux
        keystroke injection (with a settle delay) — off-loaded to the
        executor like every other blocking manager call, so one workspace's
        steer never stalls the loop the SSE stream and every other request
        share.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.send_message, ws_id, body.text)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/interrupt", status_code=204, dependencies=auth_dep)
    async def interrupt_workspace(ws_id: str) -> None:
        """Interrupt the workspace's agent, where its adapter supports it.

        Today every kind refuses (501 ``steering_unsupported``) — there is
        no safe generic interrupt for a tmux-hosted CLI. The route exists now
        so clients code against the final surface, ready for a kind whose API
        supports a real interrupt. Off-loaded to the executor like its
        ``/message`` sibling — a future arm that does shell blocking I/O
        (tmux, a remote HTTP call) must not stall the loop.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.interrupt, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/question-answer", status_code=204, dependencies=auth_dep)
    async def answer_question(ws_id: str, body: QuestionAnswerRequest) -> None:
        """Answer a pending AskUserQuestion by driving the agent's TUI.

        Dispatch semantics — 204 the instant the keystrokes are sent; the
        resolution arrives later on the activity stream (the sidecar clears and
        the transcript flushes). Refusals ride the typed-error envelope: 404
        ``workspace_not_found``, 409 ``question_not_pending`` (stale/absent
        ``tool_use_id``) / ``pane_not_found``, 422 ``question_answer_invalid``
        (plan doesn't fit the captured questions). The wire model rejects a
        structurally-malformed body (422) before the handler runs.
        ``answer_question`` drives multiple blocking tmux keystroke writes —
        off-loaded to the executor like the other steer routes.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.answer_question, ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/controls",
        response_model=SessionControlsView,
        dependencies=auth_dep,
    )
    async def workspace_controls(ws_id: str) -> SessionControlsView:
        """Enumerate the session's available input controls.

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
        controls = await asyncio.to_thread(mgr.session_controls, ws_id)
        return SessionControlsView.from_controls(controls)

    @app.post("/workspaces/{ws_id}/controls/invoke", status_code=204, dependencies=auth_dep)
    async def invoke_workspace_control(ws_id: str, body: _InvokeControlBody) -> None:
        """Invoke a named session control — a slash command or a skill.

        Composes the tool's ``/name`` invocation and delivers it through the same
        steer path as ``/message`` — 204 on dispatch (delivered, not "ran"; the
        result rides the transcript later). Refusals ride the typed envelope: 501
        ``capability_unavailable`` (a shell/remote kind has no slash-control
        surface), 409 ``pane_not_found`` / ``workspace_state_error``.

        In the executor like its ``/message`` sibling: it rides the same
        blocking tmux keystroke injection, settle delay included.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.invoke_control, ws_id, body.name)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/controls/model", status_code=204, dependencies=auth_dep)
    async def switch_workspace_model(ws_id: str, body: _SwitchModelBody) -> None:
        """Switch the running session's model.

        Delivered as the interactive ``/model <id>`` control through the steer
        path — 204 on dispatch. The id is forwarded verbatim (the provider
        boundary). Refusals: 501 ``capability_unavailable`` (a kind with no
        model-switch channel), 409 ``pane_not_found`` / ``workspace_state_error``.

        In the executor like every other steer route — same tmux injection path.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.switch_model, ws_id, body.model)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/session",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def remap_workspace_session(ws_id: str, body: RemapSessionRequest) -> WorkspaceStateView:
        """Pin an existing agent session as this workspace's tracked primary.

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
        # endpoints rather than stalling the event loop.
        try:
            state = await asyncio.to_thread(mgr.remap_session, ws_id, body.session_ref)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.patch(
        "/workspaces/{ws_id}",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def update_workspace(ws_id: str, body: UpdateWorkspaceRequest) -> WorkspaceStateView:
        """Partial metadata update — title, description and/or public sharing.

        Wire semantics: ``null`` / omitted = "do not change". Empty
        string in ``description`` clears it; title cannot be cleared.
        Mapping wire → engine kwargs: an absent field translates to
        "kwarg not passed" so the manager's ``_UNSET`` sentinel works.

        ``share`` is the one field with a security consequence, and omission is
        what makes it safe: a client renaming a workspace must never revoke a
        public link by not mentioning it. The minted token comes back on the
        response's ``share_token`` — this route is where a client learns the
        link, and there is no second endpoint that hands one out.
        """
        mgr = _manager_for(ws_id)
        # `Any` rather than `str`: the values are now heterogeneous (`share` is
        # a bool), and a `**kwargs` splat cannot be typed more precisely than
        # its widest member without the splat itself failing to check. This is
        # the wire→engine boundary the escape hatch is for, and the narrowing
        # is right below — the `is not None` guards mean only present fields
        # reach the manager, which is what preserves its `_UNSET`
        # "leave alone" semantics, and the manager validates each one anyway.
        kwargs: dict[str, Any] = {}
        if body.title is not None:
            kwargs["title"] = body.title
        if body.description is not None:
            kwargs["description"] = body.description
        if body.share is not None:
            kwargs["share"] = body.share
            if body.share:
                # TTL is resolved when the capability is issued, not when it is
                # read; later policy edits cannot retroactively move this link.
                kwargs["share_ttl_seconds"] = share_policy_store.get(mgr.repo_root).ttl_seconds
        if body.share_session_id is not None:
            kwargs["share_session_id"] = body.share_session_id
        try:
            state = await asyncio.to_thread(lambda: mgr.update(ws_id, **kwargs))
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
            instr = await asyncio.to_thread(mgr.attach, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return attach_instruction_view(instr)

    @app.get(
        "/workspaces/{ws_id}/peek",
        response_model=WorkspacePeekView,
        dependencies=auth_dep,
    )
    async def peek_workspace(ws_id: str) -> WorkspacePeekView:
        # peek() is best-effort by contract; it does not raise (CLAUDE.md).
        # It still reconciles status and shells git/tmux, so it rides the
        # executor like every other blocking manager read.
        mgr = _manager_for(ws_id)
        peek = await asyncio.to_thread(mgr.peek, ws_id)
        return WorkspacePeekView.from_peek(peek)

    @app.get(
        "/workspaces/{ws_id}/pane",
        response_model=WorkspacePaneView,
        dependencies=auth_dep,
    )
    async def workspace_pane(ws_id: str) -> WorkspacePaneView:
        """One-shot agent-pane ANSI snapshot for the dashboard's focused live pane.

        The web dashboard polls this for the single expanded card (status-gated to
        WORKING) instead of mounting N live terminals — the peer-validated "summary
        wall + one live focus" shape. Best-effort like peek; never raises (returns
        ``ansi: null`` when the session isn't live). The tmux capture runs in
        the executor — exactly as the streaming sibling below already does it.
        """
        mgr = _manager_for(ws_id)
        snapshot, taken_at = await asyncio.to_thread(mgr.peek_pane, ws_id)
        return WorkspacePaneView.from_capture(ws_id, snapshot, taken_at)

    @app.get(
        "/workspaces/{ws_id}/pane/stream",
        dependencies=auth_dep,
        responses={
            200: {
                "model": DashboardEvent,
                "description": (
                    "text/event-stream of `pane_snapshot` DashboardEvent frames for "
                    "this one workspace's agent pane. A push upgrade of "
                    "`GET .../pane`: the daemon captures ~1 Hz and emits a frame only "
                    "when the pane changed (else a keepalive comment). The client opens "
                    "this for the single focused WORKING card and closes it on blur, so "
                    "off-screen/idle panes cost nothing."
                ),
            }
        },
    )
    async def workspace_pane_stream(ws_id: str) -> StreamingResponse:
        """Live focused-pane SSE push for one workspace.

        Resolves the workspace once (404 if unknown), then self-paces: each tick
        captures the agent pane via the same best-effort ``peek_pane`` seam the
        one-shot route uses, off-loaded to the executor so the blocking tmux read
        never stalls the loop. A workspace killed mid-stream degrades to an empty
        pane (best-effort, like peek) rather than tearing the connection down — the
        client drops the focus on the next activity tick and closes the stream.
        """
        mgr = _manager_for(ws_id)

        async def _capture() -> tuple[str | None, datetime | None]:
            def _read() -> tuple[str | None, datetime | None]:
                try:
                    return mgr.peek_pane(ws_id)
                except GroveError:
                    # Killed/vanished mid-stream — empty pane, never raise.
                    return None, None

            return await asyncio.to_thread(_read)

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
    async def workspace_commits(
        ws_id: str,
        limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    ) -> list[CommitSummaryView]:
        """Every commit made in this workspace since it was created, newest first.

        The range is anchored on the commit Grove recorded at create time, not
        on the base branch — so a workspace running on the repo root, whose
        branch *is* its base branch, reports its work instead of an empty list,
        and a base branch that moves on afterwards does not change the answer.
        Distinct from ``peek.recent_commits``, a tight 3-row rail summary
        walking all of branch history.
        Best-effort like peek; never raises, returns ``[]`` on failure. The
        ``git log`` walk is blocking, so it runs in the executor.

        ``limit`` is OPT-IN and the default stays uncapped, deliberately. The
        response is a bare array with nowhere to say "there are more", so a
        default cap would be exactly the silent truncation this parameter
        exists to avoid — three shipped consumers read it as the complete log.
        A client that wants lazy loading asks for a page; measured at ~186 B
        per commit, so a 1000-commit branch is ~190 KB.
        """
        mgr = _manager_for(ws_id)
        commits = await asyncio.to_thread(mgr.commits, ws_id)
        rows = [CommitSummaryView.from_summary(c) for c in commits]
        return rows if limit is None else rows[:limit]

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
        :meth:`SessionExplorer.candidates_for` — the remap-picker set,
        which keeps a session the adoption gate rejects (a dead-minted-pointer's
        pre-birth successor, a foreign session in a shared ROOT cwd) so a UI can
        offer it to pin via ``POST .../session``. The default gated view stays
        the workspace's own attributed history.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)
        scan = explorer.candidates_for if candidates else explorer.for_workspace
        try:
            listings = await asyncio.to_thread(scan, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return [SessionSummaryView.from_listing(ls) for ls in listings[:limit]]

    @app.get(
        "/workspaces/{ws_id}/diff",
        response_model=WorkspaceDiffView,
        dependencies=auth_dep,
    )
    async def workspace_diff(
        ws_id: str,
        path: Annotated[str | None, Query()] = None,
    ) -> WorkspaceDiffView:
        """Every file this workspace has changed, as one RAW unified patch.

        Straight from `git diff`, never parsed — the clients render the format
        directly, so the daemon's whole job here is running git and bounding
        the output. Binary files arrive as git's own `Binary files … differ`
        line rather than being filtered.

        **Scope is the worktree against the commit Grove recorded when the
        workspace was created, including untracked files** — so work the agent
        has already committed stays in the patch, and a workspace running on
        the repo root (where the committed-vs-base stats are always zero) has
        an answer at all. A workspace created before Grove recorded that anchor
        falls back to uncommitted-only. `peek`'s `dirty_files` is deliberately
        the narrower *uncommitted* count and will read lower once anything has
        been committed.

        `available: false` with a `reason` means git could not answer (no repo,
        a paused workspace whose worktree is gone) and the UI owes helper text;
        an empty `patch` with `available: true` is the ordinary "nothing
        changed". Bounded to 1 MB, cut at a whole-file boundary so the result
        stays parseable, with `truncated` saying so — `?path=` fetches one
        file's hunks rather than raising the cap. Best-effort like peek; the
        blocking `git diff` runs in the executor.
        """
        mgr = _manager_for(ws_id)
        diff = await asyncio.to_thread(mgr.working_diff, ws_id, path=path)
        return WorkspaceDiffView.from_diff(diff)

    @app.get(
        "/workspaces/{ws_id}/sessions/{session_id}/turns",
        response_model=SessionDetailView,
        dependencies=auth_dep,
    )
    async def workspace_session_turns(
        ws_id: str,
        session_id: str,
        last: Annotated[int | None, Query(ge=1)] = None,
        after_turn: Annotated[int | None, Query(ge=0)] = None,
    ) -> SessionDetailView:
        """The session's conversation, oldest-first; ``last`` keeps only the tail.

        ``session_id`` must be the full id (clients hold it from the sessions
        listing) — prefix resolution stays a CLI affordance. A fleet child's
        ``session_id`` (the Claude sub-agent thread id) never appears in the
        workspace's own listing — falls back to
        ``SessionExplorer.subagent_turns`` before the typed 404, so a fleet
        child's transcript stays reachable. 404 ``agent_session_not_found``
        when the id isn't recorded for this workspace either way.

        ``after_turn=<n>`` is the INCREMENTAL read a live follower wants: it
        returns turn ``n`` onward — inclusive, because the tail turn keeps
        growing while the agent works — and sets ``incremental: true``. Measured
        on a live session, a progress tick re-downloaded 426 KB to gain 269 B;
        the same tick costs 46 KB with a cursor. The response always reports
        ``total_turns`` and ``first_turn_index``, and **``incremental: false``
        means the whole session is attached and the client must REPLACE** — a
        cursor past the end signals a transcript replaced under the reader, so
        it falls back rather than silently skipping turns. ``after_turn`` and
        ``last`` are mutually exclusive (422): ``last`` counts from the end, so
        combining them makes the reported index ambiguous.
        """
        if last is not None and after_turn is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_turn_window",
                    "message": "`last` and `after_turn` are mutually exclusive",
                },
            )
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _read() -> SessionDetailView:
            listings = explorer.for_workspace(ws_id)
            listing: SessionListing | None = next(
                (ls for ls in listings if ls.summary.session_id == session_id), None
            )
            # Read the WHOLE list and window here rather than pushing `last`
            # down: the adapter memoizes per `last`, so a client alternating
            # between a tail and a cursor would hold two projections of one
            # parse, and only the complete list can report an honest
            # `total_turns`.
            if listing is not None:
                window = turn_window(explorer.turns_for(listing), last=last, after_turn=after_turn)
                return SessionDetailView.from_listing_turns(
                    listing,
                    window.turns,
                    total_turns=window.total,
                    first_turn_index=window.first_index,
                    incremental=window.incremental,
                )
            fallback = explorer.subagent_turns(ws_id, session_id)
            if fallback is not None:
                fleet_listing, turns = fallback
                window = turn_window(turns, last=last, after_turn=after_turn)
                return SessionDetailView.from_listing_turns(
                    fleet_listing,
                    window.turns,
                    total_turns=window.total,
                    first_turn_index=window.first_index,
                    incremental=window.incremental,
                )
            raise AgentSessionNotFound(
                f"no session {session_id!r} recorded for workspace {ws_id!r}"
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/todo",
        response_model=TodoListView,
        dependencies=auth_dep,
    )
    async def workspace_todo(ws_id: str) -> TodoListView:
        """The workspace's current todo/checklist state.

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
        try:
            todo = await asyncio.to_thread(mgr.latest_todo, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return TodoListView.from_todo(todo) if todo is not None else TodoListView()

    @app.get(
        "/workspaces/{ws_id}/queue",
        response_model=WorkspaceQueueView,
        dependencies=auth_dep,
    )
    async def workspace_queue(ws_id: str) -> WorkspaceQueueView:
        """What the agent's HARNESS is holding but has not delivered yet.

        The ``/todo`` route's sibling in shape, cost and refusal. Fetch-on-demand
        because a queue is unbounded where the ~1 Hz stream must stay small (the
        stream carries only the count); resolved through
        ``WorkspaceManager.pending_queue``, whose session resolution is
        ``_todo_session_id`` and NOT ``agent_session_id`` — keying on the mint
        excludes codex by construction, which is the bug ``/todo`` already
        documents. Off the loop: the claude arm folds a transcript and the codex
        arm opens a sqlite store.

        404 ``agent_session_not_found`` when the workspace has no session at
        all; a session with an empty queue is a real 200. ``supported`` is the
        third answer the route must keep distinct — a harness whose queue Grove
        cannot observe reports ``supported=False`` with no messages, so a client
        renders "no idea" rather than "nothing waiting".
        """
        mgr = _manager_for(ws_id)

        def _read() -> WorkspaceQueueView:
            # Both halves off the loop: `pending_queue` folds a transcript or
            # opens a sqlite store, and resolving the kind reads the store and
            # reconciles (which for a container workspace reaches `docker`).
            queued = mgr.pending_queue(ws_id)
            supported = get_adapter(mgr.effective_kind(mgr.get(ws_id))).reports_queue
            return WorkspaceQueueView(
                messages=tuple(QueuedMessageView.from_message(m) for m in queued),
                supported=supported,
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/fleet",
        response_model=SubagentFleetView,
        dependencies=auth_dep,
    )
    async def workspace_fleet(ws_id: str) -> SubagentFleetView:
        """The workspace's live sub-agent roster, full detail.

        The ``/todo``/``/queue`` sibling: a fleet is unbounded in count exactly
        like a checklist or a message queue, so only counts ride the ~1 Hz
        stream (``WorkspaceActivityView.fleet``) and the roster itself is
        fetch-on-demand. Sourced from the Claude Code hook's per-
        ``(session_id, agent_id)`` sidecar (``ClaudeHook.list_subagents``) — a
        handful of small file reads, never a transcript parse — so this is
        claude_code-only (no other kind's hook payload carries ``agent_id``
        today). Unlike ``/todo``, a workspace of another kind or one with no
        minted session answers an EMPTY roster rather than 404: "no sub-agents"
        is a real, common answer for a session that never spawned one, not a
        missing-session refusal.
        """
        mgr = _manager_for(ws_id)

        def _read() -> SubagentFleetView:
            state = mgr.get(ws_id)
            if mgr.effective_kind(state) != "claude_code" or not state.agent_session_id:
                return SubagentFleetView(subagents=[])
            records = ClaudeHook.list_subagents(
                state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
            )
            return SubagentFleetView(
                subagents=[SubagentActivityView.from_record(r) for r in records]
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/provision",
        response_model=ProvisionProgressView,
        dependencies=auth_dep,
    )
    async def workspace_provision(ws_id: str) -> ProvisionProgressView:
        """How far along this workspace's container provision is.

        The fetch-on-demand half of the provisioning axis: ``PROVISIONING``
        rides the activity stream (it is the workspace's reconciled status), but
        the headline and the log tail do not, because reading them is a file
        read per workspace and the poll walks the whole host every ~2 s. A
        client that sees the status opens this route and closes it again when
        the status leaves.

        Always 200 for a known workspace — a host workspace, or one whose
        provision finished long ago, answers with an empty headline and no
        lines rather than a 404, because "nothing to report" is a real answer
        (the ``/phase`` precedent, not the ``/todo`` one). The read runs in the
        executor: a cold build's log reaches ~1 MB.
        """
        mgr = _manager_for(ws_id)
        try:
            progress = await asyncio.to_thread(mgr.provision_progress, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return ProvisionProgressView.from_progress(progress)

    @app.post(
        "/workspaces/{ws_id}/phase",
        response_model=PhaseView,
        dependencies=auth_dep,
    )
    async def set_workspace_phase(ws_id: str, body: SetPhaseRequest) -> PhaseView:
        """Set or correct the workspace's task-phase claim from outside.

        The agent itself never calls this — it reports by writing the per-agent
        file Grove names in its launch env, inside its own worktree (the file
        channel ``grove.core.phase`` documents). This route is the manual
        counterpart,
        for a human or an orchestrator to set/correct the claim, mirroring
        ``remap_workspace_session``'s trusted-write shape. Runs in the
        executor: ``WorkspaceManager.set_phase`` does blocking file I/O.

        ``body.ticket`` (``"<provider>:<id>"``) routes the claim onto that
        ticket's entry and leaves the workspace's own claim alone, mirroring
        ``PhaseFile.write``'s split; naming a ticket the workspace has no ref
        for raises ``TicketNotAttached`` from the engine, which rides the same
        typed ``GroveError`` envelope as every other refusal here (404
        ``ticket_not_attached``) rather than an unhandled 500.
        """
        mgr = _manager_for(ws_id)
        try:
            report = await asyncio.to_thread(
                mgr.set_phase,
                ws_id,
                body.phase,
                body.note,
                blocked=body.blocked,
                ticket=body.ticket,
            )
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return PhaseView.from_report(report)

    @app.get(
        "/workspaces/{ws_id}/phase",
        response_model=PhaseView | None,
        dependencies=auth_dep,
    )
    async def workspace_phase(ws_id: str) -> PhaseView | None:
        """The workspace's current task-phase claim.

        ``null`` (200, never 404) means the agent has not reported one yet —
        a real, distinct answer from "phase=scoping", not an error: a fleet
        watcher needs to tell "hasn't reported" from "is at step zero". Runs
        in the executor: ``WorkspaceManager.phase`` reads the phase file.
        """
        mgr = _manager_for(ws_id)
        try:
            report = await asyncio.to_thread(mgr.phase, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return PhaseView.from_report(report) if report is not None else None

    @app.get("/sessions", response_model=list[SessionSummaryView], dependencies=auth_dep)
    async def list_sessions(
        repo: Annotated[Path | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[SessionSummaryView]:
        """Agent sessions, newest-first — one project's (``repo``) or the whole
        host's (``repo`` omitted).

        Scope is a value of the SAME parameter, never a second endpoint: both
        answer "which sessions exist and where did they come from", and
        ``GET /workspaces`` already established that omitting ``repo`` widens a
        listing rather than narrowing it.

        **Project scope** spans every scan root of one repo (Grove-managed and
        hand-staged worktrees alike) via ``SessionExplorer.list``, which
        full-parses every transcript — heavy, and acceptable because a UI
        fetches it only on section expand. An unknown root is a 404 rather than
        an empty list so a typo'd path can't masquerade as "no sessions yet".

        **Host scope** is the Session Catalog: every session in every adapter's
        store, across every repo (and none), built from one bounded head read
        per session — so it is fast on a several-hundred-session host but
        carries no ``activity``/``size_bytes`` (the class docstring on
        ``SessionSummaryView`` spells out what a catalog row cannot know). It
        adds the resolved ``project``, the recording ``cwd``, and an honest
        ``live`` flag. Request-scoped behind a short TTL memo — the catalog is
        never on the activity poll.

        Both scopes block, so both run in the executor.
        """
        if repo is None:
            rows = await asyncio.to_thread(catalog.rows)
            # The one trigger for the turn-count pass, and it returns at once:
            # counting is a full transcript parse per changed session, so it
            # runs on the catalog's own worker and this response ships whatever
            # was already counted. A cold host answers every `turn_count` null
            # and fills in over the following scans.
            catalog.count_turns_in_background()
            return [SessionSummaryView.from_catalog(e) for e in rows[:limit]]
        explorer = SessionExplorer(registry.get(_known_root(repo)))
        try:
            listings = await asyncio.to_thread(lambda: explorer.list(limit=limit))
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return [SessionSummaryView.from_listing(ls) for ls in listings]

    @app.get(
        "/sessions/{session_id}/turns",
        response_model=SessionDetailView,
        dependencies=auth_dep,
    )
    async def session_turns(
        session_id: str,
        kind: Annotated[str, Query()],
        cwd: Annotated[str, Query()],
        last: Annotated[int | None, Query(ge=1)] = None,
    ) -> SessionDetailView:
        """The conversation of a session identified WITHOUT a workspace — the
        catalog's drill-in, and the one real wire change the catalog needs.

        ``GET /workspaces/{id}/sessions/{sid}/turns`` resolves a session
        *through* a workspace, which a catalog row may not have: most sessions
        on a host were never launched by Grove. So this resolves by the
        coordinates a catalog row actually carries — ``(kind, cwd,
        session_id)`` — which is also exactly what an adapter needs to read a
        transcript. Pass ``cwd`` back verbatim as the row reported it; the
        adapters match a recorded cwd by string. A row whose head read never
        recovered a cwd (~2 % of Claude transcripts) is not drillable at all,
        and 404s here.

        The row itself comes from the same TTL-memoized catalog the listing
        served, so list → drill-in is one scan. 404 ``agent_session_not_found``
        when no catalog row matches — including an unrecognized ``kind``. Runs
        in the executor: both the lookup and the parse block. Sessions written
        under a workspace's pinned transcript config dir stay on the
        workspace-scoped route, which resolves that override; the host scan
        sees only what the daemon's own environment can reach.
        """

        def _read() -> SessionDetailView:
            entry = catalog.find(kind=kind, cwd=cwd, session_id=session_id)
            if entry is None:
                raise AgentSessionNotFound(
                    f"no {kind!r} session {session_id!r} recorded under {cwd}"
                )
            # No `after_turn` here on purpose: this route browses HISTORY (a
            # session that may belong to no workspace and mostly is not
            # running), so nothing follows a growing tail. It still reports the
            # window it served, so `last` stops being a silent truncation.
            window = turn_window(
                get_adapter(kind).read_turns(Path(cwd), session_id),
                last=last,
                after_turn=None,
            )
            return SessionDetailView.from_catalog_turns(
                entry,
                window.turns,
                total_turns=window.total,
                first_turn_index=window.first_index,
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/sessions/{session_id}/queries",
        response_model=list[SessionQueryView],
        dependencies=auth_dep,
    )
    async def session_queries(
        session_id: str,
        kind: Annotated[str, Query()],
        cwd: Annotated[str, Query()],
        last: Annotated[int | None, Query(ge=1)] = None,
    ) -> list[SessionQueryView]:
        """Every direct user query in one session, oldest first.

        A transcript is unbounded in session length, while this response is
        bounded by how many times a human typed something — usually dozens, not
        thousands — so full query text is affordable here where it is not for
        the transcript's bounded turn view. ``last`` is an opt-in tail only;
        the complete normalized message spine is always read before it applies.

        Like the workspace-less turns drill-in, the session is addressed by the
        catalog row's ``(kind, cwd, session_id)`` coordinates. ``cwd`` must be
        passed back byte-for-byte from that row, and a coordinate mismatch is a
        typed 404 rather than a chance to expose a different transcript.
        """

        def _read() -> list[SessionQueryView]:
            entry = catalog.find(kind=kind, cwd=cwd, session_id=session_id)
            if entry is None:
                raise AgentSessionNotFound(
                    f"no {kind!r} session {session_id!r} recorded under {cwd}"
                )
            queries = SessionExplorer.queries_from_messages(
                get_adapter(kind).read_messages(Path(cwd), session_id)
            )
            selected = queries[-last:] if last is not None else queries
            return [SessionQueryView.from_query(query) for query in selected]

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get("/projects", response_model=list[ProjectView], dependencies=auth_dep)
    async def list_projects() -> list[ProjectView]:
        """Every project this daemon serves — the repo-discovery seam.

        The entry point for a client that holds no path yet: `repo_root` from a
        row here is what `/agents`, `/branches`, and `POST /workspaces` all take
        as their `repo` argument. In-process callers (the TUI's project picker)
        read `known_projects()` directly; a remote one cannot, so this returns
        the same union of store-derived and config-declared projects, which is
        what keeps an empty or freshly added repo visible.

        No `repo` query param, deliberately: this is the route you call *before*
        you know a repo root, so it is the one listing that is not repo-scoped.

        Runs in the executor because `known_projects()` re-reads the store and
        may pay a `git rev-parse` per declared nested project, the same reason
        `/agents` off-loads its build.
        """
        projects = await asyncio.to_thread(registry.known_projects)
        # Stable ordering so a client can diff two calls. The engine returns
        # dict-insertion order over a set-derived scan, which is not stable.
        return sorted(
            (ProjectView.from_project(p) for p in projects),
            key=lambda v: (v.repo_name.lower(), v.cwd),
        )

    @app.get("/agents", response_model=list[AgentSummaryView], dependencies=auth_dep)
    async def list_agents(repo: Annotated[Path, Query()]) -> list[AgentSummaryView]:
        """Configured agents for one repo's cascade — the new-workspace picker source.

        The TUI reads ``cfg.agents`` in-process to build its create-modal dropdown;
        a remote create form can't, so this returns the same merged list. ``repo``
        dispatches like ``/branches`` (per-repo cascade), so a project-scoped agent
        defined in ``<repo>/.grove/config.json`` shows up here too, and it must be
        a root the user registered — this route runs the command that cascade
        names (see ``_known_root``). Each row's ``models`` catalog is resolved via
        the single ``resolve_models`` seam (config override, else live adapter
        discovery); Codex discovery shells out (``codex debug models``), so the
        whole list is built in the executor to keep that subprocess off the event
        loop.
        """
        mgr = registry.get(_known_root(repo))
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

        return await asyncio.to_thread(_build)

    @app.get("/defaults", response_model=WorkspaceDefaultsView, dependencies=auth_dep)
    async def get_workspace_defaults(
        repo: Annotated[Path, Query()],
    ) -> WorkspaceDefaultsView:
        """Resolved create-form defaults for one registered repo's cascade.

        The raw ``defaults`` section is deliberately not returned: runtime,
        brief, branch mode, and init behavior already have create-path fallbacks,
        and a remote form needs the same pre-selected answers the TUI sees.
        """
        root = _known_root(repo)
        registry.get(root)
        # Managers cache their config for lifecycle consistency. Defaults writes
        # are immediately visible instead, so re-resolve the read-only cascade.
        cfg_for_repo = await asyncio.to_thread(load_config, root)
        return WorkspaceDefaultsView.from_config(cfg_for_repo)

    @app.put(
        "/defaults",
        response_model=WorkspaceDefaultsSaveView,
        dependencies=auth_dep,
    )
    async def save_defaults(
        defaults: WorkspaceDefaults,
        scope: Annotated[DefaultsScope, Query()],
        repo: Annotated[Path | None, Query()] = None,
    ) -> WorkspaceDefaultsSaveView:
        """Replace one config layer's complete create-defaults object.

        This is not a patch: ``save_workspace_defaults`` replaces the whole
        ``defaults`` object, so callers must send every answer they intend to
        retain or an omitted field is cleared. Project scopes require a registered
        ``repo``; the user scope deliberately has no repository requirement.
        """
        repo_root = _known_root(repo) if repo is not None else None
        # `WorkspaceDefaults` keeps optional fields for config layers, but this
        # route's replacement semantics require an explicit value or clear for
        # every field — don't let a partial JSON body silently erase the rest.
        if defaults.model_fields_set != set(WorkspaceDefaults.model_fields):
            missing = sorted(set(WorkspaceDefaults.model_fields) - defaults.model_fields_set)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "incomplete_defaults",
                    "message": f"complete defaults replacement requires: {', '.join(missing)}",
                },
            )

        def _save() -> WorkspaceDefaultsSaveView:
            saved_fields = defaults.model_dump(exclude_none=True)
            shadowed = (
                tuple(name for name in saved_fields if name in user_defaults_keys())
                if scope is not DefaultsScope.USER
                else ()
            )
            path = save_workspace_defaults(defaults, scope=scope, repo_root=repo_root)
            return WorkspaceDefaultsSaveView(path=str(path), shadowed=shadowed)

        try:
            return await asyncio.to_thread(_save)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/branches",
        response_model=list[BranchInfo],
        dependencies=auth_dep,
    )
    async def list_branches(
        repo: Annotated[Path, Query()],
        scope: Annotated[Literal["local", "remote"], Query()],
    ) -> list[BranchInfo]:
        mgr = registry.get(_known_root(repo))
        # `git branch` / `git ls-remote` are blocking subprocesses — and the
        # remote scope talks to a network remote — so this never runs on the loop.
        fetch = mgr.list_local_branches if scope == "local" else mgr.list_remote_branches
        try:
            branches = await asyncio.to_thread(fetch)
        except GroveError as exc:
            # A registered root that git nonetheless refuses surfaces as
            # ``GitError``; route it through the same envelope the rest of the
            # daemon uses so clients see a consistent error shape. An unknown
            # path never reaches here — ``_known_root`` 404s it first, which is
            # what stops a missing directory 500ing out of ``subprocess(cwd=)``.
            raise _grove_error_to_http(exc) from exc
        return list(branches)

    @app.get(
        "/tickets/providers",
        response_model=list[TicketProviderView],
        dependencies=auth_dep,
    )
    async def ticket_providers(repo: Annotated[Path, Query()]) -> list[TicketProviderView]:
        """The repo's enabled ticket providers — the client's picker source.

        ``repo`` dispatches per-repo cascade like ``/branches``: a project
        enables its tracker in ``<repo>/.grove/config.json``. Pure (no network);
        each row's ``configured`` flag tells a client to gray out a provider that
        is enabled but missing its token rather than offer a dead picker.
        """
        mgr = registry.get(_known_root(repo))
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
        """Tickets assigned to the authenticated user.

        With ``provider`` given, query exactly that tracker. Without it,
        aggregate across every enabled provider, SKIPPING any whose credential
        is absent (``configured`` is False) — so a half-configured repo still
        returns its working providers' tickets instead of failing the whole
        request on one unconfigured tracker. A configured provider whose API
        call fails still surfaces its 502. Every arm is a blocking HTTP call to
        a remote tracker, so the whole aggregation runs in the executor.
        """
        mgr = registry.get(_known_root(repo))

        def _fetch() -> list[TicketRef]:
            if provider is not None:
                return list(mgr.ticket_providers.get(provider).list_assigned(status=status))
            out: list[TicketRef] = []
            for p in mgr.ticket_providers.providers():
                if not p.configured:
                    continue
                out.extend(p.list_assigned(status=status))
            return out

        try:
            return await asyncio.to_thread(_fetch)
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
        """Fetch one ticket by its canonical key.

        404 ``ticket_provider_not_configured`` when the named tracker isn't
        enabled; 502 ``ticket_provider_error`` when the upstream API fails.
        The upstream GET is blocking, so it runs in the executor.
        """
        mgr = registry.get(_known_root(repo))
        try:
            return await asyncio.to_thread(
                lambda: mgr.ticket_providers.get(provider).get_ticket(ticket_id)
            )
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/tickets",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def attach_ticket(ws_id: str, body: _TicketAttachBody) -> WorkspaceStateView:
        """Manually associate a ticket with a workspace.

        The body is either a resolved ``TicketSelector`` (``{provider, id[,
        kind]}``) or a raw ``{ref}`` — a URL, ``#42``, ``42``, or
        ``owner/repo#42``, resolved SERVER-SIDE through
        ``TicketProviderRegistry.resolve_link``
        (``WorkspaceManager.attach_link``) rather than a client re-parsing
        it. No network either way: association is pure, and link resolution
        only matches the ref's shape against the repo's *enabled* providers.
        Idempotent by ``(provider, id)`` in the engine. An ambiguous or
        unparseable ``ref`` is 422 (``ticket_link_ambiguous`` /
        ``ticket_link_invalid``), never a silent guess. Returns the updated
        workspace with its refreshed ``ticket_refs``.
        """
        mgr = _manager_for(ws_id)
        # No network, but it reconciles status (tmux forks) and rewrites the
        # store — blocking, so it joins the rest in the executor.
        attach = (
            (lambda: mgr.attach_link(ws_id, body.ref))
            if isinstance(body, _TicketLinkBody)
            else (lambda: mgr.attach_ticket(ws_id, body))
        )
        try:
            state = await asyncio.to_thread(attach)
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
        """Remove a ticket association. Idempotent — a missing ref is a no-op."""
        mgr = _manager_for(ws_id)
        try:
            state = await asyncio.to_thread(mgr.detach_ticket, ws_id, provider, ticket_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.delete(
        "/workspaces/{ws_id}/tickets",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def detach_ticket_by_ref(
        ws_id: str,
        ref: Annotated[str, Query(min_length=1)],
    ) -> WorkspaceStateView:
        """Remove a ticket association by raw reference.

        The ref-resolving sibling of ``DELETE .../tickets/{provider}/{ticket_id}``
        above: ``ref`` rides as a query param (a DELETE with no path-named
        ticket carries no conventional body here) rather than a JSON body,
        matching the ``ticket=<provider>:<id>`` query-filter precedent on
        ``GET /workspaces``. Resolved through the same
        ``TicketProviderRegistry.resolve_link`` the attach-by-ref body uses,
        then dispatched to the same ``detach_ticket`` engine call. Idempotent
        — detaching a ticket that was never attached is a no-op.
        """
        mgr = _manager_for(ws_id)

        try:
            # `resolve_link` is pure shape-matching against the repo's enabled
            # providers (no network, no disk) — only the detach itself blocks.
            selector = mgr.ticket_providers.resolve_link(ref)
            state = await asyncio.to_thread(
                mgr.detach_ticket, ws_id, selector.provider, selector.id
            )
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    return app
