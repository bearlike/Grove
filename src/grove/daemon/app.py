"""FastAPI factory + lifespan + route handlers for the Grove daemon.

All routes are 1:1 with ``WorkspaceManager`` methods. Multi-repo dispatch
goes through ``RepoRegistry``. Clients poll for state; the one exception is
the panel proxy, which bridges a WebSocket because noVNC upgrades. Routes
require a bearer session (``grove.daemon.auth``) with the same exception:
``/panel/{token}/…`` authenticates on the token in its own path, because an
iframe and a WebSocket handshake can carry a credential nowhere else.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import getpass
import hashlib
import hmac
import os
import platform
import secrets
import socket
import time
import urllib.error
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from concurrent.futures import Future
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.gzip import GZipMiddleware
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import WebSocketException

from grove import __version__ as _GROVE_VERSION
from grove.core import paths as core_paths
from grove.core.activity import ActivityService, WorkspaceActivity
from grove.core.activity_runtime import ActivityRuntime
from grove.core.activity_sources import ActivitySources
from grove.core.admission import Admission
from grove.core.agents import configure_transcript_caches, get_adapter, resolve_models
from grove.core.agents.hook import HOOK_INGEST_ROUTE, ClaudeHook
from grove.core.agents.registry import ModelProbeMemo
from grove.core.auth import SessionStore
from grove.core.config import (
    DefaultsScope,
    GroveConfig,
    TriggerRule,
    WorkspaceDefaults,
    load_config,
    save_workspace_defaults,
    shadowing_defaults_keys,
)
from grove.core.container_infra import ProjectInfra
from grove.core.contracts.activity import (
    DashboardEvent,
    DashboardSnapshotView,
    SessionActivityView,
    SubagentActivityView,
    SubagentFleetView,
    WorkspaceActivityView,
)
from grove.core.contracts.agents import AgentSummaryView, ModelOptionView
from grove.core.contracts.artifacts import ArtifactAttachRequest
from grove.core.contracts.assignment_events import AssignmentTicketEvent
from grove.core.contracts.attachments import AttachmentUploadRequest, AttachmentView
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.diagrams import (
    DiagramDocumentView,
    DiagramOpenRequest,
    DiagramPreviewUploadRequest,
    DiagramPreviewView,
    DiagramStopRequest,
    DiagramUpdateRequest,
)
from grove.core.contracts.feedback import TurnFeedbackRequest
from grove.core.contracts.gallery import (
    GalleryDocumentView,
    GalleryItemView,
    GalleryPreviewUploadRequest,
    GalleryPreviewView,
)
from grove.core.contracts.history import WorkspaceHistoryView
from grove.core.contracts.issueops import IssueOpsEvent, IssueOpsOutcome
from grove.core.contracts.keys import SendKeysRequest
from grove.core.contracts.launch import (
    LaunchOptionsView,
    LaunchPreviewView,
    RuntimeOptionView,
)
from grove.core.contracts.live import LivePulseView
from grove.core.contracts.phase import PhaseView, SetPhaseRequest
from grove.core.contracts.projects import AddProjectRequest, ProjectCandidatesView
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
    ToolCallView,
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
    RuntimeTargetView,
    WhoamiView,
    WorkspaceDefaultsSaveView,
    WorkspaceDefaultsView,
    WorkspaceDiffView,
    WorkspacePanelView,
    WorkspacePaneView,
    WorkspacePeekView,
    WorkspaceStateView,
    attach_instruction_view,
)
from grove.core.contracts.watches import MIN_INTERVAL, TicketPredicate
from grove.core.delegations import DelegationJournal
from grove.core.devcontainer import DevcontainerCli
from grove.core.errors import (
    AgentSessionNotFound,
    ArtifactInvalid,
    ArtifactUnavailable,
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchError,
    BranchNotFound,
    CapabilityUnavailable,
    ConfigError,
    DiagramConflict,
    DiagramUnavailable,
    EffortNotSupported,
    GitError,
    GroveError,
    InvalidLaunch,
    MacroNotFound,
    PaneNotFound,
    ProjectNotOnNode,
    ProjectTargetRefused,
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
    WatchUnobservable,
    WorkCapacityExceeded,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.fleet_digest import FleetDigest, MailboxBatch
from grove.core.gallery import DiagramGallery, GalleryItem
from grove.core.idle import FreezeSpans, IdleRule, IdleWatcher
from grove.core.issueops import AssigneePoller, IssueOpsEngine, TicketStatusPublisher
from grove.core.launch_options import node_options, runtime_options
from grove.core.loaded_source import LoadedSource
from grove.core.mailboxes import MailboxDelivery
from grove.core.manager import WorkspaceManager
from grove.core.model_catalog import model_options
from grove.core.native_owners import NativeOwnerRegistry
from grove.core.notifications import NotificationBroker
from grove.core.otel_resource import node_name
from grove.core.pane_events import (
    PaneEventHub,
    PaneEventsUnavailable,
    PaneKey,
    PaneSnapshot,
    TmuxControlPaneSource,
)
from grove.core.panels import PanelResolver, PanelTarget
from grove.core.phase import PhaseReport
from grove.core.project_onboarding import ProjectOnboarding
from grove.core.reaper import (
    FilesCollector,
    ProcessCollector,
    Reaper,
    ReaperStore,
    ReaperWatcher,
    TmuxCollector,
)
from grove.core.registry import Project
from grove.core.release import ReleaseChecker, ReleaseStatus
from grove.core.sessions import SessionCatalog, SessionExplorer, SessionListing
from grove.core.share_policy import SharePolicy, SharePolicyStore
from grove.core.sidebar_pins import SidebarPinStore
from grove.core.slack import SlackBridge
from grove.core.speech import OpenAISpeech
from grove.core.store import JsonWorkspaceStore
from grove.core.telemetry.feedback import (
    LangfuseScores,
    MissingScoreConfig,
    Rating,
    TurnFeedback,
)
from grove.core.telemetry.outbox import OutboxSink
from grove.core.telemetry.project import LangfuseProject
from grove.core.tickets.provider import TicketProvider
from grove.core.tickets.reads import TicketReads
from grove.core.tickets.registry import TicketProviderRegistry
from grove.core.trace import build_span_sink, turn_trace_id
from grove.core.trace_forwarder import TraceForwarder
from grove.core.watches import (
    CiWatcher,
    CommandWatcher,
    MailboxWatchCourier,
    StandingWatches,
    TicketWatcher,
    TimerWatcher,
    TriggerDispatch,
    TriggerEvidence,
    TriggerWatcher,
    WatcherRegistry,
    WatchLog,
    WatchScheduler,
)
from grove.core.watches.dispatch import Fleet
from grove.core.watches.ticket import ProviderLookup
from grove.core.watches.triggers import EvidenceLookup, TextRenderer
from grove.core.workspace import Runtime, WorkspaceState
from grove.core.workspace_history import WorkspaceHistoryStore
from grove.core.workspace_runtime import WorkspaceRuntimes
from grove.daemon._audience import _PollAudience
from grove.daemon._catalog import _CatalogMemo, _GalleryMemo
from grove.daemon._catalog_sources import _CatalogSources
from grove.daemon._fleet_stream import (
    FleetSnapshot,
    FleetSnapshotStream,
    fleet_heartbeat_frame,
    fleet_sse_frame,
)
from grove.daemon._lifecycle import _LifecycleRunner
from grove.daemon._live import LiveHub
from grove.daemon._pane_stream import _PaneStreamer
from grove.daemon._project_sources import _ProjectSources
from grove.daemon._public import PublicWorkspaceReader, ShareNotFound
from grove.daemon._public_tickets import _PublicTicketMemo
from grove.daemon._readiness import _ReadinessMemo
from grove.daemon._runtime_sources import RuntimeSources
from grove.daemon._scoped_events import ScopedWorkspaceEvents
from grove.daemon._sse import _SseHub
from grove.daemon._turns import turn_window
from grove.daemon.auth import (
    build_auth_router,
    make_require_hook_token,
    make_require_principal,
    principal_of,
)
from grove.daemon.federation import Federation, create_resolver
from grove.daemon.fleet_digest import FleetDigestRouter
from grove.daemon.mailbox_socket import MailboxSocket
from grove.daemon.mailboxes import MailboxRouter, OwnerSteerClient
from grove.daemon.oidc import OidcVerifier
from grove.daemon.repos import RepoRegistry
from grove.daemon.request_audit import RequestAudit
from grove.daemon.sidebar_pins import SidebarPinRouter
from grove.daemon.speech import SpeechRouter
from grove.daemon.usage import _default_usage_service, build_usage_router
from grove.daemon.watches import WatchRouter

if TYPE_CHECKING:
    # Type-only: the real import is deferred into `build_app`, gated on
    # `cfg.telemetry.receiver.enabled` — see the construction site for why an
    # unconditional module-scope import is wrong here (unlike `trace_forwarder`,
    # importing `grove.core.telemetry.receiver` unconditionally pulls in
    # `opentelemetry-proto` even for a daemon that never turns the receiver on).
    from grove.core.telemetry.receiver import OtlpIngest

# How long a minted panel URL stays usable. Long enough that a dashboard left
# open all day keeps its browser tile alive without a refetch, short enough that
# a URL copied out of devtools is not a durable key to the workspace's sidecar.
_PANEL_TOKEN_TTL = timedelta(hours=12)

# The first orphan scan is best effort after readiness: its await must not
# retain a task forever. Reaper skips a later pass while this thread still runs.
_STARTUP_REAPER_TIMEOUT_SECONDS = 180

# Signing key for panel tokens, minted once per process and never persisted.
# Module scope rather than per-app so a test building two apps still validates
# its own tokens; process scope rather than a file so a daemon restart revokes
# every outstanding panel URL (the webapp refetches on mount, so nothing breaks).
_panel_token_key = secrets.token_bytes(32)


def _close_best_effort(name: str, close: Callable[[], None]) -> None:
    """Run one shutdown hook without preventing independent owners from closing."""
    try:
        close()
    except Exception as exc:
        logger.warning("{} shutdown failed: {}", name, type(exc).__name__)


class _StartupTimer:
    """Time each lifespan startup step so a slow start names its own step.

    The listener is not bound until the lifespan yields, so a slow step reads
    from outside as a dead port with nothing in the journal. Each step logs its
    start and end at INFO, which the daemon's default WARNING sink hides, and
    `report` writes ONE warning per start listing every step slowest first, so
    the journal carries the answer without anyone turning on debug logging.
    A start that never finishes never reaches `report`; for that case the INFO
    lines under `GROVE_DEBUG` show the last step entered.
    """

    def __init__(self) -> None:
        """Start the clock for the whole startup."""
        self._began = time.monotonic()
        self._steps: list[tuple[str, float]] = []

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Time one startup step.

        Args:
            name: How the step reads in the journal.
        """
        logger.info("lifespan step {} starting", name)
        began = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - began
            self._steps.append((name, elapsed))
            logger.info("lifespan step {} took {:.2f}s", name, elapsed)

    def summary(self) -> str:
        """The one-line report, slowest step first."""
        total = time.monotonic() - self._began
        steps = ", ".join(
            f"{name} {seconds:.2f}s"
            for name, seconds in sorted(self._steps, key=lambda step: step[1], reverse=True)
        )
        return f"lifespan ready in {total:.2f}s: {steps}"

    def report(self) -> None:
        """Write the summary to the journal once startup has finished."""
        logger.warning("{}", self.summary())


async def _source_startup(*sources: ActivitySources | _CatalogSources | _ProjectSources) -> None:
    """Arm the file-event observers off the readiness path, loudly on failure.

    Moving work off startup must not move its failures into silence: a bare
    `create_task` reports an exception only at GC time, by which point the
    daemon has been running without watchers and nothing says so. Each group is
    started independently so one unarmable set does not cost the other — the
    same degraded-observer rule the groups apply to their own roots.
    """
    for source in sources:
        try:
            await source.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "file event sources failed to arm ({}): {} — the daemon serves "
                "without them and reads fall back to explicit reconciliation",
                type(source).__name__,
                type(exc).__name__,
            )


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
    started_at: datetime,
    release: ReleaseStatus,
    *,
    restart_required: bool = False,
    langfuse_host: str | None = None,
    langfuse_project_id: str | None = None,
    feedback_reasons: list[str] | None = None,
    positive_feedback_reasons: list[str] | None = None,
    speech: bool = False,
    federation: str = "disabled",
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
        restart_required=restart_required,
        langfuse_host=langfuse_host,
        langfuse_project_id=langfuse_project_id,
        feedback_reasons=feedback_reasons or [],
        positive_feedback_reasons=positive_feedback_reasons or [],
        speech=speech,
        federation=federation,
    )


