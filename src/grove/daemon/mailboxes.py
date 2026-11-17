"""The contacts directory, the send route, and the native owner's own stream.

Two concerns share this module because they share one dependency — the registry
of connected owner workers — and nothing else:

* **Mail** (`/mailboxes/contacts`, `/mailboxes/messages`) addresses any live
  managed agent and delegates delivery to `WorkspaceManager.send_message`. It
  knows nothing about owners; a terminal agent is reached by the same call.
* **Owners** (`/mailboxes/connection`, `/mailboxes/ack`) is how a Grove-owned
  native worker receives the frames the daemon steers it with.

Both sit behind the daemon's ordinary bearer. Grove runs on loopback for one
trusted user, so an agent sending peer mail is the same principal as the human
driving the dashboard — a second credential system here bought isolation
between parties that were never separate, and cost every interactive session
its ability to participate at all.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from grove.core import paths
from grove.core.agents.native_owner import AskRecorder
from grove.core.contracts.live import LivePulseView
from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxDirectory,
    MailboxReceipt,
    MailboxSendRequest,
)
from grove.core.errors import PaneNotFound, WorkspaceNotFound
from grove.core.mailboxes import MailboxDelivery
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.native_owners import (
    ControlOp,
    NativeOwnerRegistry,
    OwnerBinding,
    OwnerIdentity,
    OwnerUnavailable,
    WorktreeRead,
)
from grove.core.pane_log import PaneGap, PaneLog
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.transcript_mirror import BadRelativePath, ChunkRefused, TranscriptMirror
from grove.core.workspace import WorkspaceState
from grove.daemon._lifecycle import _LifecycleRunner
from grove.daemon._live import LiveHub

#: The fold arms a remote owner may write into the host spool.
_SPOOL_SUFFIXES = frozenset({AskRecorder.SUFFIX, AskRecorder.FACTS_SUFFIX, AskRecorder.TURN_SUFFIX})
#: The largest single drop accepted; a question's input is the biggest body.
_SPOOL_DROP_LIMIT = 262_144
#: Sends kept per workspace to label its queue; the owner's input capacity.
_RECENT_INPUTS = 16


class _OwnerAckBody(BaseModel):
    """A native owner's bounded observation of one submitted frame."""

    message_id: str = Field(min_length=1, max_length=128)
    stage: Literal["queued", "delivered", "unknown", "rejected"]


class _ReadResultBody(BaseModel):
    """What one worktree command the owner ran answered."""

    request_id: str = Field(min_length=1, max_length=128)
    returncode: int
    output: str = Field(max_length=WorktreeRead.OUTPUT_LIMIT)


class _PaneBatchBody(BaseModel):
    """Lines a remote owner printed, numbered from its first (see ``PaneLog``).

    ``reset`` replaces the replica with these lines; otherwise they must be the
    replica's next lines.
    """

    first: int = Field(ge=0)
    lines: list[Annotated[str, Field(max_length=PaneLog.LINE_CHARS)]] = Field(
        max_length=PaneLog.LIMIT
    )
    reset: bool = False


