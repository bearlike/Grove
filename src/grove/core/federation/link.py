"""A node's half of the federation: join once, then re-register on every beat, forever.

Each heartbeat IS a full registration (name, address, every workspace this node
owns, whether it is draining). That makes the hub's index converge within one
beat of a create or a kill, and makes a hub restart need no special case: the
next beat simply rebuilds what the hub forgot.

The node keeps full authority over its workspaces; the hub only learns which
ones exist. So losing the hub costs a node nothing but visibility, and the link
behaves like the native worker's daemon connection: transport failures retry
with backoff from one second up to a ceiling and never end the link. Only the
hub saying it no longer holds this node's key does, because no retry can fix
that and a loop that kept knocking would be indistinguishable from an attack.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from loguru import logger

from grove.core.contracts.federation import (
    JoinRequest,
    JoinResponse,
    PingHopView,
    PingView,
    RegisterRequest,
)
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.federation.keys import NodeAuthRefused, NodeBearer, NodeKey

_RECONNECT_FLOOR_SECONDS = 1.0

LinkState = Literal["joining", "connected", "unreachable", "refused"]


class HubClient:
    """The HTTP calls a node makes to its hub. One instance per call site; cheap."""

    def __init__(
        self,
        hub_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._hub_url = hub_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    async def join(self, token: str, *, name: str, advertise_url: str) -> JoinResponse:
        body = JoinRequest(token=token, name=name, advertise_url=advertise_url)
        response = await self._post("/federation/join", body.model_dump(mode="json"), None)
        return JoinResponse.model_validate(response.json())

    async def register(self, key: tuple[str, str], request: RegisterRequest) -> None:
        await self._post("/federation/register", request.model_dump(mode="json"), key)

    async def ping(self, key: tuple[str, str] | None) -> PingView:
        """Walk resolve → connect → auth, stopping at the first hop that fails."""
        hops: list[PingHopView] = []
        host = urlsplit(self._hub_url).hostname or ""
        started = time.perf_counter()
        try:
            await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as exc:
            hops.append(PingHopView(hop="resolve", ok=False, detail=f"{host}: {exc}"))
            return PingView(hub_url=self._hub_url, hops=hops)
        hops.append(PingHopView(hop="resolve", ok=True, latency_ms=_ms(started), detail=host))
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                base_url=self._hub_url, timeout=self._timeout, transport=self._transport
            ) as client:
                await client.get("/healthz")
                hops.append(PingHopView(hop="connect", ok=True, latency_ms=_ms(started)))
                if key is None:
                    hops.append(
                        PingHopView(
                            hop="auth", ok=False, detail="node: no key; run grove node join"
                        )
                    )
                    return PingView(hub_url=self._hub_url, hops=hops)
                started = time.perf_counter()
                bearer = NodeBearer.sign(*key, audience="hub")
                response = await client.get(
                    "/federation/ping", headers={"Authorization": f"Bearer {bearer}"}
                )
        except httpx.HTTPError as exc:
            hops.append(PingHopView(hop="connect", ok=False, detail=f"{type(exc).__name__}: {exc}"))
            return PingView(hub_url=self._hub_url, hops=hops)
        ok = response.status_code == 200
        hops.append(
            PingHopView(
                hop="auth",
                ok=ok,
                latency_ms=_ms(started),
                detail="" if ok else _detail(response),
            )
        )
        return PingView(hub_url=self._hub_url, hops=hops)

    async def _post(
        self, path: str, body: dict[str, object], key: tuple[str, str] | None
    ) -> httpx.Response:
        headers = (
            {"Authorization": f"Bearer {NodeBearer.sign(*key, audience='hub')}"} if key else {}
        )
        async with httpx.AsyncClient(
            base_url=self._hub_url, timeout=self._timeout, transport=self._transport
        ) as client:
            response = await client.post(path, json=body, headers=headers)
        if response.status_code in (401, 403):
            raise NodeAuthRefused(_detail(response))
        response.raise_for_status()
        return response


@dataclass(slots=True)
class LinkStatus:
    """What `grove node status` reports about this node's link to its hub."""

    state: LinkState
    hub_url: str
    node_id: str | None
    last_contact: datetime | None = None
    last_error: str | None = None


