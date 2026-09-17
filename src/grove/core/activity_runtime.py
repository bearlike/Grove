"""Own ingestion and recovery for the maintained fleet projection."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, TypeVar

from loguru import logger

from grove.core.activity import ActivityService, DashboardDelta, RefreshDomain, WorkspaceActivity
from grove.core.admission import Admission, AdmissionLimits, BoundedInbox, InboxClosed

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class WorkspaceInvalidated:
    repo_root: str
    workspace_id: str
    reason: Literal["hook", "lifecycle", "filesystem", "runtime"]
    correlation: str = ""
    deleted: bool = False
    domains: RefreshDomain = RefreshDomain.FULL

    def merged(self, newer: WorkspaceInvalidated) -> WorkspaceInvalidated:
        """Preserve every pending fact when keyed hints coalesce."""
        return WorkspaceInvalidated(
            repo_root=newer.repo_root,
            workspace_id=newer.workspace_id,
            reason="filesystem" if "filesystem" in (self.reason, newer.reason) else newer.reason,
            correlation=newer.correlation or self.correlation,
            deleted=self.deleted or newer.deleted,
            domains=self.domains | newer.domains,
        )

    @property
    def size_bytes(self) -> int:
        return 64 + sum(
            len(value.encode())
            for value in (self.repo_root, self.workspace_id, self.reason, self.correlation)
        )


@dataclass(frozen=True, slots=True)
class ReconcileRequested:
    """A wake-up for the authoritative recovery scan, not a workspace identity."""

    reason: Literal["filesystem", "runtime"]

    @property
    def size_bytes(self) -> int:
        # A one-byte reservation fits every valid AdmissionLimits.max_bytes.
        # The object carries no caller-controlled payload.
        return 1


class ActivityRuntime:
    """A bounded source owner, independent of HTTP and dashboard serialization.

    Invalidations are replaceable hints to read authoritative state. They are
    not hook records or control commands. Refused hints require explicit recovery.
    The worker retains a trailing hint while a key is being computed.
    """

    _RECOVERY_KEY = "__grove_activity_reconcile__"
    _CLOSE_DRAIN_SECONDS = 2.0

    def __init__(self, service: ActivityService, *, limits: AdmissionLimits) -> None:
        self._service = service
        self._inbox: BoundedInbox[WorkspaceInvalidated | ReconcileRequested] = BoundedInbox(limits)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-projection")
        self._task: asyncio.Task[None] | None = None
        self._bootstrap: asyncio.Task[None] | None = None
        self._unsub: Callable[[], None] | None = None
        self._ready = False
        self._recovery_needed = False
        self._pending: dict[str, WorkspaceInvalidated] = {}
        # `invalidate()` is called from BOTH the event loop (file-source
        # callbacks, the hook route, RuntimeSources) and a `grove-lifecycle`
        # POOL THREAD — `ActivityService._bridge_callback` fires synchronously
        # inside whatever thread ran a manager verb (create/kill/pause), which
        # in the daemon is the lifecycle pool. `_pending` is a plain dict with
        # no other synchronization, so those two call paths race on it without
        # this lock: a coalesced domain union can be lost, or `_run`'s pop can
        # observe a torn write. `BoundedInbox` already guards its own state,
        # but it knows nothing about `_pending`, which lives one layer above it.
        self._pending_lock = threading.Lock()
        # A shutdown cannot stop arbitrary filesystem/git work already handed to
        # the executor. It can, however, fence that result from publication.
        self._generation = 0
        self._transition_lock = asyncio.Lock()

    async def start(self) -> None:
        """Own the projection from now on; do NOT hold readiness for its scan.

        The bootstrap is a full-fleet read — git and tmux per workspace — so
        awaiting it here made daemon startup O(fleet): measured 1.2s against
        6.7s on this host, which put `/healthz` past the SDK's spawn budget and
        reported as "local daemon failed to print port within timeout". Nothing
        served before it completes depends on it, and the one thing that does
        (`ActivityService.snapshot`) bootstraps on demand under the same
        idempotent lock — so a reader waits for its own answer instead of every
        caller waiting for a reader that may never come.

        The worker is started FIRST so an invalidation arriving mid-bootstrap
        is queued rather than dropped; `_ready` still gates publication, and
        the inbox coalesces per workspace, so a burst during startup costs one
        refresh per workspace afterwards.
        """
        if self._task is not None:
            raise RuntimeError("activity runtime already started")
        self._inbox.bind()
        self._unsub = self._service.subscribe(self._on_delta)
        self._task = asyncio.create_task(self._run(), name="grove-activity-projection")
        loop = asyncio.get_running_loop()
        self._bootstrap = asyncio.create_task(
            self._bootstrap_projection(loop), name="grove-activity-bootstrap"
        )

    async def wait_ready(self) -> None:
        """Await the first projection. For a caller that needs state, not uptime.

        The daemon deliberately does NOT call this — its readiness is
        independent of fleet size, which is the whole point of the background
        bootstrap — but a caller that is about to assert on projected state has
        to be able to say so rather than race it.
        """
        if self._bootstrap is not None:
            await asyncio.shield(self._bootstrap)

    async def _bootstrap_projection(self, loop: asyncio.AbstractEventLoop) -> None:
        """Build the first projection off-loop, then accept published results."""
        try:
            await loop.run_in_executor(self._executor, self._service.bootstrap)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Not fatal: `snapshot` bootstraps on demand, so a failure here
            # costs the eager warm-up and nothing else. Silence would be the
            # real cost, since a task exception surfaces only at GC time.
            logger.warning("activity projection bootstrap failed: {}", type(exc).__name__)
        self._ready = True

    def invalidate(self, event: WorkspaceInvalidated) -> Admission:
        """Union coalesced source hints before the inbox replaces their envelope.

        Called from both the event loop and a lifecycle pool thread (see
        ``_pending_lock``'s docstring), so the read-merge-write against
        ``_pending`` is not safe to split across two dict operations with
        nothing holding them together.
        """
        key = event.workspace_id
        with self._pending_lock:
            pending = self._pending.get(key)
            merged = pending.merged(event) if pending is not None else event
            result = self._inbox.offer(merged, size_bytes=merged.size_bytes, key=key)
            if result in (Admission.ACCEPTED, Admission.COALESCED):
                self._pending[key] = merged
        if result in (Admission.FULL, Admission.TOO_LARGE):
            self._recovery_needed = True
        return result

    def request_recovery(self) -> None:
        """Coalesce a lost-source notification into one bounded recovery job."""
        self._recovery_needed = True
        request = ReconcileRequested("filesystem")
        self._inbox.offer(request, size_bytes=request.size_bytes, key=self._RECOVERY_KEY)

    async def reconcile(self) -> None:
        """Reconcile after a source owner has armed its subscriptions."""
        if not self._ready:
            return
        async with self._transition_lock:
            publish, result = await self._compute(self._service.prepare_reconcile)
            if publish:
                self._service.apply_reconcile(result, emit=True)

    def hook(self, session_id: str, *, correlation: str = "") -> Admission:
        if not self._ready:
            return Admission.NOT_READY
        keys = self._service.workspace_keys_for_session(session_id)
        # Unmapped external sessions belong to the catalog source, not a fleet scan.
        outcome = Admission.ACCEPTED
        for repo_root, workspace_id in keys:
            result = self.invalidate(
                WorkspaceInvalidated(
                    repo_root,
                    workspace_id,
                    "hook",
                    correlation=correlation,
                    domains=RefreshDomain.TRANSCRIPT,
                )
            )
            if result not in (Admission.ACCEPTED, Admission.COALESCED):
                outcome = result
        return outcome

    def _on_delta(self, delta: DashboardDelta) -> None:
        if delta.kind != "workspace_changed" or delta.repo_root is None:
            return
        self.invalidate(
            WorkspaceInvalidated(
                delta.repo_root,
                delta.workspace_id,
                "lifecycle",
                deleted=delta.detail.get("event") == "killed",
                domains=RefreshDomain.FULL,
            )
        )

    async def _compute(self, fn: Callable[[], _T]) -> tuple[bool, _T]:
        """Run reads off-loop and fence their result at this owner's generation."""
        generation = self._generation
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, fn)
        result = await asyncio.shield(future)
        return self._ready and generation == self._generation, result

    async def _run(self) -> None:
        while True:
            try:
                delivery = await self._inbox.take()
            except InboxClosed:
                return
            event = delivery.value
            if isinstance(event, WorkspaceInvalidated):
                # A newer delivery may have merged more domains into the hint
                # after this envelope was offered but before the worker took it.
                with self._pending_lock:
                    event = self._pending.pop(event.workspace_id, event)
            try:
                async with self._transition_lock:
                    if isinstance(event, WorkspaceInvalidated):
                        await self._refresh(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                workspace_id = event.workspace_id if isinstance(event, WorkspaceInvalidated) else ""
                correlation = event.correlation if isinstance(event, WorkspaceInvalidated) else ""
                logger.bind(workspace_id=workspace_id, correlation=correlation).warning(
                    "activity transition failed: {}", type(exc).__name__
                )
            finally:
                self._inbox.complete(delivery)
            if self._recovery_needed and self._ready:
                self._recovery_needed = False
                try:
                    async with self._transition_lock:
                        publish, snapshot = await self._compute(self._service.prepare_reconcile)
                        if publish:
                            self._service.apply_reconcile(snapshot, emit=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("activity recovery failed: {}", type(exc).__name__)

    async def _refresh(self, event: WorkspaceInvalidated) -> None:
        if event.deleted:
            self._service.remove_workspace(event.repo_root, event.workspace_id)
            return

        def prepare() -> WorkspaceActivity | None:
            return self._service.prepare_workspace_refresh(
                event.repo_root, event.workspace_id, domains=event.domains
            )

        publish, row = await self._compute(prepare)
        if publish:
            self._service.apply_workspace_refresh(event.repo_root, event.workspace_id, row)
            # Any REAL filesystem write — not a hook push, a lifecycle bridge
            # event or a runtime liveness edge — invalidates client-side
            # content queries even when the activity fingerprint stayed equal
            # (a tool body changing at the same tool count, a diff whose byte
            # count happens to match). This is `reason`, never `domains`: a
            # domain says WHICH cached facts this refresh recomputed, `reason`
            # says WHERE the hint originated, and `useWorkspaceDiff`'s
            # contract is "per filesystem invalidation", not "per worktree
            # domain" — narrowing this to WORKTREE alone silently dropped the
            # edge for every transcript-only and phase-only filesystem write.
            if event.reason == "filesystem":
                self._service.source_changed(event.repo_root, event.workspace_id)

    async def close(self) -> None:
        self._ready = False
        self._generation += 1
        # Cancelled first and NOT waited on: the executor call it is parked in
        # is uncancellable filesystem work, and shutdown must never block on
        # side-effecting work already in flight. `_generation` has already
        # moved, so a result arriving late is fenced from publication.
        if self._bootstrap is not None:
            self._bootstrap.cancel()
            self._bootstrap = None
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        pending = self._inbox.close()
        if pending:
            logger.info("activity stopped with {} invalidations pending for restart", len(pending))
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task), timeout=self._CLOSE_DRAIN_SECONDS
                )
            except TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        self._executor.shutdown(wait=False, cancel_futures=True)
