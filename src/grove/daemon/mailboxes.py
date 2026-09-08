"""Authenticated HTTP and runtime-SSE faces for native agent mailboxes.

The coordinator is process-local state; this module only binds its addressing to
managed workspaces and preserves the daemon's lifecycle exclusion invariant.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from grove.core.auth import Session, SessionStore
from grove.core.contracts.mailboxes import (
    MailboxAccess,
    MailboxAddress,
    MailboxIdentity,
    MailboxPeer,
    MailboxPeerPage,
    MailboxReason,
    MailboxReceipt,
    MailboxRequest,
)
from grove.core.errors import PaneNotFound, WorkspaceNotFound
from grove.core.mailboxes import (
    ControlOp,
    MailboxBinding,
    MailboxCoordinator,
    MailboxUnavailable,
)
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon._lifecycle import _LifecycleRunner


class _MailboxAckBody(BaseModel):
    """A native owner's bounded observation of one submitted message."""

    message_id: str = Field(pattern=r"^mbx_[a-f0-9]{32}$")
    stage: Literal["queued", "delivered", "unknown", "rejected"]
    evidence: str | None = Field(default=None, max_length=4096)
    reason: MailboxReason | None = None


class MailboxRouter:
    """Bind one coordinator to managed primary agents and daemon lifecycle edges."""

    _INVALIDATING_EVENTS = frozenset(
        {"paused", "respawned", "killed", "error", "offline_detected", "orphaned_detected"}
    )

    def __init__(
        self,
        *,
        coordinator: MailboxCoordinator,
        registry: RepoRegistry,
        store: JsonWorkspaceStore,
        auth_store: SessionStore,
        require_mailbox_session: Callable[[Request], Awaitable[Session]],
        lifecycle: _LifecycleRunner,
    ) -> None:
        self._coordinator = coordinator
        self._registry = registry
        self._store = store
        self._auth_store = auth_store
        self._require_mailbox_session = require_mailbox_session
        self._lifecycle = lifecycle
        self._unsubscribers: dict[Path, Callable[[], None]] = {}
        self._addresses_by_workspace: dict[str, set[MailboxAddress]] = {}
        self._connections: dict[MailboxAddress, asyncio.Task[object]] = {}
        self._revocations: set[asyncio.Task[None]] = set()

    def router(self) -> APIRouter:
        """Build the sole authenticated mailbox router for this daemon instance."""
        router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])

        @router.get("/peers", response_model=MailboxPeerPage)
        async def peers(
            session: Session = Depends(self._require_mailbox_session),  # noqa: B008
            workspace_id: Annotated[str | None, Query(pattern=r"^[a-f0-9]{32}$")] = None,
            limit: Annotated[int, Query(ge=1, le=200)] = 50,
            cursor: Annotated[str | None, Query(max_length=128)] = None,
        ) -> MailboxPeerPage:
            caller = self._peer_caller(session)
            try:
                rows = await self._managed_primary_peers()
                return self._coordinator.peers(
                    caller, rows, workspace_id=workspace_id, limit=limit, cursor=cursor
                )
            except MailboxUnavailable as exc:
                raise self._http_for(exc) from exc

        @router.post("/messages", response_model=MailboxReceipt)
        async def send_message(
            request: MailboxRequest,
            session: Session = Depends(self._require_mailbox_session),  # noqa: B008
        ) -> MailboxReceipt:
            caller = self._peer_caller(session)
            if caller is None:
                raise self._http_for(
                    MailboxUnavailable(
                        "sender_not_bound", "peer sending requires a mailbox session"
                    )
                )
            try:
                target_workspace = self._target_workspace(caller, request)
                # The target lock is enough: serializing both endpoints would deadlock
                # reciprocal replies, while only the recipient's lifecycle can remove
                # the transport awaited by this send.
                async with self._lifecycle.hold(target_workspace):
                    return await self._coordinator.send(caller, request)
            except MailboxUnavailable as exc:
                raise self._http_for(exc) from exc

        @router.get("/messages/{message_id}", response_model=MailboxReceipt)
        async def message_status(
            message_id: str,
            session: Session = Depends(self._require_mailbox_session),  # noqa: B008
        ) -> MailboxReceipt:
            caller = self._peer_caller(session)
            try:
                return self._coordinator.status(caller, message_id)
            except MailboxUnavailable as exc:
                raise self._http_for(exc) from exc

        @router.get("/connection")
        async def connection(
            provider_session_id: Annotated[str, Query(min_length=1, max_length=512)],
            session: Session = Depends(self._require_mailbox_session),  # noqa: B008
            mcp_ready: bool = False,
            input_capacity: Annotated[int | None, Query(ge=1, le=256)] = None,
            pending_input_ids: Annotated[list[str] | None, Query(max_length=256)] = None,
        ) -> StreamingResponse:
            identity = self._runtime_identity(session)
            manager, state = await self._managed_mailbox(identity)
            self._ensure_lifecycle_subscription(manager, asyncio.get_running_loop())
            access = MailboxAccess(
                can_discover=True, can_send=True, can_reply=True, cli=True, mcp=mcp_ready
            )
            try:
                async with self._lifecycle.hold(state.id):
                    binding = self._coordinator.register(
                        identity,
                        provider_session_id,
                        access,
                        input_capacity=input_capacity,
                        pending_input_ids=tuple(pending_input_ids or ()),
                    )
            except MailboxUnavailable as exc:
                raise self._http_for(exc) from exc
            self._addresses_by_workspace.setdefault(state.id, set()).add(identity.address)
            return StreamingResponse(
                self._connection_stream(binding),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        @router.post("/ack", response_model=MailboxReceipt)
        async def acknowledge(
            body: _MailboxAckBody,
            session: Session = Depends(self._require_mailbox_session),  # noqa: B008
        ) -> MailboxReceipt:
            identity = self._runtime_identity(session)
            try:
                # `send` holds this recipient's lifecycle lock while awaiting the
                # acknowledgement. Acquiring it again here would deadlock the only
                # runtime capable of settling the bounded send.
                binding = self._coordinator.binding(identity)
                return self._coordinator.acknowledge(
                    binding,
                    body.message_id,
                    stage=body.stage,
                    evidence=body.evidence,
                    reason=body.reason,
                )
            except MailboxUnavailable as exc:
                raise self._http_for(exc) from exc

        return router

    async def aclose(self) -> None:
        """Disconnect every owner stream WITHOUT revoking its credentials.

        A daemon stop is not a workspace edge: the worker in the pane outlives
        this process and reconnects with the same registration token the
        moment a daemon is back. Revoking here (the original shape) turned
        every ordinary restart — a reinstall, a reboot — into a dead session:
        the worker's reconnect answered 401, it exited, and the pane held a
        one-line `ValueError` with no way back but a respawn. Only a workspace
        lifecycle event (`_INVALIDATING_EVENTS`) revokes; a close just drops
        the in-memory bindings so the streams end.
        """
        tasks = list(self._connections.values())
        for address in tuple(self._connections):
            self._coordinator.invalidate(address)
            self._connections.pop(address, None)
        self._addresses_by_workspace.clear()
        for unsubscribe in self._unsubscribers.values():
            unsubscribe()
        self._unsubscribers.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._revocations:
            await asyncio.gather(*self._revocations, return_exceptions=True)

    def _peer_caller(self, session: Session) -> MailboxIdentity | None:
        if session.mailbox_registration:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "mailbox_registration_denied",
                    "message": "runtime registration credentials cannot use peer operations",
                },
            )
        return session.mailbox_identity

    @staticmethod
    def _runtime_identity(session: Session) -> MailboxIdentity:
        identity = session.mailbox_identity
        if identity is None or not session.mailbox_registration:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "mailbox_registration_required",
                    "message": "a runtime registration credential is required",
                },
            )
        return identity

    async def _managed_mailbox(
        self, identity: MailboxIdentity
    ) -> tuple[WorkspaceManager, WorkspaceState]:
        """Prove the scoped identity names its configured primary mailbox agent."""
        try:
            persisted = await asyncio.to_thread(self._store.get, identity.address.workspace_id)
        except WorkspaceNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "mailbox workspace not found"},
            ) from exc
        # Registry creation stays on the loop: its registration hook schedules
        # loop-owned prebuild work. The manager read itself belongs off-loop.
        manager = self._registry.get(Path(persisted.repo_root))
        state = await asyncio.to_thread(manager.get, identity.address.workspace_id)
        # The RECORD says whether Grove owns this session, never the roster:
        # the flag was decided at create and a later config edit must not
        # turn a running owner away or admit a terminal workspace.
        if identity.address.agent or not state.native:
            raise HTTPException(
                status_code=404,
                detail={"error": "mailbox_unsupported", "message": "mailbox agent is not enabled"},
            )
        return manager, state

    async def _managed_primary_peers(self) -> list[MailboxPeer]:
        """Project current managed primary agents, never historical sessions."""
        states = await asyncio.to_thread(self._store.load_all)
        rows: list[MailboxPeer] = []
        for state in states:
            rows.append(
                MailboxPeer(
                    address=MailboxAddress(workspace_id=state.id),
                    display_name=state.title,
                    provider=state.agent_kind or "generic",
                    runtime=state.runtime,
                    can_receive=False,
                    # Off the persisted record, so no manager (and no config
                    # cascade) is resolved to answer a directory listing.
                    reason="not_registered" if state.native else "unsupported",
                )
            )
        return rows

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
            self._invalidate(address)

    def _invalidate(self, address: MailboxAddress) -> None:
        self._coordinator.invalidate(address)
        revocation = asyncio.create_task(
            asyncio.to_thread(self._auth_store.revoke_mailbox_sessions, address)
        )
        self._revocations.add(revocation)
        revocation.add_done_callback(self._revocations.discard)
        self._connections.pop(address, None)
        # Coordinator invalidation wakes an idle queue consumer. Let the ASGI
        # response finish normally rather than cancelling uvicorn's request task.
        addresses = self._addresses_by_workspace.get(address.workspace_id)
        if addresses is not None:
            addresses.discard(address)
            if not addresses:
                del self._addresses_by_workspace[address.workspace_id]

    async def _connection_stream(self, binding: MailboxBinding) -> AsyncIterator[str]:
        task = asyncio.current_task()
        if task is not None:
            self._connections[binding.identity.address] = task
        try:
            registered = json.dumps({"generation": binding.identity.generation})
            yield f"event: registered\ndata: {registered}\n\n"
            while True:
                try:
                    delivery = await self._coordinator.next_delivery(binding)
                except MailboxUnavailable:
                    return
                payload = json.dumps(
                    {"op": delivery.op, "message_id": delivery.message_id, "text": delivery.text}
                )
                yield f"event: delivery\ndata: {payload}\n\n"
        finally:
            self._coordinator.unregister(binding)
            self._connections.pop(binding.identity.address, None)
            addresses = self._addresses_by_workspace.get(binding.identity.address.workspace_id)
            if addresses is not None:
                addresses.discard(binding.identity.address)
                if not addresses:
                    del self._addresses_by_workspace[binding.identity.address.workspace_id]

    def _target_workspace(self, caller: MailboxIdentity, request: MailboxRequest) -> str:
        if request.kind == "send":
            return request.recipient.workspace_id
        receipt = self._coordinator.status(caller, request.reply_to)
        if receipt.sender is None:
            raise MailboxUnavailable("receipt_not_found", "reply has no mailbox sender")
        return receipt.sender.address.workspace_id

    @staticmethod
    def _http_for(exc: MailboxUnavailable) -> HTTPException:
        code = exc.code
        if code in {"receipt_not_found", "not_registered", "stale_recipient", "unsupported"}:
            status = 404
        elif code in {"scope_denied"}:
            status = 403
        elif code in {"invalid_receipt", "reply_unavailable", "too_large"}:
            status = 422
        else:
            status = 409
        return HTTPException(status_code=status, detail={"error": code, "message": str(exc)})