class PeerLink:
    """Keep this node registered with its hub until the hub refuses its key."""

    def __init__(
        self,
        *,
        hub_url: str,
        name: str,
        advertise_url: str,
        key: NodeKey,
        join_token: Callable[[], str | None],
        workspaces: Callable[[], list[str]],
        projects: Callable[[], list[ProjectIdentity]],
        labels: dict[str, str],
        capacity: int | None,
        heartbeat_seconds: float,
        runtimes: tuple[str, ...] = (),
        reconnect_ceiling_seconds: float,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = HubClient(hub_url, timeout_seconds=timeout_seconds, transport=transport)
        self._name = name
        self._advertise_url = advertise_url
        self._key = key
        self._join_token = join_token
        self._workspaces = workspaces
        self._projects = projects
        self._labels = labels
        self._capacity = capacity
        self._runtimes = runtimes
        self.draining = False
        self._heartbeat = heartbeat_seconds
        self._ceiling = reconnect_ceiling_seconds
        self._wake = asyncio.Event()
        held = key.load()
        self.status = LinkStatus(
            state="joining" if held is None else "unreachable",
            hub_url=hub_url,
            node_id=held[0] if held else None,
        )

    @property
    def client(self) -> HubClient:
        return self._client

    @property
    def key(self) -> NodeKey:
        return self._key

    async def join(self, token: str) -> str:
        """Join the hub with ``token`` now, persist the key, and re-register at once."""
        joined = await self._client.join(token, name=self._name, advertise_url=self._advertise_url)
        await asyncio.to_thread(self._key.store, joined.node_id, joined.secret)
        self.status.node_id = joined.node_id
        self.reconnect()
        return joined.node_id

    def drain(self, draining: bool) -> None:
        """Refuse new creates (running workspaces finish) and tell the hub now."""
        self.draining = draining
        self.reconnect()

    def reconnect(self) -> None:
        """Drop the current attempt's wait and re-register now, in place."""
        self._wake.set()

    async def run(self) -> None:
        delay = _RECONNECT_FLOOR_SECONDS
        while True:
            try:
                key = await self._ensure_key()
                if key is None:
                    # Nothing to join with: nothing to retry until someone runs
                    # `grove node join`, which writes the key and reconnects.
                    await self._sleep(self._ceiling)
                    continue
                while True:
                    await self._register(key)
                    delay = _RECONNECT_FLOOR_SECONDS
                    await self._sleep(self._heartbeat)
            except NodeAuthRefused as exc:
                self.status.state = "refused"
                self.status.last_error = str(exc)
                logger.warning("federation link to {} refused: {}", self.status.hub_url, exc)
                return
            except (httpx.HTTPError, OSError, ValueError) as exc:
                self.status.state = "unreachable"
                self.status.last_error = f"{type(exc).__name__}: {exc}"
                logger.info(
                    "federation hub {} unreachable ({}); retrying in {:.0f}s",
                    self.status.hub_url,
                    type(exc).__name__,
                    delay,
                )
                await self._sleep(delay)
                delay = min(delay * 2, self._ceiling)

    async def _ensure_key(self) -> tuple[str, str] | None:
        held = self._key.load()
        if held is not None:
            return held
        token = self._join_token()
        if not token:
            self.status.state = "joining"
            self.status.last_error = "no node key and no join token"
            return None
        joined = await self._client.join(token, name=self._name, advertise_url=self._advertise_url)
        self._key.store(joined.node_id, joined.secret)
        self.status.node_id = joined.node_id
        return self._key.load()

    async def _register(self, key: tuple[str, str]) -> None:
        workspaces, projects = await asyncio.to_thread(
            lambda: (self._workspaces(), self._projects())
        )
        draining = self.draining
        await self._client.register(
            key,
            RegisterRequest(
                name=self._name,
                advertise_url=self._advertise_url,
                workspaces=workspaces,
                projects=projects,
                labels=self._labels,
                capacity=self._capacity,
                draining=draining,
                runtimes=list(self._runtimes),
            ),
        )
        self._contact()

    def _contact(self) -> None:
        self.status.state = "connected"
        self.status.last_contact = datetime.now(UTC)
        self.status.last_error = None

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error"))
    return str(detail or f"HTTP {response.status_code}")
