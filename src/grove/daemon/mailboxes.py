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
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
)
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon._lifecycle import _LifecycleRunner


class _OwnerAckBody(BaseModel):
    """A native owner's bounded observation of one submitted frame."""

    message_id: str = Field(min_length=1, max_length=128)
    stage: Literal["queued", "delivered", "unknown", "rejected"]


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
    ) -> None:
        self._owners = owners
        self._registry = registry
        self._store = store
        self._auth_dep = auth_dep
        self._lifecycle = lifecycle
        self._delivery = MailboxDelivery(registry)
        self._unsubscribers: dict[Path, Callable[[], None]] = {}
        self._addresses_by_workspace: dict[str, set[MailboxAddress]] = {}
        self._connections: dict[MailboxAddress, asyncio.Task[object]] = {}

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
                    )
            except OwnerUnavailable as exc:
                raise self._http_for(exc) from exc
            self._addresses_by_workspace.setdefault(state.id, set()).add(address)
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

        return router

    async def aclose(self) -> None:
        """Drop owner registrations WITHOUT treating a daemon stop as an edge.

        The worker in the pane outlives this process and reconnects the moment
        a daemon is back, so closing must not invalidate anything it needs to
        return — that shape once turned every ordinary restart into a dead
        session. Only a workspace lifecycle event unregisters for real.
        """
        tasks = list(self._connections.values())
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

    def send_message(self, state: WorkspaceState, text: str) -> None:
        self._control(state, "steer", text)

    def interrupt(self, state: WorkspaceState) -> None:
        self._control(state, "interrupt")

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self._control(state, "set_model", model)

    def compact(self, state: WorkspaceState) -> None:
        self._control(state, "compact")

    def invoke_control(self, state: WorkspaceState, name: str) -> None:
        self._control(state, "command", name)

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
