"""Two daemons talking: ``/federation/*`` routes and the forward-by-owner proxy.

A SEPARATE surface with its own dependency, :func:`FederationRouter.require_node`.
Daemon-to-daemon identity is the per-node key and nothing else, so no route here
accepts a session bearer and no session route accepts a node bearer.

**Forwarding is middleware, not a route per verb.** Every workspace verb lives
under ``/workspaces/{id}``, so one ASGI layer that recognises a remote owner
covers every verb that exists and every verb added later, streams included. On
the hub it authenticates the CALLER first (the hub's own session gate, so a
remote workspace is never more reachable than a local one), then proxies to the
owner's ``/federation/forward/…`` under a hub-signed bearer. On the node that
prefix is the only way in from the hub: the node checks the signature, rewrites
the path back to ``/workspaces/…`` and stamps a node-local session bearer, so the
verb then runs through the node's ordinary routes and ordinary auth.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import timedelta
from functools import partial
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ValidationError
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_409_CONFLICT
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from grove.core.auth import SessionStore
from grove.core.config import FederationConfig, GroveConfig
from grove.core.contracts.federation import (
    JoinRequest,
    JoinResponse,
    NodeDrainRequest,
    NodeIdentityView,
    NodeJoinRequest,
    NodeSelfView,
    NodeView,
    PingView,
    RegisterRequest,
)
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.federation import Hub, NodeAuthRefused, NodeBearer, NodeKey, NodeRecord, PeerLink
from grove.core.federation.hub import OwnerUnreachable, PlacementRefused
from grove.core.registry import RepoRegistry
from grove.core.workspace import Runtime

_WORKSPACE_PATH = re.compile(r"^/workspaces/([a-f0-9]{32})(/.*)?$")
_FORWARD_PREFIX = "/federation/forward"
# Hop-by-hop headers never cross a proxy; the rest go through untouched.
_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"transfer-encoding",
        b"te",
        b"trailer",
        b"upgrade",
        b"host",
        b"content-length",
        b"authorization",
    }
)


def _envelope(code: str, message: str) -> dict[str, str]:
    return {"error": code, "message": message}


class JoinTokenView(BaseModel):
    token: str
    expires_in_seconds: int


class FederationRouter:
    """The ``/federation/*`` routes, plus the human-facing ``/nodes`` reads."""

    def __init__(
        self,
        cfg: FederationConfig,
        *,
        hub: Hub | None,
        link: PeerLink | None,
        node_key: NodeKey | None,
        auth_dep: Callable[..., object],
    ) -> None:
        self._cfg = cfg
        self.hub = hub
        self.link = link
        self.node_key = node_key
        self._auth_dep = auth_dep

    async def require_node(self, request: Request) -> NodeRecord:
        """The node a hub-bound call came from. 401 names which side refused."""
        if self.hub is None:
            raise HTTPException(HTTP_403_FORBIDDEN, _envelope("not_a_hub", "this daemon is no hub"))
        header = request.headers.get("authorization", "")
        bearer = header[len("Bearer ") :].strip() if header.lower().startswith("bearer ") else ""
        try:
            return self.hub.authenticate(bearer)
        except NodeAuthRefused as exc:
            raise HTTPException(
                HTTP_401_UNAUTHORIZED, _envelope("node_key_refused", f"hub: {exc}")
            ) from exc

    def router(self) -> APIRouter:
        router = APIRouter(tags=["federation"])
        node_dep = Depends(self.require_node)
        auth = Depends(self._auth_dep)

        @router.post("/federation/join", response_model=JoinResponse)
        async def join(body: JoinRequest) -> JoinResponse:
            hub = self._require_hub()
            try:
                node, secret = await asyncio.to_thread(
                    hub.join, body.token, name=body.name, advertise_url=body.advertise_url
                )
            except NodeAuthRefused as exc:
                raise HTTPException(
                    HTTP_401_UNAUTHORIZED, _envelope("join_token_refused", f"hub: {exc}")
                ) from exc
            logger.info("federation: node {} ({}) joined", node.node_id, node.name)
            return JoinResponse(node_id=node.node_id, secret=secret)

        @router.post("/federation/register", status_code=204)
        async def register(body: RegisterRequest, node: NodeRecord = node_dep) -> None:
            await asyncio.to_thread(self._require_hub().register, node, body)

        @router.get("/federation/ping", response_model=NodeIdentityView)
        async def ping(request: Request) -> NodeIdentityView:
            """Prove the key in either direction: a node pings its hub, a hub its node."""
            role = self._cfg.node.role or "node"
            if role == "hub":
                node = await self.require_node(request)
                return NodeIdentityView(role="hub", node_id=node.node_id, name=self._name())
            held = await asyncio.to_thread(self.node_key.load) if self.node_key else None
            _verify_hub_call(request, held, skew=self._cfg.node.max_clock_skew_seconds)
            return NodeIdentityView(
                role="node", node_id=held[0] if held else None, name=self._name()
            )

        @router.get("/nodes/self", response_model=NodeSelfView, dependencies=[auth])
        async def node_self() -> NodeSelfView:
            if self.link is None:
                return NodeSelfView(role="hub", node_id=None, name=self._name())
            status = self.link.status
            return NodeSelfView(
                role="node",
                node_id=status.node_id,
                name=self._name(),
                hub_url=status.hub_url,
                link=status.state,
                last_contact_at=status.last_contact,
                last_error=status.last_error,
                draining=self.link.draining,
            )

        @router.post("/nodes/self/join", response_model=NodeSelfView, dependencies=[auth])
        async def node_join(body: NodeJoinRequest) -> NodeSelfView:
            link = self._require_link()
            try:
                await link.join(body.token)
            except NodeAuthRefused as exc:
                raise HTTPException(
                    HTTP_401_UNAUTHORIZED, _envelope("join_token_refused", str(exc))
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    502, _envelope("hub_unreachable", f"{type(exc).__name__}: {exc}")
                ) from exc
            return await node_self()

        @router.post("/nodes/self/drain", response_model=NodeSelfView, dependencies=[auth])
        async def node_drain(body: NodeDrainRequest) -> NodeSelfView:
            self._require_link().drain(body.draining)
            return await node_self()

        @router.post("/nodes/self/reconnect", status_code=204, dependencies=[auth])
        async def node_reconnect() -> None:
            self._require_link().reconnect()

        @router.post("/nodes/self/ping", response_model=PingView, dependencies=[auth])
        async def node_ping() -> PingView:
            link = self._require_link()
            return await link.client.ping(await asyncio.to_thread(link.key.load))

        @router.get("/nodes", response_model=list[NodeView], dependencies=[auth])
        async def nodes() -> list[NodeView]:
            return await asyncio.to_thread(self._require_hub().nodes)

        @router.post("/nodes/token", response_model=JoinTokenView, dependencies=[auth])
        async def mint_token() -> JoinTokenView:
            token = await asyncio.to_thread(self._require_hub().mint_join_token)
            return JoinTokenView(
                token=token, expires_in_seconds=self._cfg.node.join_token_ttl_seconds
            )

        @router.delete("/nodes/{node_id}", status_code=204, dependencies=[auth])
        async def forget(node_id: str) -> None:
            if not await asyncio.to_thread(self._require_hub().forget, node_id):
                raise HTTPException(404, _envelope("node_not_found", f"no node {node_id}"))

        return router

    def _require_link(self) -> PeerLink:
        if self.link is None:
            raise HTTPException(
                HTTP_409_CONFLICT, _envelope("not_a_node", "this daemon is no federation node")
            )
        return self.link

    def _require_hub(self) -> Hub:
        if self.hub is None:
            raise HTTPException(HTTP_409_CONFLICT, _envelope("not_a_hub", "this daemon is no hub"))
        return self.hub

    def _name(self) -> str:
        return self._cfg.node.name or socket.gethostname()


def _verify_hub_call(request: Request, held: tuple[str, str] | None, *, skew: float) -> str:
    """A call claiming to come from this node's hub; refuses anything else."""
    if held is None:
        raise HTTPException(
            HTTP_401_UNAUTHORIZED,
            _envelope("node_key_refused", "node: no key; run grove node join"),
        )
    header = request.headers.get("authorization", "")
    bearer = header[len("Bearer ") :].strip() if header.lower().startswith("bearer ") else ""
    try:
        return NodeBearer.verify(bearer, held[1], audience="node", max_skew_seconds=skew)
    except NodeAuthRefused as exc:
        raise HTTPException(
            HTTP_401_UNAUTHORIZED, _envelope("node_key_refused", f"node: {exc}")
        ) from exc


class FederationProxy:
    """Pure ASGI: forward a remote-owned workspace verb, or admit one from the hub."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        hub: Hub | None,
        node_key: NodeKey | None,
        link: PeerLink | None,
        authenticate_caller: Callable[[Request], Awaitable[object]],
        session_store: SessionStore,
        resolve_create: Callable[[CreateWorkspaceRequest], tuple[ProjectIdentity, dict[str, str]]],
        timeout_seconds: float,
        max_clock_skew_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.app = app
        self._transport = transport
        self._hub = hub
        self._node_key = node_key
        self._link = link
        self._authenticate = authenticate_caller
        self._resolve_create = resolve_create
        self._sessions = session_store
        self._timeout = timeout_seconds
        self._skew = max_clock_skew_seconds
        self._forward_token: str | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
        elif self._node_key is not None and scope["path"].startswith(_FORWARD_PREFIX + "/"):
            await self._admit(scope, receive, send)
        elif self._link is not None and self._link.draining and _is_create(scope):
            await _json(
                send,
                HTTP_409_CONFLICT,
                {"detail": _envelope("node_draining", "node: draining; no new workspaces")},
            )
        elif self._hub is None:
            await self.app(scope, receive, send)
        else:
            await self._route(self._hub, scope, receive, send)

    async def _route(self, hub: Hub, scope: Scope, receive: Receive, send: Send) -> None:
        """Hub side: forward what a node owns, union what spans nodes, keep the rest."""
        try:
            await self._dispatch(hub, scope, receive, send)
        except OwnerUnreachable as exc:
            # Gated like the forward it stands in for: who owns a workspace is
            # not something an anonymous caller may learn.
            try:
                await self._authenticate(Request(scope))
            except HTTPException as refused:
                await _json(send, refused.status_code, {"detail": refused.detail}, refused.headers)
                return
            await _json(send, 503, {"detail": _envelope("owner_unreachable", f"hub: {exc}")})

    async def _dispatch(self, hub: Hub, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope["path"]
        if match := _WORKSPACE_PATH.match(path):
            owner = await asyncio.to_thread(hub.owner, match.group(1))
            if owner is not None:
                await self._forward(owner, scope, await _body(receive), send)
                return
        elif (scope["method"], path) == ("POST", "/workspaces"):
            await self._place(hub, scope, await _body(receive), send)
            return
        elif (scope["method"], path) in _ADDRESSED_WRITES:
            body = await _body(receive)
            owner = await asyncio.to_thread(hub.owner, _recipient(body))
            if owner is not None:
                await self._forward(owner, scope, body, send)
            else:
                await self.app(scope, _replay(body), send)
            return
        elif scope["method"] == "GET" and path in _UNION_READS:
            await self._union(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _place(self, hub: Hub, scope: Scope, body: bytes, send: Send) -> None:
        """Hub side: a create lands on the least-loaded node that fits, or is refused.

        With no node registered at all, the hub is a plain daemon and creates
        locally. Otherwise every create goes to a node: the hub keeps metadata,
        not workspaces.
        """
        try:
            await self._authenticate(Request(scope))
        except HTTPException as exc:
            await _json(send, exc.status_code, {"detail": exc.detail}, exc.headers)
            return
        if not await asyncio.to_thread(hub.has_nodes):
            await self.app(scope, _replay(body), send)
            return
        try:
            request = CreateWorkspaceRequest.model_validate_json(body)
            identity, selector = await asyncio.to_thread(self._resolve_create, request)
            node = await asyncio.to_thread(
                partial(
                    hub.place,
                    identity.remote,
                    selector,
                    node=request.node,
                    runtime=request.remote_runtime if request.runtime is Runtime.REMOTE else None,
                )
            )
        except ValidationError as exc:
            await _json(send, 422, {"detail": _envelope("invalid_request", str(exc))})
            return
        except GroveError as exc:
            await _json(send, 409, {"detail": _envelope("placement_refused", f"hub: {exc}")})
            return
        forwarded = request.model_copy(update={"repo_root": None, "project": identity})
        created: list[str | None] = [None]
        try:
            await self._forward(
                node,
                scope,
                forwarded.model_dump_json(exclude_none=True).encode(),
                _observing(send, created),
            )
        finally:
            hub.settle(node.node_id, created[0])

    async def _union(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Hub side: the local answer merged with every reachable node's."""
        try:
            await self._authenticate(Request(scope))
        except HTTPException as exc:
            await _json(send, exc.status_code, {"detail": exc.detail}, exc.headers)
            return
        local = await _capture(self.app, scope, receive)
        if local[0] != 200:
            await _json(send, local[0], local[1])
            return
        nodes = await asyncio.to_thread(self._hub.reachable_nodes) if self._hub else []
        remote = await asyncio.gather(*(self._fetch(node, scope) for node in nodes))
        parts = [part for part in remote if part is not None]
        merge = _UNION_READS[scope["path"]]
        query = scope.get("query_string", b"").decode("latin-1")
        await _json(send, 200, merge(local[1], parts, query))

    async def _fetch(self, node: NodeRecord, scope: Scope) -> Any:
        """One node's answer to the same read, or ``None`` when it could not give one."""
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{node.advertise_url.rstrip('/')}{_FORWARD_PREFIX}{scope['path']}"
        bearer = NodeBearer.sign(node.node_id, node.signing_key, audience="node")
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                response = await c.get(
                    url + (f"?{query}" if query else ""),
                    headers={"Authorization": f"Bearer {bearer}"},
                )
        except httpx.HTTPError as exc:
            logger.info("federation: {} skipped in union ({})", node.name, type(exc).__name__)
            return None
        if response.status_code != 200:
            logger.info(
                "federation: {} skipped in union (HTTP {})", node.name, response.status_code
            )
            return None
        return response.json()

    async def _admit(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Node side: a hub-signed forward becomes an ordinary local request."""
        held = await asyncio.to_thread(self._node_key.load) if self._node_key else None
        try:
            _verify_hub_call(Request(scope), held, skew=self._skew)
        except HTTPException as exc:
            await _json(send, exc.status_code, {"detail": exc.detail})
            return
        local = dict(scope)
        local["path"] = scope["path"][len(_FORWARD_PREFIX) :]
        local["raw_path"] = local["path"].encode()
        token = await asyncio.to_thread(self._local_bearer)
        local["headers"] = [
            (k, v) for k, v in scope["headers"] if k.lower() != b"authorization"
        ] + [(b"authorization", f"Bearer {token}".encode())]
        await self.app(local, receive, send)

    def _local_bearer(self) -> str:
        """One same-host session for every forwarded verb, re-minted if it lapses."""
        if self._forward_token is not None:
            with suppress(GroveError):
                self._sessions.validate(self._forward_token)
                return self._forward_token
        self._forward_token = self._sessions.mint_local("federation-forward")
        return self._forward_token

    async def _forward(self, owner: NodeRecord, scope: Scope, body: bytes, send: Send) -> None:
        """Hub side: the caller must pass the hub's own gate before anything is sent on."""
        try:
            await self._authenticate(Request(scope))
        except HTTPException as exc:
            await _json(send, exc.status_code, {"detail": exc.detail}, exc.headers)
            return
        headers = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in scope["headers"]
            if k.lower() not in _HOP_BY_HOP
        ]
        headers.append(
            (
                "authorization",
                f"Bearer {NodeBearer.sign(owner.node_id, owner.signing_key, audience='node')}",
            )
        )
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{owner.advertise_url.rstrip('/')}{_FORWARD_PREFIX}{scope['path']}"
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, read=None), transport=self._transport
        )
        try:
            upstream = await client.send(
                client.build_request(
                    scope["method"],
                    url + (f"?{query}" if query else ""),
                    headers=headers,
                    content=body,
                ),
                stream=True,
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            await _json(
                send,
                502,
                {
                    "detail": _envelope(
                        "node_unreachable", f"node {owner.name}: {type(exc).__name__}"
                    )
                },
            )
            return
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": [
                        (k.encode("latin-1"), v.encode("latin-1"))
                        for k, v in upstream.headers.items()
                        if k.lower().encode() not in _HOP_BY_HOP
                    ],
                }
            )
            async for chunk in upstream.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await upstream.aclose()
            await client.aclose()


async def _body(receive: Receive) -> bytes:
    body = b""
    more = True
    while more:
        message = await receive()
        body += message.get("body", b"")
        more = message.get("more_body", False)
    return body


def _replay(body: bytes) -> Receive:
    """A ``receive`` that hands the already-read body to the next app, once."""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _observing(send: Send, created: list[str | None]) -> Send:
    """Pass a forwarded create's response through, noting the new workspace's id."""
    status = [0]
    chunks: list[bytes] = []

    async def observe(message: Message) -> None:
        if message["type"] == "http.response.start":
            status[0] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False) and status[0] == 200:
                with suppress(ValueError, AttributeError):
                    created[0] = json.loads(b"".join(chunks)).get("id")
        await send(message)

    return observe


def _is_create(scope: Scope) -> bool:
    """Only a NEW workspace is refused while draining; every verb on a running one works."""
    path: str = scope["path"]
    return bool(scope["method"] == "POST" and path.removeprefix(_FORWARD_PREFIX) == "/workspaces")


def _recipient(body: bytes) -> str:
    """The workspace an addressed write names, or ``""`` when it names none parseable."""
    try:
        recipient = json.loads(body).get("recipient") or {}
    except (ValueError, AttributeError):
        return ""
    workspace = recipient.get("workspace_id") if isinstance(recipient, dict) else None
    return workspace if isinstance(workspace, str) else ""


async def _capture(app: ASGIApp, scope: Scope, receive: Receive) -> tuple[int, Any]:
    """Run ``app`` in-process and return ``(status, parsed JSON body)``."""
    status = 500
    chunks: list[bytes] = []

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    # Uncompressed, so the merge can read it; gzip sits inside this layer.
    plain = dict(scope)
    plain["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"accept-encoding"]
    await app(plain, receive, send)
    raw = b"".join(chunks)
    return status, json.loads(raw) if raw else None


def _merge_contacts(local: Any, remote: list[Any], _query: str) -> Any:
    merged = dict(local)
    merged["contacts"] = [c for part in [local, *remote] for c in part.get("contacts", [])]
    merged["body_limit_bytes"] = min(
        part.get("body_limit_bytes", local["body_limit_bytes"]) for part in [local, *remote]
    )
    return merged


def _merge_digest(local: Any, remote: list[Any], query: str) -> Any:
    """Rows and totals summed across daemons; paging and change cursors stay LOCAL.

    A cursor names one daemon's projection generation, so no single token can
    resume a union. With any node contributing, the hub answers WHOLE and says
    so: no ``next_page``, and ``resync_required`` when the caller sent ``since``,
    the existing contract for "discard what you held and read again".
    """
    if not remote:
        return local
    merged = dict(local)
    merged["rows"] = [row for part in [local, *remote] for row in part.get("rows", [])]
    merged["removed"] = []
    merged["next_page"] = None
    merged["resync_required"] = "since=" in query
    merged["degraded_repos"] = [
        repo for part in [local, *remote] for repo in part.get("degraded_repos", [])
    ]
    totals = dict(local["totals"])
    for part in remote:
        other = part["totals"]
        totals["workspaces"] += other.get("workspaces", 0)
        for bucket in ("by_status", "by_agent", "by_phase", "by_flag"):
            summed = dict(totals.get(bucket, {}))
            for key, count in other.get(bucket, {}).items():
                summed[key] = summed.get(key, 0) + count
            totals[bucket] = summed
    merged["totals"] = totals
    return merged


# A write whose BODY names the workspace it is for, rather than its path.
_ADDRESSED_WRITES = frozenset({("POST", "/mailboxes/messages"), ("POST", "/watches")})
# A read the hub answers as the union of itself and every reachable node.
_UNION_READS: dict[str, Callable[[Any, list[Any], str], Any]] = {
    "/mailboxes/contacts": _merge_contacts,
    "/fleet/digest": _merge_digest,
}


async def _json(
    send: Send, status: int, payload: Any, extra: Mapping[str, str] | None = None
) -> None:
    data = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    start: Message = {"type": "http.response.start", "status": status, "headers": headers}
    await send(start)
    await send({"type": "http.response.body", "body": data})


def create_resolver(
    registry: RepoRegistry, cfg: GroveConfig
) -> Callable[[CreateWorkspaceRequest], tuple[ProjectIdentity, dict[str, str]]]:
    """How a hub names a create's project and the labels it requires.

    The project is the request's ``project``, else the identity of the hub's own
    ``repo_root``. The selector is the request's, else that repo's cascade
    ``scheduling.require`` (a committed project config may set it), else the
    hub's global default.
    """

    def resolve(request: CreateWorkspaceRequest) -> tuple[ProjectIdentity, dict[str, str]]:
        identity = request.project
        selector = request.selector
        if request.repo_root is not None:
            identity = identity or registry.identity(request.repo_root)
            if selector is None:
                selector = dict(registry.get(request.repo_root).config.scheduling.require)
        if identity is None:
            raise PlacementRefused(
                "cannot place a workspace: name a project, or a repo_root with a hosted remote"
            )
        return identity, dict(cfg.scheduling.require if selector is None else selector)

    return resolve


def build_hub(cfg: FederationConfig) -> Hub | None:
    if not (cfg.enabled and cfg.node.role == "hub"):
        return None
    from grove.core.federation import NodeCatalogFile  # noqa: PLC0415

    return Hub(
        NodeCatalogFile(),
        unreachable_after_seconds=cfg.node.unreachable_after_seconds,
        max_clock_skew_seconds=cfg.node.max_clock_skew_seconds,
        join_token_ttl=timedelta(seconds=cfg.node.join_token_ttl_seconds),
    )


class Federation:
    """Everything one daemon runs for federation, built once from config.

    ``None`` from :meth:`build` means federation is off and the app is
    byte-identical to one without this module.
    """

    def __init__(
        self,
        cfg: FederationConfig,
        *,
        hub: Hub | None,
        link: PeerLink | None,
        node_key: NodeKey | None,
        auth_dep: Callable[..., object],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.routes = FederationRouter(
            cfg, hub=hub, link=link, node_key=node_key, auth_dep=auth_dep
        )
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def build(
        cls,
        cfg: FederationConfig,
        *,
        auth_dep: Callable[..., object],
        workspaces: Callable[[], list[str]],
        projects: Callable[[], list[ProjectIdentity]],
        runtimes: tuple[str, ...] = (),
    ) -> Federation | None:
        if not cfg.enabled:
            return None
        hub = build_hub(cfg)
        if hub is not None:
            return cls(cfg, hub=hub, link=None, node_key=None, auth_dep=auth_dep)
        key = NodeKey()
        link = build_link(cfg, key=key, workspaces=workspaces, projects=projects, runtimes=runtimes)
        return cls(cfg, hub=None, link=link, node_key=key, auth_dep=auth_dep)

    @staticmethod
    def mount(
        federation: Federation | None,
        app: FastAPI,
        *,
        session_store: SessionStore,
        authenticate_caller: Callable[[Request], Awaitable[object]],
        resolve_create: Callable[[CreateWorkspaceRequest], tuple[ProjectIdentity, dict[str, str]]],
    ) -> None:
        """Add the routes and the proxy; a disabled federation adds nothing at all."""
        if federation is None:
            return
        app.include_router(federation.routes.router())
        # Added after gzip, so it runs outside it: a forwarded verb is proxied
        # before compression and the node's own encoding passes through.
        app.add_middleware(
            FederationProxy,
            hub=federation.routes.hub,
            node_key=federation.routes.node_key,
            link=federation.routes.link,
            authenticate_caller=authenticate_caller,
            session_store=session_store,
            resolve_create=resolve_create,
            timeout_seconds=federation.cfg.node.request_timeout_seconds,
            max_clock_skew_seconds=federation.cfg.node.max_clock_skew_seconds,
            transport=federation.transport,
        )

    def start(self) -> None:
        if self.routes.link is not None:
            self._task = asyncio.create_task(self.routes.link.run(), name="grove-federation-link")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def build_link(
    cfg: FederationConfig,
    *,
    key: NodeKey,
    workspaces: Callable[[], list[str]],
    projects: Callable[[], list[ProjectIdentity]],
    runtimes: tuple[str, ...] = (),
) -> PeerLink:
    node = cfg.node
    return PeerLink(
        hub_url=node.hub_url,
        name=node.name or socket.gethostname(),
        advertise_url=node.advertise_url,
        key=key,
        join_token=lambda: os.environ.get(node.join_token_env) if node.join_token_env else None,
        workspaces=workspaces,
        projects=projects,
        labels=dict(node.labels),
        capacity=node.capacity,
        runtimes=runtimes,
        heartbeat_seconds=node.heartbeat_seconds,
        reconnect_ceiling_seconds=node.reconnect_ceiling_seconds,
        timeout_seconds=node.request_timeout_seconds,
    )