class _TranscriptChunkBody(BaseModel):
    """The next bytes of one of a remote owner's transcript files.

    ``data`` is base64 (the proxy-safe encoding every binary body here uses);
    the cap is on the ENCODED text so an oversized string is refused before it
    is decoded, and the decoded length is checked again against the mirror's
    limit.
    """

    session_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=1024)
    offset: int = Field(ge=0)
    data: str = Field(max_length=TranscriptMirror.CHUNK_LIMIT * 4 // 3 + 4)


class MailboxRouter:
    """Mail between managed agents, plus the native owners' control stream."""

    _INVALIDATING_EVENTS = frozenset(
        {"paused", "respawned", "killed", "error", "offline_detected", "orphaned_detected"}
    )

    def __init__(
        self,
        *,
        owners: NativeOwnerRegistry,
        registry: RepoRegistry,
        store: JsonWorkspaceStore,
        auth_dep: Callable[..., object],
        lifecycle: _LifecycleRunner,
        live: LiveHub | None = None,
        on_spooled: Callable[[str], object] | None = None,
    ) -> None:
        self._owners = owners
        # Told the session id of every drop a remote owner posts, so the
        # activity projection re-reads that session instead of waiting a tick.
        self._on_spooled = on_spooled
        self._live = live if live is not None else LiveHub(owners)
        self._registry = registry
        self._store = store
        self._auth_dep = auth_dep
        self._lifecycle = lifecycle
        self._delivery = MailboxDelivery(registry)
        self._unsubscribers: dict[Path, Callable[[], None]] = {}
        self._addresses_by_workspace: dict[str, set[MailboxAddress]] = {}
        self._connections: dict[MailboxAddress, asyncio.Task[object]] = {}
        self._wake_confirmations: set[asyncio.Task[None]] = set()

    def router(self) -> APIRouter:
        """Build the sole mailbox router for this daemon instance."""
        router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])
        auth = Depends(self._auth_dep)

        @router.get("/contacts", response_model=MailboxDirectory)
        async def contacts(_: object = auth) -> MailboxDirectory:
            # Reconciles every workspace against live tmux, so never on the loop.
            return await asyncio.to_thread(self._delivery.contacts)

        @router.post("/messages", response_model=MailboxReceipt)
        async def send_message(request: MailboxSendRequest, _: object = auth) -> MailboxReceipt:
            # The recipient's lifecycle lock alone: serializing both ends would
            # deadlock two agents replying to each other, and only the
            # recipient's teardown can remove the transport this send uses.
            async with self._lifecycle.hold(request.recipient.workspace_id):
                return await asyncio.to_thread(self._delivery.send, request)

        @router.get("/connection")
        async def connection(
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            provider_session_id: Annotated[str, Query(min_length=1, max_length=512)],
            _: object = auth,
            input_capacity: Annotated[int | None, Query(ge=1, le=256)] = None,
            pending_input_ids: Annotated[list[str] | None, Query(max_length=256)] = None,
            live: Annotated[bool, Query()] = False,
            reads: Annotated[bool, Query()] = False,
            transcripts: Annotated[bool, Query()] = False,
            cwd: Annotated[str | None, Query(max_length=4096)] = None,
        ) -> StreamingResponse:
            address = MailboxAddress(workspace_id=workspace_id)
            manager, state = await self._managed_native(address)
            self._ensure_lifecycle_subscription(manager, asyncio.get_running_loop())
            identity = OwnerIdentity(address=address, generation=uuid4().hex)
            try:
                async with self._lifecycle.hold(state.id):
                    binding = self._owners.register(
                        identity,
                        provider_session_id,
                        input_capacity=input_capacity,
                        pending_input_ids=tuple(pending_input_ids or ()),
                        live=live,
                        reads=reads,
                    )
                    if transcripts and cwd:
                        # A mirroring worker names the cwd its provider encoded
                        # into the transcript's folder; from here every host
                        # read resolves the mirrored copy.
                        await asyncio.to_thread(manager.record_mirrored_transcript, state.id, cwd)
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            # A restored worker must not wait for a registry delete before it
            # can receive its stream. The confirmation is isolated after the
            # registration edge, and a failure leaves its durable obligation.
            self._schedule_wake_confirmation(manager, state.id)
            self._addresses_by_workspace.setdefault(state.id, set()).add(address)
            self._live.rewatch(state.id)
            return StreamingResponse(
                self._connection_stream(binding),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        @router.post("/ack")
        async def acknowledge(
            body: _OwnerAckBody,
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> dict[str, str]:
            identity = OwnerIdentity(
                address=MailboxAddress(workspace_id=workspace_id), generation=generation
            )
            try:
                binding = self._owners.binding(identity)
                self._owners.acknowledge(binding, body.message_id, stage=body.stage)
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            return {"status": "ok"}

        @router.post("/read-result")
        async def read_result(
            body: _ReadResultBody,
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> dict[str, str]:
            """What a command the daemon asked the owner to run in its worktree answered."""
            identity = OwnerIdentity(
                address=MailboxAddress(workspace_id=workspace_id), generation=generation
            )
            try:
                binding = self._owners.binding(identity)
                self._owners.resolve_read(
                    binding,
                    body.request_id,
                    WorktreeRead(returncode=body.returncode, output=body.output),
                )
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            return {"status": "ok"}

        @router.post("/pulse")
        async def pulse(
            body: LivePulseView,
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> dict[str, str]:
            """A watched owner's newest live step; dropped when nobody watches."""
            identity = OwnerIdentity(
                address=MailboxAddress(workspace_id=workspace_id), generation=generation
            )
            try:
                self._owners.binding(identity)
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            self._live.publish(workspace_id, body)
            return {"status": "ok"}

        @router.post("/spool")
        async def spool(
            body: dict[str, Any],
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            suffix: Annotated[str, Query(max_length=32)],
            _: object = auth,
        ) -> dict[str, str]:
            """One spool drop from an owner that shares no filesystem with this host.

            A remote worker states its session's turn edges, standing ask and
            stream facts here; the daemon writes each into the host hook spool,
            where the sidecar reader folds it exactly like a local drop. Fenced
            like ``/pulse`` (bearer plus the connection's generation), and a
            drop may only speak for the session this owner registered: a worker
            naming another session would be writing a stranger's agent state.
            """
            await self._accept_spool(workspace_id, generation, suffix, body)
            return {"status": "ok"}

        self._pane_route(router, auth)
        self._transcript_routes(router, auth)

        return router

    def _pane_route(self, router: APIRouter, auth: Any) -> None:
        """The route a remote owner posts its pane lines through (split for size)."""

        @router.post("/pane")
        async def pane(
            body: _PaneBatchBody,
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> dict[str, int]:
            """A watched remote owner's newest pane lines; dropped when nobody watches.

            Fenced like ``/pulse``. A batch that does not continue the replica is
            409 carrying where the replica ends, and the worker answers by
            resending its whole log, so a lost batch never leaves a gap.
            """
            identity = OwnerIdentity(
                address=MailboxAddress(workspace_id=workspace_id), generation=generation
            )
            try:
                self._owners.binding(identity)
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            try:
                self._live.publish_pane(workspace_id, body.first, body.lines, reset=body.reset)
            except PaneGap as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "pane_gap", "message": str(exc), "end": exc.end},
                ) from exc
            return {"end": body.first + len(body.lines)}

    def _transcript_routes(self, router: APIRouter, auth: Any) -> None:
        """The route a mirroring owner replicates its transcript through.

        Split from :meth:`router` for size only; same prefix, same auth, same
        fence as ``/spool``.
        """

        @router.post("/transcript")
        async def transcript_chunk(
            body: _TranscriptChunkBody,
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            generation: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> dict[str, int]:
            """The next bytes of one of a remote owner's transcript files.

            Fenced like ``/spool``: bearer plus the connection's generation,
            and a chunk may only name the session this owner registered, so a
            worker can never write a stranger's transcript. Applied only when
            ``offset`` is the file's current size; otherwise 409 carrying that
            size, which is the worker's cue to re-read from there. That one
            answer is the whole sync protocol (there is no size query): a
            reconnect, a restored daemon or a file the host already holds all
            correct the worker on its next POST.
            """
            mirror = self._transcript_mirror(workspace_id, generation, session_id=body.session_id)
            try:
                data = base64.b64decode(body.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_transcript_chunk", "message": "data is not base64"},
                ) from exc
            if len(data) > TranscriptMirror.CHUNK_LIMIT:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "too_large", "message": "transcript chunk too large"},
                )
            try:
                applied = await asyncio.to_thread(
                    mirror.append, body.relative_path, body.offset, data
                )
            except BadRelativePath as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_transcript_path", "message": str(exc)},
                ) from exc
            except ChunkRefused as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "transcript_offset", "message": exc.reason, "size": exc.size},
                ) from exc
            if self._on_spooled is not None:
                # The projection re-reads this session now rather than at the
                # next tick, the same nudge a spool drop gives it.
                self._on_spooled(body.session_id)
            return {"size": applied.size}

        @router.get("/transcript/files")
        async def transcript_files(
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            session_id: Annotated[str, Query(min_length=1, max_length=128)],
            _: object = auth,
        ) -> list[dict[str, Any]]:
            """The host-mirrored files of a remote workspace's own session, with their sizes.

            Read by a fresh workload that is about to resume the session, before
            its provider starts and therefore before it can register, so the
            fence is the bearer alone. That reads no wider than ``/turns``,
            which serves the same transcript to every authenticated principal.
            Empty for any session but the record's own and for a non-remote
            workspace.
            """
            manager = await self._manager_for_record(workspace_id)
            files = await asyncio.to_thread(manager.mirrored_transcripts, workspace_id, session_id)
            return [{"relative_path": path, "size": size} for path, size in files]

        @router.get("/transcript")
        async def transcript_read(
            workspace_id: Annotated[str, Query(pattern=r"^[a-f0-9]{32}$")],
            session_id: Annotated[str, Query(min_length=1, max_length=128)],
            relative_path: Annotated[str, Query(min_length=1, max_length=1024)],
            offset: Annotated[int, Query(ge=0)] = 0,
            _: object = auth,
        ) -> dict[str, str]:
            """One bounded chunk of a file :func:`transcript_files` listed, base64."""
            manager = await self._manager_for_record(workspace_id)
            listed = await asyncio.to_thread(manager.mirrored_transcripts, workspace_id, session_id)
            if relative_path not in {path for path, _size in listed}:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "transcript_not_found", "message": "no such mirrored file"},
                )
            data = await asyncio.to_thread(
                TranscriptMirror.for_workspace(workspace_id).read, relative_path, offset
            )
            return {"data": base64.b64encode(data).decode("ascii")}

    async def _manager_for_record(self, workspace_id: str) -> WorkspaceManager:
        """The manager owning ``workspace_id``'s record; 404 when there is none."""
        try:
            persisted = await asyncio.to_thread(self._store.get, workspace_id)
        except WorkspaceNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "workspace not found"},
            ) from exc
        return self._registry.get(Path(persisted.repo_root))

    def _transcript_mirror(
        self, workspace_id: str, generation: str, *, session_id: str | None = None
    ) -> TranscriptMirror:
        """The mirror for a registered owner, after the same fence ``/spool`` applies.

        Raises:
            HTTPException: no such owner (404 / 409 per the registry), or a
                ``session_id`` naming any session but the owner's own (403).
        """
        identity = OwnerIdentity(
            address=MailboxAddress(workspace_id=workspace_id), generation=generation
        )
        try:
            binding = self._owners.binding(identity)
        except OwnerUnavailable as exc:
            raise self._http_for(exc) from exc
        if session_id is not None and session_id != binding.provider_session_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "foreign_session",
                    "message": "a chunk may only name this owner's own session",
                },
            )
        return TranscriptMirror.for_workspace(workspace_id)

    async def _accept_spool(
        self, workspace_id: str, generation: str, suffix: str, body: dict[str, Any]
    ) -> None:
        """Check one remote drop against its owner, then write it into the host spool.

        Raises:
            HTTPException: no such owner (404), an unknown fold arm (422), a
                drop naming any session but the owner's own (403), or an
                oversized body (413).
        """
        identity = OwnerIdentity(
            address=MailboxAddress(workspace_id=workspace_id), generation=generation
        )
        try:
            binding = self._owners.binding(identity)
        except OwnerUnavailable as exc:
            raise self._http_for(exc) from exc
        if suffix not in _SPOOL_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_spool_drop", "message": "unknown spool suffix"},
            )
        if body.get("session_id") != binding.provider_session_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "foreign_session",
                    "message": "a drop may only name this owner's own session",
                },
            )
        if len(json.dumps(body)) > _SPOOL_DROP_LIMIT:
            raise HTTPException(
                status_code=413, detail={"error": "too_large", "message": "spool drop too large"}
            )
        await asyncio.to_thread(AskRecorder.write_spool, paths.agent_hook_spool_dir(), suffix, body)
        if self._on_spooled is not None:
            self._on_spooled(binding.provider_session_id)

    def _schedule_wake_confirmation(self, manager: WorkspaceManager, workspace_id: str) -> None:
        """Forget a restored snapshot without delaying its owner's stream."""
        task = asyncio.create_task(
            self._confirm_wake(manager, workspace_id), name=f"grove-wake-confirm-{workspace_id}"
        )
        self._wake_confirmations.add(task)
        task.add_done_callback(self._wake_confirmations.discard)

    async def _confirm_wake(self, manager: WorkspaceManager, workspace_id: str) -> None:
        """Confirm a wake off-loop, retaining failed cleanup for reconnect."""
        try:
            await asyncio.to_thread(manager.confirm_wake, workspace_id)
        except Exception as exc:
            logger.warning("could not forget confirmed snapshot for {}: {}", workspace_id, exc)

    async def aclose(self) -> None:
        """Drop owner registrations WITHOUT treating a daemon stop as an edge.

        The worker in the pane outlives this process and reconnects the moment
        a daemon is back, so closing must not invalidate anything it needs to
        return — that shape once turned every ordinary restart into a dead
        session. Only a workspace lifecycle event unregisters for real.
        """
        tasks = list(self._connections.values())
        # A registry operation can outlive shutdown. Cancel its await without
        # waiting for its worker thread, just like lifecycle shutdown itself.
        for confirmation in self._wake_confirmations:
            confirmation.cancel()
        self._wake_confirmations.clear()
        for address in tuple(self._connections):
            self._owners.invalidate(address)
            self._connections.pop(address, None)
        self._addresses_by_workspace.clear()
        for unsubscribe in self._unsubscribers.values():
            unsubscribe()
        self._unsubscribers.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _managed_native(
        self, address: MailboxAddress
    ) -> tuple[WorkspaceManager, WorkspaceState]:
        """Prove this address names a workspace Grove owns a native session for.

        Reads the RECORD rather than the roster: the flag was decided at create,
        and a later config edit must not turn a running owner away.
        """
        try:
            persisted = await asyncio.to_thread(self._store.get, address.workspace_id)
        except WorkspaceNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "workspace not found"},
            ) from exc
        # Registry creation stays on the loop: its registration hook schedules
        # loop-owned prebuild work. The manager read itself belongs off-loop.
        manager = self._registry.get(Path(persisted.repo_root))
        state = await asyncio.to_thread(manager.get, address.workspace_id)
        if address.agent or not state.native:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "native_session_unsupported",
                    "message": "this workspace has no Grove-owned native session",
                },
            )
        return manager, state

    def _ensure_lifecycle_subscription(
        self, manager: WorkspaceManager, loop: asyncio.AbstractEventLoop
    ) -> None:
        root = manager.repo_root.resolve()
        if root in self._unsubscribers:
            return

        def on_event(event: WorkspaceEvent) -> None:
            if event.kind not in self._INVALIDATING_EVENTS:
                return
            loop.call_soon_threadsafe(self._invalidate_workspace, event.workspace_id)

        self._unsubscribers[root] = manager.subscribe(on_event)

    def _invalidate_workspace(self, workspace_id: str) -> None:
        for address in tuple(self._addresses_by_workspace.get(workspace_id, ())):
            self._owners.invalidate(address)
            self._connections.pop(address, None)
            addresses = self._addresses_by_workspace.get(address.workspace_id)
            if addresses is not None:
                addresses.discard(address)
                if not addresses:
                    del self._addresses_by_workspace[address.workspace_id]

    async def _connection_stream(self, binding: OwnerBinding) -> AsyncIterator[str]:
        task = asyncio.current_task()
        if task is not None:
            self._connections[binding.identity.address] = task
        try:
            registered = json.dumps({"generation": binding.identity.generation})
            yield f"event: registered\ndata: {registered}\n\n"
            while True:
                try:
                    frame = await self._owners.next_frame(binding)
                except OwnerUnavailable:
                    return
                payload = json.dumps(
                    {"op": frame.op, "message_id": frame.message_id, "text": frame.text}
                )
                yield f"event: delivery\ndata: {payload}\n\n"
        finally:
            self._owners.unregister(binding)
            self._connections.pop(binding.identity.address, None)
            addresses = self._addresses_by_workspace.get(binding.identity.address.workspace_id)
            if addresses is not None:
                addresses.discard(binding.identity.address)
                if not addresses:
                    del self._addresses_by_workspace[binding.identity.address.workspace_id]

    @staticmethod
    def _http_for(exc: OwnerUnavailable) -> HTTPException:
        status = 404 if exc.code in {"not_registered", "unknown_submission"} else 409
        if exc.code in {"invalid_receipt", "invalid_registration"}:
            status = 422
        return HTTPException(status_code=status, detail={"error": exc.code, "message": str(exc)})


