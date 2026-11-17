"""The hub's live view of its nodes: who is reachable, and who owns which workspace.

Metadata only, and in memory. A hub restart forgets every heartbeat and every
workspace owner, and the next registration from each node puts them back; the
one durable fact, which nodes hold a key, is :class:`NodeCatalogFile`'s.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from grove.core.contracts.federation import NodeView, RegisterRequest
from grove.core.errors import GroveError
from grove.core.federation.keys import NodeAuthRefused, NodeBearer, NodeCatalogFile, NodeRecord


@dataclass(slots=True)
class _Live:
    last_seen: float
    workspaces: frozenset[str] = field(default_factory=frozenset)
    projects: frozenset[str] = field(default_factory=frozenset)
    labels: Mapping[str, str] = field(default_factory=dict)
    capacity: int | None = None
    draining: bool = False
    runtimes: frozenset[str] = field(default_factory=frozenset)
    # Creates placed here and not yet confirmed. Kept apart from `workspaces`,
    # which every heartbeat replaces, so a beat landing mid-create cannot hand
    # the same last slot to a second create.
    pending: int = 0

    def load(self) -> int:
        return len(self.workspaces) + self.pending


class PlacementRefused(GroveError):
    """No node can take this create; the message says which filter emptied the field."""


class OwnerUnreachable(GroveError):
    """The workspace belongs to a node the hub cannot currently reach."""


class Hub:
    """Admit nodes, verify their calls, and answer "which node owns this workspace"."""

    def __init__(
        self,
        catalog: NodeCatalogFile,
        *,
        unreachable_after_seconds: float,
        max_clock_skew_seconds: float,
        join_token_ttl: timedelta,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.catalog = catalog
        self._unreachable_after = unreachable_after_seconds
        self._skew = max_clock_skew_seconds
        self._join_ttl = join_token_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._live: dict[str, _Live] = {}

    # ─── keys ───────────────────────────────────────────────────────────────

    def mint_join_token(self) -> str:
        return self.catalog.mint_join_token(self._join_ttl)

    def join(self, token: str, *, name: str, advertise_url: str) -> tuple[NodeRecord, str]:
        return self.catalog.admit(token, name=name, advertise_url=advertise_url)

    def authenticate(self, bearer: str) -> NodeRecord:
        """The node a bearer belongs to; refuses an unknown node or a bad signature."""
        node = self.catalog.node(NodeBearer.node_id(bearer))
        if node is None:
            raise NodeAuthRefused("this hub holds no key for that node")
        NodeBearer.verify(bearer, node.signing_key, audience="hub", max_skew_seconds=self._skew)
        return node

    def forget(self, node_id: str) -> bool:
        with self._lock:
            self._live.pop(node_id, None)
        return self.catalog.forget(node_id)

    # ─── liveness and the index ─────────────────────────────────────────────

    def register(self, node: NodeRecord, request: RegisterRequest) -> None:
        """Replace everything the hub knew about ``node`` with what it just said.

        Every heartbeat is one of these, so this is also the liveness refresh.
        """
        self.catalog.update_address(
            node.node_id, name=request.name, advertise_url=request.advertise_url
        )
        with self._lock:
            previous = self._live.get(node.node_id)
            self._live[node.node_id] = _Live(
                last_seen=self._clock(),
                workspaces=frozenset(request.workspaces),
                projects=frozenset(project.remote for project in request.projects),
                labels=dict(request.labels),
                capacity=request.capacity,
                draining=request.draining,
                runtimes=frozenset(request.runtimes),
                pending=previous.pending if previous else 0,
            )

    def owner(self, workspace_id: str) -> NodeRecord | None:
        """The node that owns ``workspace_id``; raises when it is known but unreachable.

        ``None`` means no node claims it, so the hub's own routes answer. An
        unreachable owner is a distinct, typed answer rather than that fall-through,
        because "the hub has never heard of it" and "its node is out of sight" take
        different actions.
        """
        with self._lock:
            claims = [
                (node_id, live)
                for node_id, live in self._live.items()
                if workspace_id in live.workspaces
            ]
            reachable = [node_id for node_id, live in claims if self._reachable(live)]
            stale = [(node_id, live.last_seen) for node_id, live in claims]
        if reachable:
            return self.catalog.node(reachable[0])
        if stale:
            node_id, last_seen = stale[0]
            record = self.catalog.node(node_id)
            seen = datetime.fromtimestamp(last_seen, UTC).isoformat(timespec="seconds")
            name = record.name if record else node_id
            raise OwnerUnreachable(f"workspace {workspace_id} is on node {name}, last seen {seen}")
        return None

    def place(
        self,
        project: str,
        selector: Mapping[str, str],
        *,
        node: str | None = None,
        runtime: str | None = None,
    ) -> NodeRecord:
        """Reserve a slot on the least-loaded node that fits; :meth:`settle` confirms it.

        Filters in the order an operator would debug them (the named node,
        reachable, not draining, holds the project, configures the runtime,
        carries every selected label, under its declared capacity) and refuses
        naming the first filter that left nothing, before anything is forwarded.
        Load is workspaces over capacity, so a big node and a small node fill
        evenly; an undeclared capacity counts as unbounded.

        Args:
            project: The project's cross-node name.
            selector: Labels the node must carry.
            node: The one node name the create may land on, or any.
            runtime: The resident ``runtimes.<name>`` the node must configure,
                or none for a host or container create.
        """
        names = {record.node_id: record.name for record in self.catalog.nodes()}
        filters: list[tuple[str, Callable[[str, _Live], bool]]] = [
            (
                f"no node is named {node}",
                lambda node_id, _: node is None or names.get(node_id) == node,
            ),
            ("no reachable node", lambda _, live: self._reachable(live)),
            ("every reachable node is draining", lambda _, live: not live.draining),
            (f"no node holds project {project}", lambda _, live: project in live.projects),
            (
                f"no node configures runtime {runtime}",
                lambda _, live: runtime is None or runtime in live.runtimes,
            ),
            (
                f"no node carries labels {dict(selector)}",
                lambda _, live: all(live.labels.get(k) == v for k, v in selector.items()),
            ),
            (
                "every fitting node is at capacity",
                lambda _, live: live.capacity is None or live.load() < live.capacity,
            ),
        ]
        with self._lock:
            field = list(self._live.items())
            for reason, keep in filters:
                field = [(node_id, live) for node_id, live in field if keep(node_id, live)]
                if not field:
                    raise PlacementRefused(f"cannot place a workspace: {reason}")
            node_id, live = min(
                field,
                key=lambda item: (
                    item[1].load() / item[1].capacity if item[1].capacity else 0.0,
                    item[1].load(),
                    item[0],
                ),
            )
            live.pending += 1
        record = self.catalog.node(node_id)
        if record is None:
            self.settle(node_id, None)
            raise PlacementRefused("cannot place a workspace: the chosen node was just forgotten")
        return record

    def settle(self, node_id: str, workspace_id: str | None) -> None:
        """Release a :meth:`place` reservation, recording the new workspace if it was made."""
        with self._lock:
            live = self._live.get(node_id)
            if live is None:
                return
            live.pending = max(0, live.pending - 1)
            if workspace_id is not None:
                live.workspaces = live.workspaces | {workspace_id}

    def nodes(self) -> list[NodeView]:
        with self._lock:
            live = dict(self._live)
        return [
            NodeView(
                node_id=record.node_id,
                name=record.name,
                advertise_url=record.advertise_url,
                state="reachable"
                if (entry := live.get(record.node_id)) and self._reachable(entry)
                else "unreachable",
                last_seen_at=(
                    datetime.fromtimestamp(entry.last_seen, UTC) if entry is not None else None
                ),
                workspaces=len(entry.workspaces) if entry else 0,
                draining=entry.draining if entry else False,
                labels=dict(entry.labels) if entry else {},
                capacity=entry.capacity if entry else None,
                runtimes=sorted(entry.runtimes) if entry else [],
            )
            for record in self.catalog.nodes()
        ]

    def has_nodes(self) -> bool:
        """Whether any node has ever registered with this hub since it started."""
        with self._lock:
            return bool(self._live)

    def reachable_nodes(self) -> list[NodeRecord]:
        with self._lock:
            ids = [node_id for node_id, live in self._live.items() if self._reachable(live)]
        return [node for node_id in ids if (node := self.catalog.node(node_id)) is not None]

    def _reachable(self, live: _Live) -> bool:
        return self._clock() - live.last_seen <= self._unreachable_after