def _langfuse_credentials(cfg: GroveConfig) -> tuple[str, str] | None:
    """``(host, "Basic …")`` for the configured Langfuse, or ``None``.

    Gated on exactly the same resolution as the host below — a credential
    without a usable host reaches nothing. The auth value is the composed
    exporter header, reused rather than re-deriving the Basic token from the
    key pair: one composition site, and this module never touches the secret
    itself.
    """
    telemetry = cfg.telemetry
    if not telemetry.enabled:
        return None
    derived = telemetry.derive_env(os.environ)
    if telemetry.unresolved(derived):
        return None
    host = derived.get("LANGFUSE_HOST")
    headers = derived.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    auth = next(
        (part.removeprefix("Authorization=") for part in headers.split(",") if "=" in part),
        "",
    )
    if not host or not auth.startswith("Basic "):
        return None
    return host, auth


def _resolve_langfuse_project(cfg: GroveConfig, projects: LangfuseProject) -> str | None:
    """The Langfuse project these credentials belong to, or ``None``.

    Asked of Langfuse itself, because a key pair names its project and the
    config never does. Cached in ``projects`` (one instance per daemon), so a
    whoami costs an HTTP call at most once per TTL and never on a failure path.
    """
    credentials = _langfuse_credentials(cfg)
    return projects.resolve(*credentials) if credentials else None


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

    ``attachments`` names files already stored through
    ``POST /workspaces/{id}/attachments``. Ids, never paths: a path chosen by
    the caller is the caller choosing which file the agent is told to open,
    and the engine resolves an id against one directory it owns. An id that no
    longer resolves is skipped rather than refused — see
    ``WorkspaceManager.send_message``.
    """

    text: str = Field(min_length=1)
    attachments: list[str] = []


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


class _SwitchEffortBody(BaseModel):
    """Change the running native session's reasoning effort.

    ``effort`` must be one of the agent's ``AgentSummaryView.efforts``; the
    pattern keeps a flag-shaped value out of any provider frame, and the engine
    refuses a level the kind does not list. Module-scope for the same
    forward-ref reason above."""

    effort: str = Field(pattern=r"^[a-z]+$", max_length=16)


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


def _sse_response(body: AsyncIterator[str]) -> StreamingResponse:
    """An unbuffered ``text/event-stream`` response over ``body``."""
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


def _workspace_lookup(registry: RepoRegistry) -> Callable[[str], WorkspaceState | None]:
    """Adapt the registry's raising resolver to the watcher's optional one.

    `resolve_workspace` raises for a ref that names nothing, which is right for
    a user-typed verb and wrong here: a command watch outlives its workspace by
    design, and a killed one is an ordinary outcome the watcher reports rather
    than an error that would abort the whole tick.
    """

    def lookup(workspace_id: str) -> WorkspaceState | None:
        try:
            _, state = registry.resolve_workspace(workspace_id)
        except GroveError:
            return None
        return state

    return lookup


def _ticket_provider_lookup(registry: RepoRegistry) -> ProviderLookup:
    """Resolve a ticket watch's provider through its workspace's own repo cascade.

    ``None`` once the workspace is gone or its repo no longer enables that
    tracker. The watcher reads that as "cannot tell", never as a change.
    """

    def lookup(predicate: TicketPredicate) -> TicketProvider | None:
        try:
            manager, _ = registry.resolve_workspace(predicate.workspace_id)
            return manager.ticket_providers.get(predicate.provider)
        except GroveError:
            return None

    return lookup


def _trigger_evidence(registry: RepoRegistry, activity: ActivityService) -> EvidenceLookup:
    """Fresh evidence for the trigger rules, computed for ONE workspace and never published.

    Deliberately not the maintained projection: a row the poll has not
    refreshed (the poll waits while no dashboard is open) can still read
    WORKING for an agent that has since stopped, and an instruction sent on
    that is the one case triggers must never hit. ``workspace_row`` would serve
    the maintained row, so this asks for a new one. The check runs once per
    workspace per ``triggers.check_seconds`` on the watch worker, so its cost
    is one row read per workspace per minute, not a fleet scan.

    The rules come from the workspace's OWN repo cascade, so a project can
    override a built-in rule or add its own.
    """

    def lookup(workspace_id: str) -> TriggerEvidence | None:
        try:
            manager, state = registry.resolve_workspace(workspace_id)
            row = activity.prepare_workspace_refresh(str(manager.repo_root), state.id)
        except GroveError:
            return None
        if row is None:
            return None
        return TriggerEvidence.of(row, manager.config.triggers.active())

    return lookup


def _idle_watcher(
    registry: RepoRegistry, activity: ActivityService, lifecycle: _LifecycleRunner
) -> IdleWatcher:
    """The idle check over the maintained projection, confirmed by a fresh row per candidate."""

    def fresh(row: WorkspaceActivity) -> WorkspaceActivity | None:
        try:
            return activity.prepare_workspace_refresh(row.state.repo_root, row.state.id)
        except GroveError:
            return None

    def freeze(row: WorkspaceActivity) -> None:
        registry.get(Path(row.state.repo_root)).freeze(row.state.id)

    def hibernate(row: WorkspaceActivity) -> None:
        registry.get(Path(row.state.repo_root)).hibernate(row.state.id)

    def rule_for(row: WorkspaceActivity) -> IdleRule:
        idle = registry.get(Path(row.state.repo_root)).config.idle
        return IdleRule(
            cooldown=timedelta(seconds=idle.cooldown_seconds),
            hibernate_after=(
                timedelta(seconds=idle.hibernate_after_seconds)
                if idle.hibernate_after_seconds
                else None
            ),
        )

    def submit(workspace_id: str, action: Callable[[], None]) -> Future[None]:
        return lifecycle.submit(workspace_id, action)

    return IdleWatcher(
        candidates=lambda: tuple(activity.snapshot().iter_workspaces()),
        fresh=fresh,
        freeze=freeze,
        rule_for=rule_for,
        hibernate=hibernate,
        submit=submit,
    )


def _trigger_text(registry: RepoRegistry) -> TextRenderer:
    """Render a rule's text through the workspace's own manager, which owns the facts."""

    def render(rule: TriggerRule, seen: TriggerEvidence, values: Mapping[str, str]) -> str:
        manager, state = registry.resolve_workspace(seen.state.id)
        return manager.trigger_text(state, rule, rule.render(values))

    return render


def _trigger_fleet(registry: RepoRegistry) -> Fleet:
    """Every workspace on the host with its current phase and its repo's trigger rules.

    For a manual dispatch's selector. The phase comes from the manager's own
    reader, so a ``--phase`` filter means what the dashboard shows.
    """

    def fleet() -> list[tuple[WorkspaceState, PhaseReport | None, tuple[TriggerRule, ...]]]:
        rows: list[tuple[WorkspaceState, PhaseReport | None, tuple[TriggerRule, ...]]] = []
        for root in registry.known_roots():
            try:
                manager = registry.get(root)
                rules = tuple(manager.config.triggers.rules)
                rows.extend((state, manager.phase_for(state), rules) for state in manager.list())
            except GroveError:
                continue
        return rows

    return fleet


def build_app(  # noqa: PLR0915
    *,
    cfg: GroveConfig,
    store: JsonWorkspaceStore,
    auth_store: SessionStore | None = None,
    history_store: WorkspaceHistoryStore | None = None,
    share_policy_store: SharePolicyStore | None = None,
    sidebar_pin_store: SidebarPinStore | None = None,
    notification_broker: NotificationBroker | None = None,
    release_checker: ReleaseChecker | None = None,
    issue_ops_engine: IssueOpsEngine | None = None,
    status_publisher: TicketStatusPublisher | None = None,
    assignee_poller: AssigneePoller | None = None,
    slack_bridge: SlackBridge | None = None,
    loaded_source: LoadedSource | None = None,
) -> FastAPI:
    """Construct the daemon's FastAPI app.

    Tests call this directly; the CLI's ``serve`` calls it via uvicorn.
    ``auth_store`` is constructed from ``cfg.auth`` if not supplied — tests
    inject one with a fake clock when they need to control TTLs. ``history_store``
    is likewise injectable so route tests can seed an isolated durable timeline.
    ``share_policy_store`` is likewise injectable so tests do not write user state.
    ``notification_broker`` is built from ``cfg.notifications`` if not supplied —
    tests inject one with a capturing channel to assert the edge-trigger wiring.
    ``status_publisher`` is built from ``cfg.issueops`` if not supplied (``None``
    when issue-ops is disabled) — tests inject a capturing one to assert the
    lifespan bind/close wiring, mirroring ``notification_broker``.
    ``slack_bridge`` is built from ``cfg.slack`` if not supplied (``None`` when
    Slack is off or has no tokens) and started in the lifespan the same way.
    ``release_checker`` defaults to a real GitHub-backed one — tests inject one
    with a fake fetcher so ``/whoami`` never touches the network.
    ``loaded_source`` defaults to a snapshot of the package taken here, at
    build time — tests inject one over a scratch tree to move its files.

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
    # The owner registry is built BEFORE the registry so every manager the
    # daemon mints can steer a native workspace's owner in-process (see
    # `OwnerSteerClient`); the loop it needs is bound at lifespan start.
    configure_transcript_caches(cfg.transcript_cache)
    native_owners = NativeOwnerRegistry()
    native_steer = OwnerSteerClient(native_owners)
    registry = RepoRegistry(
        cfg=cfg,
        store=store,
        config_loader=load_config,
        on_project_registered=ProjectInfra.registration_hook(cfg),
        native_steer=native_steer,
    )
    # A resolver has no cache: a panel destination is a live compose membership
    # fact, so carrying an old IP past a container restart would be a security
    # claim the daemon can no longer prove.
    panel_resolver = PanelResolver()
    activity_service = ActivityService(registry=registry)
    usage_service = _default_usage_service(cfg=cfg, registry=registry)
    # Host-wide session catalog, request-scoped behind a short TTL — never
    # polled, never per-row (see `_catalog.py`). Shared by the host-scoped
    # listing and its drill-in so the pair costs one scan.
    catalog = _CatalogMemo(SessionCatalog(registry))
    # The diagram gallery joins on the catalog's rows, so it shares the memo
    # rather than scanning sessions a second time.
    gallery = _GalleryMemo(DiagramGallery(registry), catalog)
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
    # ONE ticket-read cache for the daemon: the sticky publisher and the ticket
    # watcher read the same tickets, so sharing it is what holds each ticket to
    # one forge read per minute however many consumers ask.
    ticket_reads = TicketReads()
    activity_runtime = ActivityRuntime(activity_service, limits=cfg.activity_admission)
    activity_sources = ActivitySources(activity_runtime, store)
    runtime_sources = RuntimeSources(
        registry=registry,
        store=store,
        activity_runtime=activity_runtime,
        docker_bin=cfg.container.docker_bin,
    )
    pane_events = PaneEventHub()
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
            reads=ticket_reads,
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
    lifecycle = _LifecycleRunner(max_pending=cfg.lifecycle_max_pending)
    readiness = _ReadinessMemo()
    onboarding = ProjectOnboarding(cfg.projects_root)
    # Who wants activity deltas. The 2s poll waits on it, so an idle daemon that
    # nobody and nothing is listening to does no per-workspace git/transcript
    # work at all (see `_audience.py`).
    audience = _PollAudience()
    sse_hub = _SseHub(activity_service, audience=audience)
    catalog_sources = _CatalogSources(
        catalog,
        gallery,
        registry,
        lambda: sse_hub.publish(
            DashboardEvent(kind="catalog_changed", seq=activity_service.next_seq())
        ),
    )

    async def _projects_changed() -> None:
        # A project list change moves groups, never rows, so the held projection
        # is regrouped rather than the fleet re-read (a full reconcile measured
        # 57 s on a busy host). The snapshot frame is what reaches clients that
        # already hold one; the catalog and gallery index per known repo.
        snapshot, cursor = await asyncio.to_thread(activity_service.regroup_projects)
        sse_hub.resync(snapshot, cursor)
        catalog_sources.rescan()

    project_sources = _ProjectSources(registry, _projects_changed)
    if notification_broker is None:
        notification_broker = NotificationBroker.from_config(cfg.notifications)
    # Slack mentions in, agent replies out. Built from the daemon-wide config: one
    # Slack app serves every project, and a mention picks its repo by channel.
    slack_bridge = slack_bridge or SlackBridge.from_config(cfg.slack, registry=registry)
    # The watch scheduler is NOT an activity-bus subscriber: a watch is woken by
    # its own deadline rather than by anything a workspace does, so it owns a
    # heap instead of a subscription. It is constructed here for the lifespan's
    # sake and delivers through the same MailboxDelivery the mailbox routes use,
    # so a callback reaches every session kind without a second transport.
    # The CI watcher resolves its provider from the DAEMON-wide ticket config
    # rather than a repo's cascade: a watch names its forge, owner and repo
    # explicitly, so there is no repo in hand to resolve a cascade against at
    # probe time — and the credential a forge read needs is the same one at
    # either scope.
    trigger_watcher = TriggerWatcher(
        _trigger_evidence(registry, activity_service), render=_trigger_text(registry)
    )
    # The fleet digest reads the MAINTAINED projection, unlike the trigger
    # evidence above. No `audience.join()` keeps it fresh, because none would:
    # the projection is refreshed by hook, filesystem and runtime edges, and
    # nothing awaits the audience any more.
    fleet_digest = FleetDigest(
        snapshot=activity_service.snapshot_with_cursor, config=cfg.fleet_digest
    )
    # The daemon's own spans (reaper actions, freezes, request audits) share one
    # exporter behind a durable outbox, so a crash or a dead collector loses
    # nothing and what a previous run left is sent before anything new.
    inner_sink = build_span_sink(cfg.telemetry)
    daemon_sink = OutboxSink(inner_sink) if inner_sink is not None else None
    # Host-wide leak reconciliation; one span per action when telemetry is on.
    reaper = Reaper(
        collectors=(TmuxCollector(), ProcessCollector(), FilesCollector()),
        workspaces=store,
        store=ReaperStore(),
        sink=daemon_sink,
    )
    watch_scheduler = WatchScheduler(
        log=WatchLog(),
        watchers=WatcherRegistry(
            [
                TimerWatcher(),
                CiWatcher(TicketProviderRegistry(cfg.tickets)),
                # A command runs in the workspace that registered it, so the
                # watcher is handed a lookup rather than a manager — it must not
                # import the engine's orchestration to answer where to run. A
                # workspace that has since been killed resolves to None, which
                # the watcher settles honestly rather than retrying forever.
                CommandWatcher(
                    _workspace_lookup(registry),
                    WorkspaceRuntimes(
                        cfg, cli=DevcontainerCli, remote_runner=native_steer.run_in_worktree
                    ).for_state,
                ),
                # A ticket is named the way its workspace names it, so the
                # provider comes from THAT workspace's repo cascade.
                TicketWatcher(_ticket_provider_lookup(registry), ticket_reads),
                trigger_watcher,
                ReaperWatcher(reaper, dry_run=cfg.reaper.dry_run),
                _idle_watcher(registry, activity_service, lifecycle),
            ]
        ),
        deliver=MailboxWatchCourier(MailboxDelivery(registry)),
    )
    # Standing watches follow each running workspace: one per attached ticket,
    # plus one trigger watch unless triggers are switched off host-wide. Which
    # rules apply is each workspace's own repo cascade, read at every check.
    standing_watches = StandingWatches(
        scheduler=watch_scheduler,
        workspaces=_workspace_lookup(registry),
        trigger_every=(
            timedelta(seconds=cfg.triggers.check_seconds) if cfg.triggers.enabled else None
        ),
    )
    trigger_dispatch = TriggerDispatch(
        scheduler=watch_scheduler,
        watcher=trigger_watcher,
        fleet=_trigger_fleet(registry),
    )
    # The daemon is the long-lived reader of every create form, so it is where
    # the operator's catalog freshness applies; a one-shot CLI keeps the default.
    ModelProbeMemo.ttl_seconds = cfg.models.catalog_ttl_seconds
    if release_checker is None:
        release_checker = ReleaseChecker()
    # Taken once, as the process boots: the code this daemon will run for its
    # whole life, against which every later edit on disk is compared.
    if loaded_source is None:
        loaded_source = LoadedSource.capture()
    # One per daemon, like the release checker: the cache is what keeps a
    # whoami off Langfuse's API on every call.
    langfuse_projects = LangfuseProject()
    if auth_store is None:
        auth_store = SessionStore(
            session_ttl=timedelta(seconds=cfg.auth.session_ttl_seconds),
            pairing_ttl=timedelta(seconds=cfg.auth.pairing_ttl_seconds),
            pair_init_per_minute=cfg.auth.pair_init_per_minute,
            pair_poll_per_minute=cfg.auth.pair_poll_per_minute,
        )
    if history_store is None:
        # The workspace store's own, not a second instance: it is the WRITER
        # (names on save, the tombstone on delete) and this route is a reader of
        # the same file, so sharing one connection keeps "what was just written"
        # and "what is served" the same object rather than two views that agree
        # only because they resolved the same default path.
        history_store = store.history
    if share_policy_store is None:
        share_policy_store = SharePolicyStore()
    # One gate for every authenticated route: a person's OIDC ID token, or a Grove
    # bearer (a person's, or this host's paired user, which a federation refuses).
    oidc = OidcVerifier(cfg.auth.oidc)
    require_principal = make_require_principal(
        auth_store=auth_store,
        enabled=cfg.auth.enabled,
        oidc=oidc,
        federated=cfg.federation.enabled,
    )
    auth_dep = [Depends(require_principal)]
    live_hub = LiveHub(native_owners)
    mailbox_router = MailboxRouter(
        owners=native_owners,
        registry=registry,
        store=store,
        auth_dep=require_principal,
        lifecycle=lifecycle,
        live=live_hub,
        # A remote owner's spool drop is the same edge a hook ingest POST is.
        on_spooled=activity_runtime.hook,
    )
    # The batch's durable replay record, so a retried id after a restart replays.
    delegation_journal = DelegationJournal()
    fleet_digest_router = FleetDigestRouter(
        digest=fleet_digest,
        # Each recipient's send holds that recipient's lifecycle lock, exactly as
        # the single send does, and never two at once.
        batch=MailboxBatch(
            MailboxDelivery(registry), hold=lifecycle.hold, journal=delegation_journal
        ),
        auth_dep=require_principal,
    )
    speech_router = SpeechRouter(OpenAISpeech(cfg.speech), auth_dep=require_principal)
    federation = Federation.build(
        cfg.federation,
        auth_dep=require_principal,
        workspaces=lambda: [state.id for state in store.load_all()],
        projects=registry.known_identities,
        runtimes=cfg.resident_runtimes,
    )
    mailbox_socket = (
        MailboxSocket(Path(path), mailbox_router.router())
        if (path := os.environ.get("GROVE_MAILBOX_SOCKET"))
        else None
    )
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
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: PLR0912, PLR0915
        # Statement count grows linearly with subscriber/owner count (bind on
        # entry, close in `finally`) — the same justification `build_app`
        # itself already carries the identical suppression for.
        startup = _StartupTimer()
        app.state.registry = registry
        app.state.auth_store = auth_store
        app.state.share_policy_store = share_policy_store
        app.state.activity = activity_service
        app.state.usage = usage_service
        app.state.workspace_history = history_store
        app.state.sse_hub = sse_hub
        app.state.native_owners = native_owners
        app.state.mailbox_router = mailbox_router
        if mailbox_socket is not None:
            with startup.step("mailbox socket"):
                await mailbox_socket.start()
            app.state.mailbox_socket = mailbox_socket
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
        loop = asyncio.get_running_loop()
        sse_hub.start(loop)
        native_steer.bind(loop)
        lifecycle.bind(loop)
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
        # Same bargain as the broker: a Slack thread is a consumer with no
        # dashboard open, so the bus keeps polling while Slack is connected. The
        # connect is a network handshake, so it runs off the loop; a failure is
        # logged and the daemon starts without Slack.
        if slack_bridge is not None:
            await asyncio.to_thread(slack_bridge.start, activity_service.subscribe)
            audience.join()
        # A create, kill or provision a SIGKILL interrupted left a record that
        # describes work nobody is doing; finish it before any surface reads it.
        with startup.step("resolve interrupted"):
            await asyncio.to_thread(registry.resolve_interrupted)
        # Re-arms every durable watch and begins waiting. A restart must not
        # lose a callback an agent has already halted waiting for.
        with startup.step("watch scheduler"):
            await watch_scheduler.start()
        app.state.watch_scheduler = watch_scheduler
        # Ticket watches follow the activity bus (attach, detach, pause, kill,
        # from any process); the start-up pass repairs whatever changed while
        # the daemon was down. No `audience.join()`: these edges arrive by
        # invalidation, not by the fleet poll. Off the loop: store + log reads.
        standing_watches.bind(activity_service.subscribe)
        with startup.step("standing watches"):
            await asyncio.to_thread(
                standing_watches.reconcile_all,
                [state.id for state in registry.workspace_states()],
            )

        # The standing watch repeats this pass; disabled cancels a watch a past
        # run left. The first scan starts after readiness below: it can touch a
        # large durable ledger and never earns a dead listener.
        async def _startup_reaper() -> None:
            began = time.monotonic()
            try:
                async with asyncio.timeout(_STARTUP_REAPER_TIMEOUT_SECONDS):
                    await asyncio.to_thread(reaper.run, apply=not cfg.reaper.dry_run)
            except asyncio.CancelledError:
                logger.info("startup reaper pass cancelled after {:.2f}s", time.monotonic() - began)
                raise
            except TimeoutError:
                logger.warning(
                    "startup reaper pass timed out after {}s", _STARTUP_REAPER_TIMEOUT_SECONDS
                )
            except Exception as exc:
                logger.warning(
                    "startup reaper pass failed after {:.2f}s: {}", time.monotonic() - began, exc
                )
            else:
                logger.warning("startup reaper pass finished in {:.2f}s", time.monotonic() - began)

        # One host-wide idle check per tenth of the cooldown, so a workspace is
        # frozen within ~10% of the moment it crossed it, never more often than
        # the scheduler's floor allows.
        with startup.step("idle watch"):
            await asyncio.to_thread(
                IdleWatcher.ensure,
                watch_scheduler,
                every=max(MIN_INTERVAL, timedelta(seconds=cfg.idle.cooldown_seconds) / 10),
            )
        freeze_spans = FreezeSpans(daemon_sink)

        def _span_freezes(_root: Path, manager: WorkspaceManager) -> None:
            manager.subscribe(
                lambda event: freeze_spans.record(event.kind, event.workspace_id, datetime.now(UTC))
            )

        registry.subscribe_managers(_span_freezes)
        reaper_every = (
            timedelta(seconds=cfg.reaper.interval_seconds) if cfg.reaper.enabled else None
        )
        with startup.step("reaper watch"):
            await asyncio.to_thread(ReaperWatcher.ensure, watch_scheduler, every=reaper_every)
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
            assignee_poller.bind(registry.subscribe_managers)
            app.state.assignee_poller = assignee_poller
        # THE PROJECTION IS AWAITED; THE WATCHERS ARE NOT. `activity_runtime`
        # bootstraps the state every read answers from, so serving before it is
        # ready would answer an empty fleet — the one thing a maintained
        # projection must never do. The two file-event groups are pure
        # OBSERVERS: nothing serving a request needs them armed, and arming
        # them is filesystem discovery across every repo on the host plus a
        # watcher-readiness wait (measured 9.1s and 6.2s here), all of it in
        # front of `/healthz`. Blocking readiness on them made daemon startup
        # O(host), so the SDK's spawn budget became a bet on fleet size.
        #
        # They still start exactly once, and a failure is still surfaced —
        # `_source_startup` logs it rather than letting the task die silently,
        # which is the cost of moving work off the readiness path.
        watchers: asyncio.Task[None] | None = None
        startup_reaper: asyncio.Task[None] | None = None
        try:
            with startup.step("activity runtime"):
                await activity_runtime.start()
            with startup.step("runtime sources"):
                await runtime_sources.start()
            watchers = asyncio.create_task(
                # The one-file project watch first: the other two take many
                # seconds to arm, and `add-project` must not wait on them.
                _source_startup(project_sources, activity_sources, catalog_sources),
                name="grove-source-startup",
            )
            app.state.activity_runtime = activity_runtime
            if federation is not None:
                federation.start()
                app.state.federation = federation
            startup.report()
            if cfg.reaper.enabled:
                startup_reaper = asyncio.create_task(_startup_reaper(), name="grove-startup-reaper")
            yield
        finally:
            if startup_reaper is not None:
                startup_reaper.cancel()
                await asyncio.gather(startup_reaper, return_exceptions=True)
            if federation is not None:
                await _aclose_best_effort("federation", federation.close)
            if watchers is not None:
                watchers.cancel()
                await asyncio.gather(watchers, return_exceptions=True)
            await _aclose_best_effort("project sources", project_sources.close)
            await _aclose_best_effort("catalog sources", catalog_sources.close)
            await _aclose_best_effort("pane events", pane_events.aclose)
            await _aclose_best_effort("activity sources", activity_sources.close)
            await _aclose_best_effort("runtime sources", runtime_sources.close)
            await _aclose_best_effort("activity runtime", activity_runtime.close)
            _close_best_effort("standing watches", standing_watches.close)
            _close_best_effort("watch scheduler", watch_scheduler.close)
            if notification_broker is not None:
                _close_best_effort("notification broker", notification_broker.close)
            if slack_bridge is not None:
                _close_best_effort("slack bridge", slack_bridge.close)
            if status_publisher is not None:
                _close_best_effort("status publisher", status_publisher.close)
            if trace_forwarder is not None:
                _close_best_effort("trace forwarder", trace_forwarder.close)
            if assignee_poller is not None:
                _close_best_effort("assignee poller", assignee_poller.close)
            if otlp_ingest is not None:
                await _aclose_best_effort("otlp ingest", otlp_ingest.aclose)
            await _aclose_best_effort("speech router", speech_router.aclose)
            await _aclose_best_effort("mailbox router", mailbox_router.aclose)
            if mailbox_socket is not None:
                await _aclose_best_effort("mailbox socket", mailbox_socket.close)
            _close_best_effort("session catalog", catalog.close)
            _close_best_effort("SSE hub", sse_hub.stop)
            _close_best_effort("activity service", activity_service.close)
            _close_best_effort("workspace history", history_store.close)
            _close_best_effort("delegation journal", delegation_journal.close)
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

    # A JSON transcript is mostly ASCII prose and code, so gzip is the single
    # cheapest byte reduction on the wire. `minimum_size=1024` skips framing
    # overhead on small bodies where compression would net-lose. Starlette's
    # default `DEFAULT_EXCLUDED_CONTENT_TYPES` already excludes
    # `text/event-stream` — load-bearing, not incidental: a proxy that buffers a
    # compressed stream before forwarding it breaks the live dashboard, so
    # `/events` and the pane stream must never be gzipped. The activity endpoint
    # test mutation-tests this exclusion by asserting it rather than reading it.
    # The sentry test attaches the real middleware before an SSE fixture, so its
    # result cannot pass after this registration is deleted.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    # Outermost of the daemon's own layers: it stamps the request id and trace
    # context every layer below reads, the federation proxy included.
    app.add_middleware(RequestAudit, sink=daemon_sink, node=node_name())
    Federation.mount(
        federation,
        app,
        session_store=auth_store,
        authenticate_caller=require_principal,
        resolve_create=create_resolver(registry, cfg),
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
    app.include_router(
        build_auth_router(
            auth_store=auth_store,
            require_principal=require_principal,
            oidc=oidc,
            federated=cfg.federation.enabled,
        )
    )
    app.include_router(mailbox_router.router())
    app.include_router(fleet_digest_router.router())
    app.include_router(speech_router.router())
    app.include_router(
        SidebarPinRouter(
            pins=sidebar_pin_store or SidebarPinStore(),
            workspaces=store,
            auth_dep=require_principal,
        ).router()
    )
    app.include_router(
        WatchRouter(
            scheduler=watch_scheduler, dispatch=trigger_dispatch, auth_dep=require_principal
        ).router()
    )

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
            WorkCapacityExceeded: (503, "work_capacity_exceeded"),
            ArtifactInvalid: (422, "artifact_invalid"),
            ArtifactUnavailable: (404, "artifact_unavailable"),
            DiagramConflict: (409, "diagram_conflict"),
            DiagramUnavailable: (404, "diagram_unavailable"),
            BranchConflict: (409, "branch_conflict"),
            BranchAlreadyCheckedOut: (409, "branch_already_checked_out"),
            BranchNotFound: (404, "branch_not_found"),
            BranchError: (409, "branch_error"),
            WorkspaceNotFound: (404, "workspace_not_found"),
            ProjectNotOnNode: (404, "project_not_on_node"),
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
            # A `/grove:<name>` no config layer declares: the request named a
            # resource that does not exist, the same shape as an unknown workspace.
            MacroNotFound: (404, "macro_not_found"),
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
            # A create named a reasoning effort the agent cannot launch with.
            # 422 for the same reason: well-formed, invalid for this agent.
            EffortNotSupported: (422, "effort_not_supported"),
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
            # A watch whose subject this daemon can never observe (a forge it
            # has not enabled, or cannot authenticate to). 422 like the
            # capability refusals above: the request is well-formed, and no
            # retry succeeds until the daemon's configuration changes.
            WatchUnobservable: (422, "watch_unobservable"),
            # An add-project target Grove will not write (exists, not one path
            # segment, not a repository). Refused before any side effect, and the
            # fix is a different value in the same form, so 422.
            ProjectTargetRefused: (422, "project_target_refused"),
            # A repo whose `.grove/config.json` will not parse. 500 and
            # not 4xx: the request was fine, the server's own state is not, and
            # no client retry fixes it — but it is NAMED, because the message
            # carries the file and the parse position an operator needs.
            ConfigError: (500, "config_error"),
        }
        if isinstance(exc, InvalidLaunch):
            return HTTPException(
                status_code=422,
                detail={"error": exc.error, "message": str(exc), "field": exc.field},
            )
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

    def _mint_panel_token(ws_id: str, name: str) -> str:
        """A short-lived opaque credential naming exactly one workspace panel.

        The credential rides in the PATH rather than a header because of what
        has to carry it. A panel is rendered in an ``<iframe>`` and then
        upgraded to a WebSocket by noVNC, and neither of those can set an
        ``Authorization`` header — the browser builds both requests itself. The
        webapp's bearer token is also deliberately unreachable from page
        scripts (it lives server-side behind the BFF, keyed by an opaque
        cookie), so there is nothing for a script to attach even if it could.

        Stateless and HMAC-signed against a key minted once per process. That
        buys three things over a stored token: no persistence to grow or prune,
        no lookup on the hot path of every VNC frame's HTTP sibling, and
        automatic invalidation of every outstanding panel URL when the daemon
        restarts. The last is a feature — the webapp refetches ``/panels`` when
        a workspace page mounts, so the cost of a restart is one extra request
        and the benefit is that a leaked URL cannot outlive the process.

        The payload binds BOTH the workspace and the panel name, so a token
        minted for one panel cannot be replayed against another, and carries its
        own expiry so the signature alone settles freshness.
        """
        expires = int((datetime.now(UTC) + _PANEL_TOKEN_TTL).timestamp())
        payload = f"{ws_id}\x00{name}\x00{expires}".encode()
        body = base64.urlsafe_b64encode(payload).rstrip(b"=")
        digest = hmac.new(_panel_token_key, body, hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(digest).rstrip(b"=")
        return f"{body.decode()}.{signature.decode()}"

    def _read_panel_token(token: str) -> tuple[str, str]:
        """Recover ``(ws_id, panel_name)`` from a token, or refuse.

        Every rejection raises the SAME 404 the resolver raises for a panel that
        does not exist. A distinct 401/403 here would turn this endpoint into an
        oracle: unauthenticated callers could probe which workspace ids are real
        by watching the status code change. Signature is compared before the
        payload is trusted, and with ``compare_digest`` so the comparison does
        not leak its progress through timing.
        """
        try:
            body, _, signature = token.partition(".")
            if not body or not signature:
                raise ValueError("malformed token")
            expected = hmac.new(_panel_token_key, body.encode(), hashlib.sha256).digest()
            given = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, given):
                raise ValueError("bad signature")
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            ws_id, name, expires = payload.decode().split("\x00")
            if datetime.now(UTC).timestamp() > int(expires):
                raise ValueError("expired")
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "panel_not_found", "message": "no such panel"},
            ) from exc
        return ws_id, name

    def _panel_url(ws_id: str, name: str, path: str) -> str:
        """Build the browser-facing URL without exposing the internal target.

        Relative on purpose, and NOT under the webapp's ``/api/grove`` BFF. The
        BFF is a Next route handler, which can only take a Request and return a
        Response — it has no upgrade path, so a panel proxied through it would
        serve noVNC's first page and then never open its socket. ``/panel/…``
        is served by this daemon directly and reaches the browser on the
        webapp's own origin through the reverse proxy, which keeps the iframe
        same-origin and free of mixed content.

        The service, port and container IP never cross this boundary: the
        browser receives only a signed token, and the daemon resolves the
        constrained Docker destination again on every request.
        """
        return f"/panel/{quote(_mint_panel_token(ws_id, name), safe='.')}{path}"

    async def _resolve_panel(ws_id: str, name: str) -> tuple[PanelTarget, str]:
        """Read and resolve one declared panel, with unavailable as a clean 404.

        Both manager reconciliation and Docker inspection block, so this keeps
        their work together in an executor.  A panel can disappear between a
        detail read and its iframe request as a compose service exits; resolving
        again is the honest answer, never a cached address pointed at a recycled
        container IP.
        """
        manager = _manager_for(ws_id)

        def _read() -> tuple[PanelTarget | None, str | None]:
            state = manager.get(ws_id)
            panel = next((item for item in manager.config.panels if item.name == name), None)
            if panel is None:
                return None, None
            return panel_resolver.resolve(state, panel), panel.path

        try:
            target, path = await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        if path is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "panel_not_found", "message": f"no panel named {name!r}"},
            )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "panel_unavailable",
                    "message": f"panel {name!r} is not available in this workspace",
                },
            )
        return target, path

    async def _resolved_panel_views(ws_id: str) -> list[WorkspacePanelView]:
        """List only panels whose declared compose service resolves right now.

        Omission rather than a nullable target keeps the client contract useful:
        every row it receives is immediately iframeable.  The direct proxy route
        still distinguishes an undeclared panel from one that was declared but
        became unavailable after the detail response.
        """
        manager = _manager_for(ws_id)

        def _read() -> list[WorkspacePanelView]:
            state = manager.get(ws_id)
            return [
                WorkspacePanelView(
                    name=panel.name,
                    title=panel.title,
                    url=_panel_url(ws_id, panel.name, panel.path),
                )
                for panel in manager.config.panels
                if panel_resolver.resolve(state, panel) is not None
            ]

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    def _panel_target_url(
        target: PanelTarget, rest: str, query: str, *, websocket: bool = False
    ) -> str:
        """Build a service URL from a resolved address and the caller's suffix."""
        scheme = "ws" if websocket else "http"
        suffix = quote(rest, safe="/%")
        url = f"{scheme}://{target.host}:{target.port}/{suffix}"
        return f"{url}?{query}" if query else url

    def _panel_headers(headers: Any, *, websocket: bool = False) -> list[tuple[str, str]]:
        """Keep end-to-end headers while withholding hop and daemon-auth headers.

        A panel is an application in the workspace, not another daemon client:
        forwarding the browser's bearer would disclose host-wide Grove authority
        to code selected by the repository.  Hop-by-hop headers belong to each
        individual connection, especially ``Upgrade`` which the WebSocket client
        builds itself from its negotiated upstream connection.
        """
        omitted = {
            "authorization",
            "connection",
            "host",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        }
        if websocket:
            # The upstream client performs its OWN handshake and mints its own
            # key, version and extension offer. Forwarding the browser's copies
            # would put two of each on the wire, which the upstream library
            # rejects outright — the symptom is a bare 403 on the browser's
            # upgrade, with no handler ever reached to explain it. The
            # subprotocol is renegotiated explicitly by the caller instead.
            omitted |= {
                "sec-websocket-key",
                "sec-websocket-version",
                "sec-websocket-extensions",
                "sec-websocket-protocol",
                "sec-websocket-accept",
            }
        # Starlette's `Headers` is not a `QueryParams`: it exposes `raw` (the
        # decoded byte pairs, repeats preserved) but no `multi_items()`. Reading
        # `raw` also keeps repeated headers like `Cookie` intact, which
        # `dict(headers)` would silently collapse to the last value.
        return [
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in headers.raw
            if key.decode("latin-1").lower() not in omitted
        ]

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

    @app.get("/public/{token}/events")
    async def public_events(
        token: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """Workspace-scoped invalidations with no private fleet payload."""
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
        except ShareNotFound as exc:
            raise _share_404(exc) from exc

        def authorize() -> WorkspaceState:
            return _share_reader(token, x_grove_share_passcode).state

        source = ScopedWorkspaceEvents(activity_service, reader.state, authorize)
        return StreamingResponse(
            source.events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/public/{token}/turns", response_model=SessionDetailView | None)
    async def public_turns(
        token: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
        last: Annotated[int | None, Query(ge=1)] = None,
        after_turn: Annotated[int | None, Query(ge=0)] = None,
        before_turn: Annotated[int | None, Query(ge=0)] = None,
    ) -> SessionDetailView | None:
        """The shared workspace's transcript, windowed — the LIVE half.

        Takes no session id: the token names a workspace and the daemon picks
        the session, so an unauthenticated caller holds no coordinate it could
        tamper with. ``after_turn``, ``before_turn`` and ``last`` mean exactly
        what they mean on the authenticated route (one ``turn_window``, shared),
        which is what lets the browser reuse its whole cursor-merge path
        unchanged. Settled tool bodies are withheld here too, and the public
        drill-in below serves them; there is deliberately no ``bodies=all``
        escape on this namespace — the whole-payload read exists for a CLI or
        script holding a session, and a share link is neither.

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
        if before_turn is not None and after_turn is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_turn_window",
                    "message": "`before_turn` and `after_turn` are mutually exclusive",
                },
            )
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
            return await asyncio.to_thread(
                reader.turns, last=last, after_turn=after_turn, before_turn=before_turn
            )
        except ShareNotFound as exc:
            raise _share_404(exc) from exc
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get("/public/{token}/tools/{tool_use_id}", response_model=ToolCallView)
    async def public_tool(
        token: str,
        tool_use_id: str,
        x_grove_share_passcode: Annotated[str | None, Header()] = None,
    ) -> ToolCallView:
        """One tool call's complete body, for the session the TOKEN names.

        The public sibling of the authenticated drill-in, and it takes no
        workspace or session coordinate for the same reason ``/turns`` does not:
        the reader holds a token, the daemon picks the session, so there is
        nothing here an anonymous caller could point at another workspace. A
        tool id belonging to any other session is the flat share 404 every
        public failure answers with — never a hint that the id exists elsewhere.
        """
        try:
            reader = await asyncio.to_thread(_share_reader, token, x_grove_share_passcode)
            call = await asyncio.to_thread(reader.tool_call, tool_use_id)
            if call is None:
                raise ShareNotFound("no such tool call on this shared transcript")
            return call
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
        restart_required = await asyncio.to_thread(loaded_source.changed)
        langfuse_host = await asyncio.to_thread(_resolve_langfuse_host, cfg)
        langfuse_project = await asyncio.to_thread(
            _resolve_langfuse_project, cfg, langfuse_projects
        )
        return _build_whoami(
            app.state.started_at,
            release,
            restart_required=restart_required,
            langfuse_host=langfuse_host,
            langfuse_project_id=langfuse_project,
            feedback_reasons=list(cfg.telemetry.feedback_reasons) if langfuse_host else [],
            positive_feedback_reasons=(
                list(cfg.telemetry.positive_feedback_reasons) if langfuse_host else []
            ),
            speech=speech_router.available,
            federation=cfg.federation.status,
        )

    @app.get("/activity", response_model=DashboardSnapshotView, dependencies=auth_dep)
    async def activity() -> DashboardSnapshotView:
        """One-shot cross-project dashboard snapshot.

        ``snapshot()`` does blocking git/tmux I/O, so it runs in the executor to
        keep the loop responsive under concurrent requests.
        """
        snap = activity_service.snapshot()
        return DashboardSnapshotView.from_snapshot(snap)

    @app.post(
        HOOK_INGEST_ROUTE,
        status_code=204,
        dependencies=[Depends(require_hook_token)],
    )
    async def ingest_agent_hook(body: _HookIngestBody) -> None:
        """Admit a session invalidation without waiting for projection work."""
        result = activity_runtime.hook(body.session_id)
        if result not in (Admission.ACCEPTED, Admission.COALESCED):
            raise HTTPException(status_code=503, detail={"error": "activity_intake_unavailable"})

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
                    snap, cursor = activity_service.snapshot_with_cursor()
                    snapshot_event = DashboardEvent.snapshot_event(snap, seq=cursor)
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
                    if event.seq <= last_seq:
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
        request: Request,
        repo: Annotated[Path, Query()] | None = None,
        ticket: Annotated[str, Query()] | None = None,
        creator: Annotated[Literal["me", "all"], Query()] = "me",
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
        ``creator`` defaults to ``me``, the caller's own workspaces; ``all``
        shows everyone's. It narrows a view and grants nothing: every signed-in
        person may read every workspace.
        """
        mine = principal_of(request).subject if creator == "me" else None
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
            return out if mine is None else [view for view in out if view.created_by == mine]

        # `list()` reconciles every workspace against live tmux + the
        # filesystem — measured ~500 ms for 23 workspaces — and this is the
        # most-polled route in the product, so it belongs in the executor with
        # the other scans rather than stalling the loop on every dashboard tick.
        return await asyncio.to_thread(_scan)

    @app.post("/workspaces", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def create_workspace(req: CreateWorkspaceRequest, request: Request) -> WorkspaceStateView:
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
        if req.repo_root is None and req.project is not None:
            try:
                root = await asyncio.to_thread(registry.root_for, req.project)
            except GroveError as exc:
                raise _grove_error_to_http(exc) from exc
            req = req.model_copy(update={"repo_root": root})
        if req.repo_root is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "repo_root_required",
                    "message": "POST /workspaces requires repo_root or project in the request body",
                },
            )
        mgr = registry.get(req.repo_root)
        # The creator is whoever the daemon admitted, never a field the request
        # could carry: a client cannot create a workspace in somebody else's name.
        creator = principal_of(request).subject
        try:
            state = await lifecycle.run(None, partial(mgr.create, created_by=creator), req)
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

    @app.post("/issue-ops/assignments", status_code=202, dependencies=auth_dep)
    async def ingest_assignment_event(event: AssignmentTicketEvent) -> dict[str, bool]:
        """Admit an authenticated normalized tracker assignment edge."""
        if assignee_poller is None:
            raise HTTPException(status_code=409, detail={"error": "assignment_disabled"})
        accepted = assignee_poller.handle_ticket_event(event)
        if not accepted:
            raise HTTPException(status_code=409, detail={"error": "assignment_not_admitted"})
        return {"accepted": True}

    @app.get("/workspaces/{ws_id}", response_model=WorkspaceStateView, dependencies=auth_dep)
    async def get_workspace(ws_id: str) -> WorkspaceStateView:
        """One workspace's reconciled state — in the executor, since `get`
        reconciles against live tmux and the filesystem like `list`."""
        mgr = _manager_for(ws_id)
        try:
            state = await asyncio.to_thread(mgr.get, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        view = WorkspaceStateView.from_state(state)
        return view.model_copy(update={"panels": await _resolved_panel_views(ws_id)})

    @app.get(
        "/workspaces/{ws_id}/panels",
        response_model=list[WorkspacePanelView],
        dependencies=auth_dep,
    )
    async def list_workspace_panels(ws_id: str) -> list[WorkspacePanelView]:
        """The currently iframeable subset of this workspace's panel declarations.

        This is deliberately a separate list route as well as the ``panels``
        field on workspace detail: a panel service can start after the detail
        query, and refreshing this small list avoids making the client treat a
        full workspace state read as a liveness probe.
        """
        return await _resolved_panel_views(ws_id)

    @app.api_route(
        "/panel/{token}/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        response_class=Response,
        # Kept out of the schema: this is a byte passthrough, not an API. Every
        # method shares one handler, so FastAPI would mint colliding operation
        # ids and the webapp's generated client would grow seven meaningless
        # methods whose request and response shapes belong to whatever service
        # the panel points at, not to Grove.
        include_in_schema=False,
    )
    async def proxy_workspace_panel(token: str, rest: str, request: Request) -> Response:
        """Proxy one HTTP request to a resolved service in this workspace's stack.

        Carries no ``auth_dep``: the signed token in the path IS the credential,
        because a browser cannot put a header on an ``<iframe>`` request. The
        token names the workspace and panel, so this handler still cannot be
        pointed anywhere the minting route would not have allowed.

        Target lookup happens before every request rather than trusting a URL
        previously returned in workspace detail.  That repetition is the
        containment check: Docker membership is live, while a composed service
        can be restarted and receive a different IP at any time.  ``httpx`` is
        used as a transparent HTTP transport only; it has no proxy configuration
        or redirect following, so the repository cannot turn a local panel into
        a route to a second origin.
        """
        ws_id, name = _read_panel_token(token)
        target, _path = await _resolve_panel(ws_id, name)
        content = await request.body()
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=None) as client:
                upstream = await client.request(
                    request.method,
                    _panel_target_url(target, rest, request.url.query),
                    content=content,
                    headers=_panel_headers(request.headers),
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "panel_unreachable",
                    "message": f"panel {name!r} could not be reached: {exc}",
                },
            ) from exc
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={
                key: value
                for key, value in upstream.headers.items()
                if key.lower()
                not in {
                    "connection",
                    "keep-alive",
                    "proxy-authenticate",
                    "proxy-authorization",
                    "te",
                    "trailer",
                    "transfer-encoding",
                    "upgrade",
                }
            },
        )

    @app.websocket("/panel/{token}/{rest:path}")
    async def proxy_workspace_panel_websocket(websocket: WebSocket, token: str, rest: str) -> None:
        """Bridge a panel's WebSocket upgrade through the same resolved target.

        noVNC upgrades its initial HTTP page into a WebSocket, so an HTTP-only
        proxy would render a convincing blank screen.  This is the reason the
        credential lives in the path at all: the browser constructs this
        handshake itself and cannot be made to attach a bearer header to it, so
        a header-authenticated route could never be reached from an iframe.
        The same signed token is verified here, and the live destination is
        resolved again rather than trusted from the HTTP leg.  Frames are copied
        in both directions without interpretation; the WebSocket protocol owns
        ping/pong and close framing, while a concurrent task group prevents one
        quiet side from blocking the other forever.
        """
        try:
            ws_id, name = _read_panel_token(token)
            target, _path = await _resolve_panel(ws_id, name)
        except HTTPException:
            await websocket.close(code=1011)
            return
        # The client's subprotocol offer has to be renegotiated rather than
        # forwarded: noVNC asks for ``binary``, and a bridge that accepted the
        # browser's offer without agreeing the same one upstream would hand each
        # side a different framing contract.
        offered = websocket.scope.get("subprotocols") or []
        try:
            async with websocket_connect(
                _panel_target_url(target, rest, websocket.url.query, websocket=True),
                additional_headers=_panel_headers(websocket.headers, websocket=True),
                subprotocols=list(offered) or None,
                max_size=None,
            ) as upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)

                async def client_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send(message["text"])

                async def upstream_to_client() -> None:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(client_to_upstream())
                    tasks.create_task(upstream_to_client())
        except (OSError, WebSocketException) as exc:
            logger.debug("panel websocket {} for {} failed: {}", name, ws_id, exc)
            with suppress(RuntimeError):
                await websocket.close(code=1011)

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

    @app.post(
        "/workspaces/{ws_id}/fork",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def fork_workspace(ws_id: str, request: Request) -> WorkspaceStateView:
        """Create a new workspace whose agent starts from a copy of this one's conversation.

        Returns the NEW workspace once it is provisioned, like ``POST /workspaces``.
        Unkeyed on the lifecycle runner for the same reason create is: the
        serializing id is minted by the call, and the source is only read.
        """
        mgr = _manager_for(ws_id)
        creator = principal_of(request).subject
        try:
            state = await lifecycle.run(None, partial(mgr.fork, created_by=creator), ws_id)
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
            await asyncio.to_thread(
                mgr.send_message, ws_id, body.text, attachments=body.attachments
            )
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/attachments",
        response_model=AttachmentView,
        dependencies=auth_dep,
    )
    async def add_workspace_attachment(ws_id: str, body: AttachmentUploadRequest) -> AttachmentView:
        """Store one file for the workspace and return the id a message names.

        The response's ``path`` is where the AGENT will find the file, already
        translated into its own namespace — shown to the human so the request
        is legible, never sent back by the client, which names the ``id``.

        Base64 rather than multipart, so the browser's JSON-only BFF proxy and
        this route need no second content type between them; the reasoning and
        its cost are on ``AttachmentUploadRequest``. Malformed base64 is 422
        ``invalid_attachment`` — a client bug, not a workspace state problem.
        The decode and the disk write are both off-loaded like every other
        blocking manager call.
        """
        mgr = _manager_for(ws_id)
        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_attachment", "message": f"content is not base64: {exc}"},
            ) from exc
        try:
            return await asyncio.to_thread(mgr.add_attachment, ws_id, body.name, data)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/keys", status_code=204, dependencies=auth_dep)
    async def send_workspace_keys(ws_id: str, body: SendKeysRequest) -> None:
        """Deliver one named key to the workspace's own live terminal.

        Acknowledgement means delivered, not cancelled or accepted by the app.
        The engine chooses the pane and tmux server; clients cannot name either.
        Remote or paneless agents refuse with 501 steering_unsupported.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.send_keys, ws_id, body.key)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/interrupt", status_code=204, dependencies=auth_dep)
    async def interrupt_workspace(ws_id: str) -> None:
        """Interrupt the workspace's agent where its provider supports it.

        Claude Code receives Escape in its agent pane, which drives its REPL
        abort controller; a pending permission prompt instead takes Escape's
        ``onAbort()`` path. Other tmux-hosted kinds retain the typed 501
        ``steering_unsupported`` refusal. The blocking pane write is off-loaded
        like ``/message`` so it cannot stall the daemon loop.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.interrupt, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post("/workspaces/{ws_id}/question-answer", status_code=204, dependencies=auth_dep)
    async def answer_question(ws_id: str, body: QuestionAnswerRequest) -> None:
        """Answer a pending question by dismissing it and restating the batch as text.

        A 204 means the on-screen prompt was cancelled and the rendered answers
        were delivered — the same dispatch semantics ``/message`` has, since the
        agent's actual response lands later on the transcript. Refusals ride the
        typed-error envelope: 404 ``workspace_not_found``, 409
        ``question_not_pending`` (stale/absent ``tool_use_id``) /
        ``workspace_state_error`` / ``pane_not_found``, 422
        ``question_answer_invalid`` (the plan doesn't fit the captured
        questions). The wire model rejects a structurally-malformed body (422)
        before the handler runs. This drives blocking tmux operations, so it is
        off-loaded like the other steer routes.
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

    @app.post("/workspaces/{ws_id}/controls/effort", status_code=204, dependencies=auth_dep)
    async def switch_workspace_effort(ws_id: str, body: _SwitchEffortBody) -> None:
        """Change the running native session's reasoning effort.

        Delivered over the provider's own control channel (Claude Code
        ``apply_flag_settings``, Codex ``thread/settings/update``) — 204 means
        the request reached the owner, not that the provider applied it.
        Refusals: 422 ``effort_not_supported`` (a level the kind does not list,
        or a workspace with no native channel), 409 ``pane_not_found`` (no
        connected owner), 501 ``steering_unsupported``.
        """
        mgr = _manager_for(ws_id)
        try:
            await asyncio.to_thread(mgr.switch_effort, ws_id, body.effort)
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
        "/workspaces/{ws_id}/diagram",
        response_model=DiagramDocumentView,
        dependencies=auth_dep,
    )
    async def read_workspace_diagram(
        ws_id: str, repo: Annotated[Path | None, Query()] = None
    ) -> DiagramDocumentView:
        """Read this workspace's acknowledged diagram document and revision.

        The workspace id identifies its configured manager. ``repo`` is an
        optional consistency selector for callers that already hold one; no
        editor draft crosses this boundary—only accepted bytes are returned.
        """
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await asyncio.to_thread(mgr.read_diagram, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/diagram",
        response_model=DiagramDocumentView,
        dependencies=auth_dep,
    )
    async def open_workspace_diagram(
        ws_id: str, body: DiagramOpenRequest, repo: Annotated[Path | None, Query()] = None
    ) -> DiagramDocumentView:
        """Open one existing workspace-relative diagram for managed editing."""
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await lifecycle.run(ws_id, mgr.open_diagram, ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.put(
        "/workspaces/{ws_id}/diagram",
        response_model=DiagramDocumentView,
        dependencies=auth_dep,
    )
    async def update_workspace_diagram(
        ws_id: str, body: DiagramUpdateRequest, repo: Annotated[Path | None, Query()] = None
    ) -> DiagramDocumentView:
        """Conditionally save a diagram using its active session and revision."""
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await lifecycle.run(ws_id, mgr.update_diagram, ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/diagram/preview",
        response_model=DiagramPreviewView,
        dependencies=auth_dep,
    )
    async def read_workspace_diagram_preview(
        ws_id: str, repo: Annotated[Path | None, Query()] = None
    ) -> DiagramPreviewView:
        """Fetch the browser-rendered first-page PNG for the current revision only."""
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await asyncio.to_thread(mgr.read_diagram_preview, ws_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/diagram/preview",
        response_model=DiagramPreviewView,
        dependencies=auth_dep,
    )
    async def save_workspace_diagram_preview(
        ws_id: str,
        body: DiagramPreviewUploadRequest,
        repo: Annotated[Path | None, Query()] = None,
    ) -> DiagramPreviewView:
        """Store a browser-rendered first-page PNG fenced to its saved revision."""
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await lifecycle.run(ws_id, mgr.save_diagram_preview, ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/diagram/stop",
        response_model=DiagramDocumentView,
        dependencies=auth_dep,
    )
    async def stop_workspace_diagram(
        ws_id: str, body: DiagramStopRequest, repo: Annotated[Path | None, Query()] = None
    ) -> DiagramDocumentView:
        """Fence a collaboration generation and retain its document read-only."""
        mgr = _manager_for(ws_id)
        if repo is not None and mgr.repo_root != _known_root(repo):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace {ws_id!r} in {repo}",
                },
            )
        try:
            return await lifecycle.run(ws_id, mgr.stop_diagram, ws_id, body)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

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
        "/workspaces/{ws_id}/activity",
        response_model=WorkspaceActivityView,
        dependencies=auth_dep,
    )
    async def workspace_activity(ws_id: str) -> WorkspaceActivityView:
        """One workspace's activity row — the per-workspace half of ``/activity``.

        A page about ONE workspace needs one session id, and reading it off the
        cross-project snapshot made it wait for every workspace on the host:
        measured on the reference host, two OFFLINE workspaces contributed
        22.4 s of a 40.6 s bootstrap that a transcript page sat behind before
        its first paint. This answers the same question at O(1) workspaces.

        The frame is byte-identical to that workspace's row in the snapshot —
        same ``WorkspaceActivityView``, built by the same engine seam — so a
        client can hold one shape whether it arrived here or on the stream, and
        the ``session_activity`` frames keep it current afterwards.

        ``workspace_row`` reconciles status and shells git/tmux, so it rides the
        executor like every other blocking manager read.
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
        row = await asyncio.to_thread(activity_service.workspace_row, state.repo_root, ws_id)
        if row is None:
            # Raced a kill between the store read and the row build. The engine
            # answers None rather than raising, so the 404 envelope is owned
            # here — the same shape `_state_for` would have produced.
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "workspace_not_found",
                    "message": f"no workspace with id {ws_id!r}",
                },
            )
        return WorkspaceActivityView.from_activity(row)

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

        state = await asyncio.to_thread(mgr.get, ws_id)
        if state.runtime is Runtime.REMOTE:
            return _sse_response(_remote_pane_frames(ws_id))
        pane = await asyncio.to_thread(mgr.pane_for, state)
        if pane is None:
            raise HTTPException(status_code=409, detail={"error": "pane_not_found"})

        async def _capture() -> PaneSnapshot:
            try:
                ansi, taken_at = await asyncio.to_thread(mgr.peek_pane, ws_id)
                return PaneSnapshot(ansi, taken_at)
            except GroveError:
                return PaneSnapshot(None, None)

        try:
            subscription = pane_events.subscribe(
                PaneKey(ws_id, pane.display),
                capture=_capture,
                source=lambda: TmuxControlPaneSource(target=pane.target, command=pane.command),
            )
        except PaneEventsUnavailable as exc:
            raise HTTPException(
                status_code=501, detail={"error": "pane_events_unavailable"}
            ) from exc
        streamer = _PaneStreamer(
            workspace_id=ws_id, subscription=subscription, next_seq=activity_service.next_seq
        )

        async def stream() -> AsyncIterator[str]:
            async for event in streamer.events():
                yield _sse_frame(event) if event is not None else ": keepalive\n\n"

        return _sse_response(stream())

    async def _remote_pane_frames(ws_id: str) -> AsyncIterator[str]:
        """A remote workspace's pane: the lines its worker posts while this stream is open.

        Opening it joins the workspace's live room, which is the watch: the
        worker starts posting its printed lines and the first batch is its
        whole log. Same frame shape and keepalive as a host pane's stream.
        """
        wake = live_hub.join_pane(ws_id)
        try:
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=_HEARTBEAT_INTERVAL_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                wake.clear()
                ansi, taken_at = live_hub.pane(ws_id)
                if ansi is None:
                    continue
                yield _sse_frame(
                    DashboardEvent.pane_event(
                        WorkspacePaneView.from_capture(ws_id, ansi, taken_at),
                        seq=activity_service.next_seq(),
                    )
                )
        finally:
            live_hub.leave_pane(ws_id, wake)

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
        The scan uses one bounded head read per transcript in the workspace's
        cwd; parse-derived fields come only from the durable cache. It still runs
        in the executor because filesystem discovery is blocking.

        ``candidates=true`` flips the scan to the UNGATED
        :meth:`SessionExplorer.candidates_for` — the remap-picker set,
        which keeps a session the adoption gate rejects (a dead-minted-pointer's
        pre-birth successor, a foreign session in a shared ROOT cwd) so a UI can
        offer it to pin via ``POST .../session``. The default gated view stays
        the workspace's own attributed history.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)
        try:
            if candidates:
                listings = await asyncio.to_thread(explorer.candidates_for, ws_id)
                listings = listings[:limit]
            else:
                listings = await asyncio.to_thread(explorer.for_workspace, ws_id, limit=limit)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return [SessionSummaryView.from_listing(ls) for ls in listings]

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
        before_turn: Annotated[int | None, Query(ge=0)] = None,
        bodies: Annotated[Literal["head", "all"], Query()] = "head",
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

        ``before_turn=<n>`` is the BACKWARD page — turns preceding ``n``,
        exclusive — and it is what makes "load earlier" transfer only the
        earlier turns instead of re-downloading the tail it already holds. It
        combines with ``last``, which is its page size, and refuses
        ``after_turn`` (422): a request cannot both resume forward and page
        back.

        ``bodies=head`` (the default) withholds the request/result of every
        SETTLED tool call outside the tail turn, marking each ``body:
        "available"`` for the drill-in below. Tool bodies were measured at 55.6%
        of a ``?last=40`` window while a historical tool call mounts collapsed
        and never puts its body in the DOM. It is a PROJECTION, not a cap —
        nothing is truncated and the response says what it withheld — and
        ``bodies=all`` opts a CLI, TUI or script back into the complete payload
        in one read.
        """
        if last is not None and after_turn is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_turn_window",
                    "message": "`last` and `after_turn` are mutually exclusive",
                },
            )
        if before_turn is not None and after_turn is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_turn_window",
                    "message": "`before_turn` and `after_turn` are mutually exclusive",
                },
            )
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _projected(detail: SessionDetailView) -> SessionDetailView:
            return detail if bodies == "all" else detail.withhold_settled_bodies()

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
                window = turn_window(
                    explorer.turns_for(listing),
                    last=last,
                    after_turn=after_turn,
                    before_turn=before_turn,
                )
                return _projected(
                    SessionDetailView.from_listing_turns(
                        listing,
                        window.turns,
                        total_turns=window.total,
                        first_turn_index=window.first_index,
                        incremental=window.incremental,
                    )
                )
            fallback = explorer.subagent_turns(ws_id, session_id)
            if fallback is not None:
                fleet_listing, turns = fallback
                window = turn_window(
                    turns, last=last, after_turn=after_turn, before_turn=before_turn
                )
                return _projected(
                    SessionDetailView.from_listing_turns(
                        fleet_listing,
                        window.turns,
                        total_turns=window.total,
                        first_turn_index=window.first_index,
                        incremental=window.incremental,
                    )
                )
            raise AgentSessionNotFound(
                f"no session {session_id!r} recorded for workspace {ws_id!r}"
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.post(
        "/workspaces/{ws_id}/sessions/{session_id}/feedback",
        status_code=204,
        dependencies=auth_dep,
    )
    async def workspace_session_feedback(
        ws_id: str, session_id: str, body: TurnFeedbackRequest
    ) -> None:
        """Record a human rating of one turn as Langfuse scores on that turn's trace.

        The trace is re-derived from the transcript (:func:`turn_trace_id`),
        never taken from the client. 409 ``feedback_unavailable`` when Langfuse
        is not configured — the same gate ``WhoamiView.feedback_reasons`` shows
        a client; 422 ``unknown_feedback_reason`` for a reason the config does
        not list; 404 ``turn_not_found`` for a turn not in this
        session; 502 ``feedback_not_recorded`` when Langfuse refuses the write
        or lacks the ``user-feedback`` / ``user-feedback-reason`` score configs
        (the message names which).
        """
        credentials = await asyncio.to_thread(_langfuse_credentials, cfg)
        if credentials is None:
            raise HTTPException(status_code=409, detail={"error": "feedback_unavailable"})
        catalogs: dict[Rating, tuple[str, ...]] = {
            "positive": cfg.telemetry.positive_feedback_reasons,
            "negative": cfg.telemetry.feedback_reasons,
        }
        unknown = [reason for reason in body.reasons if reason not in catalogs[body.rating]]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_feedback_reason", "reasons": unknown},
            )
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _trace_id() -> int | None:
            listing = next(
                (ls for ls in explorer.for_workspace(ws_id) if ls.summary.session_id == session_id),
                None,
            )
            if listing is None:
                raise AgentSessionNotFound(
                    f"no session {session_id!r} recorded for workspace {ws_id!r}"
                )
            return turn_trace_id(explorer.messages_for(listing), session_id, body.started_at)

        try:
            trace_id = await asyncio.to_thread(_trace_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        if trace_id is None:
            raise HTTPException(status_code=404, detail={"error": "turn_not_found"})
        feedback = TurnFeedback(
            trace_id=f"{trace_id:032x}",
            rating=body.rating,
            reasons=tuple(dict.fromkeys(body.reasons)),
            note=body.note.strip(),
        )
        try:
            await asyncio.to_thread(LangfuseScores(*credentials).record, feedback, catalogs)
        except (MissingScoreConfig, urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("feedback not recorded for trace {}: {}", feedback.trace_id, exc)
            raise HTTPException(
                status_code=502,
                detail={"error": "feedback_not_recorded", "message": str(exc)},
            ) from exc

    @app.get(
        "/workspaces/{ws_id}/sessions/{session_id}/tools/{tool_use_id}",
        response_model=ToolCallView,
        dependencies=auth_dep,
    )
    async def workspace_session_tool(ws_id: str, session_id: str, tool_use_id: str) -> ToolCallView:
        """ONE tool call's complete request and result — the drill-in member of
        the head+drill-in pairing ``/turns`` heads.

        The same shape ``/diff`` + ``?path=`` already has, applied to a
        transcript: the windowed turn list carries every call's identity and
        status while withholding a settled body it marks ``body: "available"``,
        and this serves that body WHOLE. Byte-for-byte what ``?bodies=all``
        carries for the same id, because both render the same
        ``ToolCallView.from_call`` over the same spine — a drill-in that
        disagreed with the unwindowed read would make the projection a cap in
        disguise.

        Resolved by id against that session's own messages
        (``SessionExplorer.tool_call``), so an unknown id is a typed 404 rather
        than a neighbouring call. Off the loop like every other transcript read
        here.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _read() -> ToolCallView:
            call = explorer.tool_call(ws_id, session_id, tool_use_id)
            if call is None:
                raise AgentSessionNotFound(
                    f"no tool call {tool_use_id!r} in session {session_id!r} "
                    f"for workspace {ws_id!r}"
                )
            return ToolCallView.from_call(call)

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
            supported = mgr.queue_supported(ws_id)
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
    async def workspace_fleet(
        ws_id: str, session_id: Annotated[str, Query()] | None = None
    ) -> SubagentFleetView:
        """The workspace's live child-session roster, full detail, for one root.

        The ``/todo``/``/queue`` sibling: a fleet is unbounded in count exactly
        like a checklist or a message queue, so only counts ride the ~1 Hz
        stream (``WorkspaceActivityView.fleet``) and the roster itself is
        fetch-on-demand. ``session_id`` selects the root session (default: the
        workspace's readable primary) — any of the workspace's own sessions,
        never a foreign workspace's.

        ``sessions`` is the provider-neutral transcript-derived child
        projection (``SessionExplorer.fleet_activity``, generic across every
        adapter exposing the capability); ``subagents`` stays Claude Code's
        hook-pushed live roster, filtered to entries the transcript
        projection has not yet surfaced (a just-started child with no thread
        messages) so the same child never appears twice. ``supported=False``
        means this root's adapter has no child-reader capability, distinct
        from a supported root with no children (``sessions=[]``,
        ``supported=True``). A read failure sets ``error`` rather than being
        reported as an empty fleet.
        """
        mgr = _manager_for(ws_id)

        def _read() -> SubagentFleetView:
            explorer = SessionExplorer(mgr)
            resolved_id, supported, children = explorer.fleet_activity(ws_id, session_id)
            subagents: list[SubagentActivityView] = []
            state = mgr.get(ws_id)
            if (
                mgr.effective_kind(state) == "claude_code"
                and state.agent_session_id
                and resolved_id == state.agent_session_id
            ):
                known_child_ids = {c.session.session_id for c in children}
                records = ClaudeHook.list_subagents(
                    state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
                )
                subagents = [
                    SubagentActivityView.from_record(r)
                    for r in records
                    if r.agent_id not in known_child_ids
                ]
            return SubagentFleetView(
                subagents=subagents,
                sessions=[SessionActivityView.from_session_activity(c) for c in children],
                session_id=resolved_id,
                supported=supported,
            )

        try:
            return await asyncio.to_thread(_read)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc

    @app.get(
        "/workspaces/{ws_id}/fleet/stream",
        dependencies=auth_dep,
        responses={
            200: {
                "description": (
                    "text/event-stream of `fleet_snapshot` SubagentFleetView JSON "
                    "frames for one workspace root session. Coalesced: a burst of "
                    "activity edges produces at most one in-flight re-read at a "
                    "time, offloaded to a worker thread so a slow snapshot never "
                    "blocks `/message` or any other route. Heartbeats every 15s."
                ),
            }
        },
    )
    async def workspace_fleet_stream(
        ws_id: str, session_id: Annotated[str, Query()] | None = None
    ) -> StreamingResponse:
        """Live scoped fleet replacement for one workspace root session.

        Reuses the existing activity bus and its bounded-queue audience
        discipline — no new always-on poller, no host-wide roster payload, no
        child transcript bodies (a selected child's turns stay behind the
        existing cursor-aware ``/turns`` route). Authorization is the
        workspace's own tree: any session belonging to this workspace, never a
        stranger's. Teardown on disconnect or when the workspace/session is
        removed — the reader answers honestly rather than wedging the stream.
        """
        mgr = _manager_for(ws_id)
        explorer = SessionExplorer(mgr)

        def _read() -> FleetSnapshot:
            try:
                resolved_id, supported, children = explorer.fleet_activity(ws_id, session_id)
            except GroveError as exc:
                return FleetSnapshot(
                    SubagentFleetView(session_id=session_id, supported=False, error=str(exc)),
                    terminal=True,
                )
            subagents: list[SubagentActivityView] = []
            try:
                state = mgr.get(ws_id)
                if (
                    mgr.effective_kind(state) == "claude_code"
                    and state.agent_session_id
                    and resolved_id == state.agent_session_id
                ):
                    known_child_ids = {c.session.session_id for c in children}
                    records = ClaudeHook.list_subagents(
                        state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
                    )
                    subagents = [
                        SubagentActivityView.from_record(r)
                        for r in records
                        if r.agent_id not in known_child_ids
                    ]
            except GroveError:
                # The workspace vanished between the fleet read above and this
                # lookup — the snapshot below still answers honestly for the
                # session projection it already has.
                pass
            return FleetSnapshot(
                SubagentFleetView(
                    subagents=subagents,
                    sessions=[SessionActivityView.from_session_activity(c) for c in children],
                    session_id=resolved_id,
                    supported=supported,
                )
            )

        fleet_stream = FleetSnapshotStream(
            activity_service, audience, workspace_id=ws_id, reader=_read
        )
        fleet_stream.start()

        async def stream() -> AsyncIterator[str]:
            try:
                initial = await fleet_stream.snapshot()
                yield fleet_sse_frame(initial.view)
                if initial.terminal:
                    return
                while True:
                    try:
                        await asyncio.wait_for(
                            fleet_stream.changed(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                        )
                    except TimeoutError:
                        yield fleet_heartbeat_frame(session_id)
                        continue
                    snap = await fleet_stream.snapshot()
                    yield fleet_sse_frame(snap.view)
                    if snap.terminal:
                        return
            finally:
                fleet_stream.stop()

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
        "/workspaces/{ws_id}/live",
        dependencies=auth_dep,
        response_class=StreamingResponse,
        responses={
            200: {
                "model": LivePulseView,
                "description": (
                    "Server-sent `live_pulse` events: what the workspace's native "
                    "session is doing this second (thinking, generating, a tool, a "
                    "sub-agent). Produced only while a viewer holds this stream; "
                    "a terminal workspace sends heartbeats and nothing else."
                ),
            }
        },
    )
    async def workspace_live_stream(ws_id: str) -> StreamingResponse:
        """The working loader's feed, alive exactly as long as somebody reads it.

        Opening the stream is the watch: the first viewer asks the owner worker
        to start posting its live step and the last one leaving stops it, so an
        unwatched session costs nothing beyond the in-memory fold the worker
        already does. No snapshot read, no poll and no host-wide delta.
        """
        _manager_for(ws_id)
        queue = live_hub.join(ws_id)

        async def stream() -> AsyncIterator[str]:
            try:
                while True:
                    try:
                        pulse = await asyncio.wait_for(
                            queue.get(), timeout=_HEARTBEAT_INTERVAL_SECONDS
                        )
                    except TimeoutError:
                        yield "event: heartbeat\ndata: {}\n\n"
                        continue
                    yield f"event: live_pulse\ndata: {pulse.model_dump_json()}\n\n"
            finally:
                live_hub.leave(ws_id, queue)

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

    @app.get(
        "/workspaces/{ws_id}/history",
        response_model=WorkspaceHistoryView,
        dependencies=auth_dep,
    )
    async def workspace_history(ws_id: str) -> WorkspaceHistoryView:
        """The workspace's durable name, progress and ticket timeline.

        Fetch-on-demand rather than on the activity stream: no frame there
        carries these rows, and the claim being reported right now is already
        live on the workspace's own activity. The SQLite read runs in the
        executor so it never blocks the loop.

        **This route deliberately does NOT gate on the workspace still
        existing, and that is the one thing separating it from every sibling
        per-workspace read.** `kill` deletes the record, which is the normal end
        of a task — and the entire purpose of this store is to answer for a
        workspace whose record is gone. A `_manager_for` gate here (the obvious
        copy from `/todo` and `/queue`) made a killed workspace's history
        unreachable through the only route that serves it: verified 404 against
        a real tombstoned record before this was removed.

        So the refusal is the STORE's: an id with nothing recorded returns an
        empty view, exactly as it does for a live workspace that predates the
        store. Both are the same honest "nothing was recorded", and there is no
        404 to distinguish them because an unknown id is not a different fact
        here — a reader holding an id off a usage row has no way to know, or
        need to know, whether the workspace behind it still exists.
        """
        history = await asyncio.to_thread(history_store.history_for, ws_id)
        return WorkspaceHistoryView.from_history(history)

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
        a real, distinct answer from "phase=scope", not an error: a fleet
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

    # ─── /gallery — every .drawio on the host ────────────────────────────────

    @app.get("/gallery", response_model=list[GalleryItemView], dependencies=auth_dep)
    async def list_gallery() -> list[GalleryItemView]:
        """Every ``.drawio`` in a known repo's worktrees, newest-first.

        Host-wide by construction — a gallery is a browse surface like the
        session catalog, and it JOINS on that catalog for attribution, so it
        rides the same request-scoped memo and never the activity poll. Each
        row says whether a preview for its content digest is already cached;
        a client renders the missing ones itself and posts them back.
        """
        items = await asyncio.to_thread(gallery.items)
        return [
            GalleryItemView.from_item(
                item, preview_ready=DiagramGallery.preview_path(item.digest).is_file()
            )
            for item in items
        ]

    def _gallery_item(item_id: str) -> GalleryItem:
        item = gallery.find(item_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "diagram_not_found", "message": f"no diagram {item_id!r}"},
            )
        return item

    @app.get("/gallery/{item_id}", response_model=GalleryDocumentView, dependencies=auth_dep)
    async def read_gallery_document(item_id: str) -> GalleryDocumentView:
        """One diagram's source XML — what the read-only viewer loads and the
        download saves. Read through the scan's own record, never a path the
        client supplied."""
        item = await asyncio.to_thread(_gallery_item, item_id)
        try:
            source = await asyncio.to_thread(DiagramGallery.read_source, item)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return GalleryDocumentView(
            id=item.id,
            name=item.path.name,
            digest=hashlib.sha256(source).hexdigest(),
            xml=source.decode("utf-8", errors="replace"),
        )

    @app.get(
        "/gallery/{item_id}/preview",
        response_model=GalleryPreviewView,
        dependencies=auth_dep,
    )
    async def read_gallery_preview(item_id: str) -> GalleryPreviewView:
        """The cached first-page PNG for the item's CURRENT content, 404 until
        a browser has rendered that revision."""
        item = await asyncio.to_thread(_gallery_item, item_id)
        try:
            png = await asyncio.to_thread(DiagramGallery.read_preview, item.digest)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        if png is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "diagram_unavailable",
                    "message": "no preview has been rendered for this revision yet",
                },
            )
        return GalleryPreviewView(
            digest=item.digest, content_base64=base64.b64encode(png).decode("ascii")
        )

    @app.post(
        "/gallery/{item_id}/preview",
        response_model=GalleryPreviewView,
        dependencies=auth_dep,
    )
    async def save_gallery_preview(
        item_id: str, body: GalleryPreviewUploadRequest
    ) -> GalleryPreviewView:
        """Store a browser-rendered PNG for the item's current digest.

        The digest is echoed by the client and checked against the scan's,
        so a render of a file that changed underneath is refused rather than
        filed against bytes it does not depict.
        """
        item = await asyncio.to_thread(_gallery_item, item_id)
        if body.digest != item.digest:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "diagram_conflict",
                    "message": "diagram changed since this preview was rendered; reload",
                },
            )
        try:
            png = base64.b64decode(body.content_base64, validate=True)
        except binascii.Error as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_preview", "message": "preview must be base64 PNG"},
            ) from exc
        try:
            await asyncio.to_thread(DiagramGallery.write_preview, item.digest, png)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return GalleryPreviewView(
            digest=item.digest, content_base64=base64.b64encode(png).decode("ascii")
        )

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

    @app.get("/projects/candidates", response_model=ProjectCandidatesView, dependencies=auth_dep)
    async def project_candidates() -> ProjectCandidatesView:
        """Git repositories under `projects_root` that Grove does not list yet.

        The "add an existing repository" step's source. Off-loop because it
        stats every entry of the root and reads each candidate's remotes.
        """

        def _scan() -> ProjectCandidatesView:
            known = {root.resolve() for root in registry.known_roots()}
            return onboarding.candidates(known)

        return await asyncio.to_thread(_scan)

    @app.post("/projects", status_code=201, response_model=ProjectView, dependencies=auth_dep)
    async def add_project(body: AddProjectRequest) -> ProjectView:
        """Create, clone or adopt a repository and add it to the user config.

        Rides the lifecycle pool, not the default executor: a clone takes as long
        as the remote and the repository make it take.

        The registry is reloaded HERE, with the watch's own loader, before the
        response leaves. The user-config watch would get there a second or two
        later, and in that window every repo-gated route (`/agents`,
        `/branches`, `/defaults`) answered `unknown_repo_root` for the project a
        client had just been told exists — measured as a 404 on the launch
        picker's first agents read. The watch's later reload then finds nothing
        changed. Pushing the new snapshot to clients stays the watch's job.
        """

        def _add_and_register(request: AddProjectRequest) -> Path:
            root = onboarding.add(request)
            registry.reload_projects(load_config(repo_root=None).projects)
            return root

        try:
            repo_root = await lifecycle.run(None, _add_and_register, body)
        except GitError as exc:
            # The git failure is the user's to act on (a wrong URL, no access),
            # and its message is git's own, already free of credentials.
            raise HTTPException(
                status_code=422, detail={"error": "project_git_failed", "message": str(exc)}
            ) from exc
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return ProjectView.from_project(Project(repo_root=repo_root, cwd=repo_root))

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
                    efforts=get_adapter(spec.kind).efforts,
                )
                for spec in agents
            ]

        return await asyncio.to_thread(_build)

    @app.get("/models", response_model=list[ModelOptionView], dependencies=auth_dep)
    async def list_models(
        repo: Annotated[Path, Query()],
        agent: Annotated[str | None, Query()] = None,
    ) -> list[ModelOptionView]:
        """One agent's model catalog, enriched with the name and window a picker draws.

        The read beside ``/agents`` rather than a widening of it: that route's
        ``models`` tuple is a published contract with several consumers, and a
        picker wanting a second line per row is not a reason to change what the
        catalog IS. Same ``repo`` dispatch and the same ``_known_root`` gate,
        because this runs the same configured discovery command.

        ``agent`` omitted means the repo's first configured agent, which is what
        an untouched create form has selected. An unknown name is an empty list
        rather than a 404: the catalog is a display hint, and a client asking
        about an agent this repo's cascade does not define has no picker to draw
        either way.

        Authenticated like every other listing, so the enrichment — which names
        the models an operator runs and the windows their gateway publishes —
        reaches nobody who is not already trusted with the roster itself.
        Off-loaded for ``/agents``' reason: Codex discovery shells out.
        """
        mgr = registry.get(_known_root(repo))
        cfg_for_repo = mgr.config
        spec = (
            cfg_for_repo.find_agent(agent)
            if agent is not None
            else next(iter(cfg_for_repo.agents), None)
        )
        if spec is None:
            return []
        return list(await asyncio.to_thread(model_options, spec, cfg=cfg_for_repo))

    @app.get("/defaults", response_model=WorkspaceDefaultsView, dependencies=auth_dep)
    async def get_workspace_defaults(
        repo: Annotated[Path, Query()],
    ) -> WorkspaceDefaultsView:
        """Resolved create-form defaults for one registered repo's cascade.

        The raw ``defaults`` section is deliberately not returned: runtime,
        brief, branch mode, and init behavior already have create-path fallbacks,
        and a remote form needs the same pre-selected answers the TUI sees.
        """
        mgr = registry.get(_known_root(repo))
        # The SAME config `create` resolves against (`launch_config`: the
        # manager's cascade with saved defaults re-read), so this view and an
        # untouched create can never disagree, and a `PUT /defaults` shows here
        # and applies to the next create at once.
        view = WorkspaceDefaultsView.from_config(await asyncio.to_thread(mgr.launch_config))
        hub = federation.routes.hub if federation is not None else None
        if hub is None:
            return view
        nodes = [
            RuntimeTargetView(node=node.name, runtimes=tuple(node.runtimes))
            for node in await asyncio.to_thread(hub.nodes)
            if node.state == "reachable"
        ]
        return view.model_copy(update={"targets": (*view.targets, *nodes)})

    @app.post("/launch/preview", response_model=LaunchPreviewView, dependencies=auth_dep)
    async def preview_launch(req: CreateWorkspaceRequest) -> LaunchPreviewView:
        """What ``POST /workspaces`` would do with this exact body, before anything is created.

        Same resolution and same pre-side-effect checks as the create, run
        against the same per-repo config, so a form showing this is showing
        what the create will record. ``problems`` empty means validation
        passed; provisioning is still checked by the create itself. Cheap
        enough to call on every draft change: container readiness is a
        shared reading at most a few seconds old (`_ReadinessMemo`), and
        nothing here runs git, tmux or Docker on the request.
        """
        if req.repo_root is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "repo_root_required",
                    "message": "POST /launch/preview requires repo_root in the request body",
                },
            )
        mgr = registry.get(_known_root(req.repo_root))
        unavailable = await readiness.reason(mgr.repo_root, mgr.container_unavailable_reason)
        # The project's own requires_container refusal, memoized the same way,
        # so the preview raises the identical 422 a create would.
        refused = await readiness.reason(mgr.repo_root, mgr.host_refusal, "host_refusal")
        preview = await asyncio.to_thread(
            partial(mgr.preview, container_unavailable=unavailable, host_refused=refused), req
        )
        return LaunchPreviewView.from_preview(preview, req)

    @app.get("/launch/options", response_model=LaunchOptionsView, dependencies=auth_dep)
    async def list_launch_options(repo: Annotated[Path, Query()]) -> LaunchOptionsView:
        """Where a create in ``repo`` could run, configured or not, and whether each is ready.

        Host and container readiness are the same preflight checks ``grove
        doctor`` runs, read through the preview's shared memo so a form fetching
        this costs at most one probe per runtime every few seconds. A remote
        block's platform is never probed and reads ``unknown``; a hub appends
        each reachable node's runtimes by name, also ``unknown``.
        """
        mgr = registry.get(_known_root(repo))
        host = await readiness.reason(mgr.repo_root, mgr.host_unavailable_reason, "host")
        container = await readiness.reason(
            mgr.repo_root, mgr.container_unavailable_reason, "container"
        )
        options = runtime_options(
            mgr.config, host_unavailable=host, container_unavailable=container
        )
        hub = federation.routes.hub if federation is not None else None
        if hub is not None:
            for node in await asyncio.to_thread(hub.nodes):
                if node.state == "reachable":
                    options.extend(node_options(node.name, node.runtimes))
        return LaunchOptionsView(
            runtimes=tuple(RuntimeOptionView.from_option(option) for option in options)
        )

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
        required = set(WorkspaceDefaults.model_fields) - WorkspaceDefaults.HAND_EDITED
        if not required <= defaults.model_fields_set:
            missing = sorted(required - defaults.model_fields_set)
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "incomplete_defaults",
                    "message": f"complete defaults replacement requires: {', '.join(missing)}",
                },
            )

        def _save() -> WorkspaceDefaultsSaveView:
            saved_fields = defaults.model_dump(exclude_none=True)
            higher = shadowing_defaults_keys(scope, repo_root)
            shadowed = tuple(name for name in saved_fields if name in higher)
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

    # ─── artifacts: files and links a workspace hands back ───────────────────

    @app.post(
        "/workspaces/{ws_id}/artifacts",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def attach_artifact(ws_id: str, body: ArtifactAttachRequest) -> WorkspaceStateView:
        """Attach a file path or an http(s) link to a workspace.

        The engine classifies and validates the target: a file must be a
        regular file under the workspace checkout, the project root or a
        configured ``artifacts.roots`` directory. Idempotent by target. A
        refused target is 422 ``artifact_invalid``.
        """
        mgr = _manager_for(ws_id)
        try:
            state = await asyncio.to_thread(mgr.attach_artifact, ws_id, body.target, body.title)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.delete(
        "/workspaces/{ws_id}/artifacts",
        response_model=WorkspaceStateView,
        dependencies=auth_dep,
    )
    async def detach_artifact(
        ws_id: str, ref: Annotated[str, Query(min_length=1, max_length=4096)]
    ) -> WorkspaceStateView:
        """Remove an artifact by its id or by the target it was attached with.

        ``ref`` is a query parameter because a target is a path or a URL, and
        neither survives as one path segment. Idempotent.
        """
        mgr = _manager_for(ws_id)
        try:
            state = await asyncio.to_thread(mgr.detach_artifact, ws_id, ref)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return WorkspaceStateView.from_state(state)

    @app.get(
        "/workspaces/{ws_id}/artifacts/{artifact_id}/content",
        dependencies=auth_dep,
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    async def read_artifact_content(ws_id: str, artifact_id: str) -> Response:
        """The current bytes of one attached file artifact.

        Only an attached artifact is addressable, so a client never names a
        path, and the engine re-checks the allowed directories on every read.

        ALWAYS ``application/octet-stream`` with ``nosniff``, whatever the file
        is. An agent-written HTML or SVG file opened directly at this URL must
        download, never render on the dashboard's origin. The viewer picks a
        renderer from the file name, which rides ``Content-Disposition``.
        """
        mgr = _manager_for(ws_id)
        try:
            content = await asyncio.to_thread(mgr.read_artifact, ws_id, artifact_id)
        except GroveError as exc:
            raise _grove_error_to_http(exc) from exc
        return Response(
            content=content.data,
            media_type="application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(content.name)}",
                "Cache-Control": "no-store",
            },
        )

    return app