class OwnerSteerClient:
    """The daemon's in-process ``NativeSteerClient``: hand a control to the owner.

    Every manager the daemon mints gets this injected (`RepoRegistry`'s
    ``native_steer``), so a steer route on a native workspace queues straight
    onto the owner's SSE delivery without a network hop. The queue is loop-owned
    state and the manager verb runs on a pool thread, which is why each op is
    marshalled back with ``call_soon_threadsafe`` and awaited — the refusal has
    to reach the caller as the typed error the route maps, not vanish into a
    callback.

    A missing owner is ``PaneNotFound``: the workspace's live shape has nowhere
    to deliver and a respawn fixes it, the same 409 a session with no window
    gets. A full queue is the same class of trouble (the owner is not draining)
    and takes the same code.
    """

    def __init__(self, owners: NativeOwnerRegistry) -> None:
        self._owners = owners
        self._loop: asyncio.AbstractEventLoop | None = None
        # What each workspace was last sent as input, as the provider receives
        # it: only to LABEL queue entries the harness recorded without text
        # (`label_queue`). Process memory on purpose; a restart leaves them blank.
        self._sent: dict[str, deque[tuple[str, datetime]]] = {}

    def recent_inputs(self, state: WorkspaceState) -> tuple[tuple[str, datetime], ...]:
        """The texts lately submitted to this workspace's owner, oldest first."""
        return tuple(self._sent.get(state.id, ()))

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Adopt the loop the owner queues live on (lifespan start).

        No owner can be registered before that loop runs, so a control that
        arrives unbound is honestly "no owner" rather than a wiring error.
        """
        self._loop = loop

    def owner_connected(self, state: WorkspaceState) -> bool:
        """Whether this workspace's owner worker is registered right now.

        The manager asks before deciding a steer needs a revive, so the answer
        must describe the REGISTRY rather than the record: a native workspace
        whose worker died is still `native`, and that is exactly the state worth
        reviving. Unbound (no loop yet at lifespan start) means no owner can
        have registered, which is honestly "no".
        """
        loop = self._loop
        if loop is None:
            return False
        if loop.is_running() and _current_loop() is not loop:
            future = asyncio.run_coroutine_threadsafe(_owner_present(self._owners, state.id), loop)
            return future.result(timeout=5)
        return self._owners.owner_for(state.id) is not None

    def run_in_worktree(
        self, state: WorkspaceState, argv: Sequence[str], timeout: float
    ) -> WorktreeRead:
        """Run ``argv`` in the owner worker's own worktree; called from a pool thread.

        Raises:
            PaneNotFound: No owner that serves reads is connected, or it did
                not answer within ``timeout``; the same remedy as a missing pane.
        """
        return self._ask_owner(
            state, lambda: self._owners.read(state.id, list(argv), timeout=timeout)
        )

    def transcript_tail(self, state: WorkspaceState, lines: int, timeout: float) -> WorktreeRead:
        """The last ``lines`` of the owner worker's own transcript; called from a pool thread.

        Raises:
            PaneNotFound: As for :meth:`run_in_worktree`.
        """
        return self._ask_owner(state, lambda: self._owners.tail(state.id, lines, timeout=timeout))

    def _ask_owner(
        self, state: WorkspaceState, ask: Callable[[], Coroutine[Any, Any, WorktreeRead]]
    ) -> WorktreeRead:
        loop = self._loop
        try:
            if loop is None or not loop.is_running():
                raise OwnerUnavailable("not_registered", "no native owner is connected")
            return asyncio.run_coroutine_threadsafe(ask(), loop).result()
        except OwnerUnavailable as exc:
            raise PaneNotFound(
                f"workspace {state.id}'s worker could not answer a worktree read ({exc.code})"
            ) from exc

    def send_message(self, state: WorkspaceState, text: str) -> None:
        self._control(state, "steer", text)

    def interrupt(self, state: WorkspaceState) -> None:
        self._control(state, "interrupt")

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self._control(state, "set_model", model)

    def set_effort(self, state: WorkspaceState, effort: str) -> None:
        self._control(state, "set_effort", effort)

    def compact(self, state: WorkspaceState) -> None:
        self._control(state, "compact")

    def invoke_control(self, state: WorkspaceState, name: str) -> None:
        self._control(state, "command", name)

    def run_macro(self, state: WorkspaceState, macro: str) -> None:
        self._control(state, "macro", macro)

    def answer(self, state: WorkspaceState, plan: str) -> None:
        self._control(state, "answer", plan)

    def _control(self, state: WorkspaceState, op: ControlOp, text: str = "") -> None:
        def queue() -> None:
            self._owners.control(state.id, op, text)

        loop = self._loop
        try:
            if loop is None:
                raise OwnerUnavailable("not_registered", "no native owner is connected")
            if loop.is_running() and _current_loop() is not loop:
                asyncio.run_coroutine_threadsafe(_call(queue), loop).result(timeout=5)
            else:
                queue()
        except OwnerUnavailable as exc:
            raise PaneNotFound(
                f"workspace {state.id} has no connected native owner to {op} ({exc.code})"
            ) from exc
        typed = {"steer": text, "command": f"/{text.strip().lstrip('/')}", "compact": "/compact"}
        if op in typed:
            sent = self._sent.setdefault(state.id, deque(maxlen=_RECENT_INPUTS))
            sent.append((typed[op], datetime.now(UTC)))


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


async def _call(fn: Callable[[], None]) -> None:
    fn()


async def _owner_present(owners: NativeOwnerRegistry, workspace_id: str) -> bool:
    """Read the registry ON its own loop (the queues live there)."""
    return owners.owner_for(workspace_id) is not None


__all__ = ["MailboxRouter", "OwnerSteerClient"]