class CoordinatorSteerClient:
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

    def __init__(self, coordinator: MailboxCoordinator) -> None:
        self._coordinator = coordinator
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Adopt the loop the coordinator's queues live on (lifespan start).

        No owner can be registered before that loop runs, so a control that
        arrives unbound is honestly "no owner" rather than a wiring error.
        """
        self._loop = loop

    def owner_connected(self, state: WorkspaceState) -> bool:
        """Whether this workspace's owner worker is registered right now.

        The manager asks before deciding a steer needs a revive, so the answer
        must describe the COORDINATOR's registry rather than the record: a
        native workspace whose worker died is still `native`, and that is
        exactly the state worth reviving. Unbound (no loop yet at lifespan
        start) means no owner can have registered, which is honestly "no".
        """
        loop = self._loop
        if loop is None:
            return False
        if loop.is_running() and _current_loop() is not loop:
            future = asyncio.run_coroutine_threadsafe(
                _owner_present(self._coordinator, state.id), loop
            )
            return future.result(timeout=5)
        return self._coordinator.owner_for(state.id) is not None

    def send_message(self, state: WorkspaceState, text: str) -> None:
        self._control(state, "steer", text)

    def interrupt(self, state: WorkspaceState) -> None:
        self._control(state, "interrupt")

    def set_model(self, state: WorkspaceState, model: str) -> None:
        self._control(state, "set_model", model)

    def answer(self, state: WorkspaceState, plan: str) -> None:
        self._control(state, "answer", plan)

    def _control(self, state: WorkspaceState, op: ControlOp, text: str = "") -> None:
        def queue() -> None:
            self._coordinator.control(state.id, op, text)

        loop = self._loop
        try:
            if loop is None:
                raise MailboxUnavailable("not_registered", "no native owner is connected")
            if loop.is_running() and _current_loop() is not loop:
                asyncio.run_coroutine_threadsafe(_call(queue), loop).result(timeout=5)
            else:
                queue()
        except MailboxUnavailable as exc:
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


async def _owner_present(coordinator: MailboxCoordinator, workspace_id: str) -> bool:
    """Read the coordinator's registry ON its own loop (the queues live there)."""
    return coordinator.owner_for(workspace_id) is not None


__all__ = ["CoordinatorSteerClient", "MailboxRouter"]
