"""Event-owned assignment reconciliation for the assignee work queue.

The old timer called every configured provider and scanned every workspace on
repeat.  That made an unchanged fleet perform unbounded discovery, while a
slow ``create`` accumulated timer jobs behind it.  ``AssigneePoller`` now has
three explicit inputs instead:

* :meth:`bootstrap` is an operator-controlled reconciliation for tracker
  providers without push support.  It is never armed as a timer.
* :meth:`handle_ticket_event` receives an authenticated-forwarder-normalized
  Gitea/GitHub assignment wake-up and re-reads only its authoritative ticket.
* :meth:`handle_lifecycle_event` maintains the holder index from Grove
  lifecycle edges, releasing only assignments this process made.

The optional ``bind(subscribe)`` accepts one narrow manager-subscription
callable.  There is no package event bus: the daemon supplies
``RepoRegistry.subscribe_managers`` and each manager's existing ``subscribe``.
The subscriber callback merely admits a compact lifecycle wake-up; expensive
provider reads and provisioning run on the dedicated bounded worker.
"""

from __future__ import annotations

import contextlib
import itertools
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.assignment_events import (
    AssignmentLifecycleEvent,
    AssignmentTicketEvent,
    AssignmentTicketIdentity,
)
from grove.core.contracts.tickets import TicketKind, TicketProviderName, TicketRef
from grove.core.errors import GroveError
from grove.core.issueops.handover import HandoverLog
from grove.core.issueops.pickup import PickupCandidate, PickupEngine, PickupPlan

if TYPE_CHECKING:
    from grove.core.config import IssueOpsConfig
    from grove.core.manager import WorkspaceEvent, WorkspaceManager
    from grove.core.registry import RepoRegistry
    from grove.core.tickets.provider import TicketProvider

# The provider name is not global: two repositories can point a name at distinct
# hosts and credentials. It is retained for compatibility with callers that
# inspect the internal assignment set in tests.
_ProviderKey = tuple[str, TicketProviderName]
_SubscribeManagers = Callable[[Callable[..., None]], Callable[[], None]]
_AssignmentEvent = AssignmentTicketEvent | AssignmentLifecycleEvent


@dataclass(frozen=True, slots=True)
class _Assigned:
    """Ticket coordinates Grove may later release, plus its indexed holder."""

    repo_root: str
    provider: TicketProviderName
    ticket_id: str
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Work:
    """One admitted event. Reservations remain held through provisioning."""

    event: _AssignmentEvent
    size_bytes: int


class AssigneePoller:
    """Own assignee reconciliation from explicit tracker and lifecycle edges.

    The public class name remains for daemon and plugin compatibility. It no
    longer polls periodically: ``tick`` is the explicit manual reconcile for a
    no-push provider, and normal operation is ``handle_ticket_event`` plus
    ``handle_lifecycle_event``.
    """

    def __init__(
        self,
        *,
        config: IssueOpsConfig,
        registry: RepoRegistry,
        engine: PickupEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = config
        self._registry = registry
        self._engine = engine if engine is not None else PickupEngine(log=HandoverLog())
        self._clock = clock if clock is not None else self._utcnow
        self._lock = Lock()
        # The only assignment ownership authority. A ticket entering through
        # publish-edge assign_now lands here too, so a later lifecycle kill can
        # release it without a fleet sweep.
        self._assigned: dict[str, _Assigned] = {}
        # A bounded LRU metadata index carries replay and ordering state only for
        # keys that are still recent. A key's delivery ids are capped as well.
        self._generation: OrderedDict[str, int] = OrderedDict()
        self._delivery_ids: OrderedDict[str, list[str]] = OrderedDict()
        self._metadata_capacity = 256
        self._delivery_capacity = 32
        self._backoff: dict[_ProviderKey, datetime] = {}
        self._identity: dict[_ProviderKey, str] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._pending: OrderedDict[str, _Work] = OrderedDict()
        self._in_flight: set[str] = set()
        self._futures: set[Future[None]] = set()
        self._pending_limit = config.admission.max_items
        self._pending_bytes = 0
        self._max_pending_bytes = config.admission.max_bytes
        self._closed = False
        self._unsubscribe: Callable[[], None] | None = None
        self._workspace_unsubscribers: list[Callable[[], None]] = []
        self._next_lifecycle_generation = itertools.count()

    @classmethod
    def from_config(
        cls,
        cfg: IssueOpsConfig,
        *,
        registry: RepoRegistry | None,
        log: HandoverLog | None = None,
    ) -> AssigneePoller | None:
        """Build the assignment owner only when either explicit feature is enabled."""
        if not (cfg.pickup_enabled or cfg.assign_bot):
            return None
        assert registry is not None
        return cls(config=cfg, registry=registry, engine=PickupEngine(log=log))

    # ─── lifecycle subscription and bounded delivery ────────────────────────

    def bind(self, subscribe: _SubscribeManagers | None = None) -> None:
        """Enable bounded delivery and optionally subscribe to manager lifecycles.

        The optional argument preserves old ``bind()`` callers. A normal daemon
        passes ``registry.subscribe_managers``; it is deliberately a narrow
        callable rather than a new generic event bus.
        """
        with self._lock:
            if self._closed:
                return
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="grove-assignment"
                )
        if subscribe is not None and self._unsubscribe is None:
            self._unsubscribe = subscribe(self._bind_manager)

    def close(self) -> None:
        """Stop new work and abandon pending wakes without waiting for provisioning."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            self._pending_bytes = 0
            pool, self._pool = self._pool, None
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            unsubscribers, self._workspace_unsubscribers = self._workspace_unsubscribers, []
        if unsubscribe is not None:
            with contextlib.suppress(Exception):
                unsubscribe()
        for workspace_unsubscribe in unsubscribers:
            with contextlib.suppress(Exception):
                workspace_unsubscribe()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _bind_manager(self, _root: Path, manager: WorkspaceManager) -> None:
        """Translate the manager's existing callback into a compact lifecycle wake."""
        unsubscribe = manager.subscribe(self._on_workspace_event)
        with self._lock:
            if self._closed:
                unsubscribe()
            else:
                self._workspace_unsubscribers.append(unsubscribe)

    def _on_workspace_event(self, event: WorkspaceEvent) -> None:
        if event.kind not in {"created", "updated", "paused", "resumed", "respawned", "killed"}:
            return
        generation = next(self._next_lifecycle_generation)
        self.handle_lifecycle_event(
            AssignmentLifecycleEvent(
                workspace_id=event.workspace_id,
                lifecycle=event.kind,
                delivery_id=f"workspace:{event.workspace_id}:{generation}",
                generation=generation,
            )
        )

    def handle_ticket_event(self, event: AssignmentTicketEvent) -> bool:
        """Admit one normalized tracker assignment delivery.

        The daemon must authenticate its forwarding token before calling this.
        The supplied ``change`` is only a wake-up: processing always re-reads
        this one target to defeat reorder and an incomplete forge payload.
        """
        return self._admit(event, key=event.target.key)

    def handle_lifecycle_event(self, event: AssignmentLifecycleEvent) -> bool:
        """Admit one Grove workspace lifecycle wake-up without scanning the fleet."""
        return self._admit(event, key=f"workspace:{event.workspace_id}")

    def _admit(self, event: _AssignmentEvent, *, key: str) -> bool:  # noqa: PLR0911
        """Reserve a bounded delivery before scheduling a worker for it.

        The reservation covers pending AND in-flight work. A target may replace
        one pending wake, but never queues a second behind the same provisioning
        operation; that is the per-target exclusion lost once lifecycle work
        moved off the synchronous manager path.
        """
        size = len(event.model_dump_json().encode())
        with self._lock:
            unusable = self._closed or size > self._max_pending_bytes
            if unusable or self._seen_delivery(key, event.delivery_id):
                return False
            latest = self._generation.get(key, -1)
            if event.generation < latest:
                return False
            old = self._pending.get(key)
            if old is not None and event.generation < old.event.generation:
                return False
            replacing = old is not None
            items = len(self._pending) + len(self._in_flight)
            bytes_after = self._pending_bytes + size - (old.size_bytes if old else 0)
            full_bytes = bytes_after > self._max_pending_bytes
            full_items = not replacing and items >= self._pending_limit
            if full_bytes or full_items:
                logger.warning("issue-ops assignment delivery rejected — bounded intake is full")
                return False
            self._generation[key] = event.generation
            self._generation.move_to_end(key)
            self._trim_metadata()
            self._pending[key] = _Work(event=event, size_bytes=size)
            self._pending.move_to_end(key)
            self._pending_bytes = bytes_after
            self._remember_delivery(key, event.delivery_id)
            pool = self._pool
            if pool is None:
                work = self._take_pending(key)
            elif key in self._in_flight:
                return True
            else:
                return self._submit_next(pool)
        self._dispatch(key, work)
        return True

    def _take_pending(self, key: str) -> _Work:
        work = self._pending.pop(key)
        self._pending_bytes -= work.size_bytes
        return work

    def _submit_next(self, pool: ThreadPoolExecutor) -> bool:
        if not self._pending or len(self._in_flight) >= 1:
            return True
        key = next(iter(self._pending))
        work = self._take_pending(key)
        self._in_flight.add(key)
        future = pool.submit(self._run_work, key, work)
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)
        return True

    def _run_work(self, key: str, work: _Work) -> None:
        try:
            self._dispatch(key, work)
        except Exception as exc:  # a provider/create failure never kills intake
            logger.warning("issue-ops assignment delivery failed (swallowed): {}", exc)
        finally:
            with self._lock:
                self._in_flight.discard(key)
                pool = self._pool
                if not self._closed and pool is not None:
                    self._submit_next(pool)

    def _dispatch(self, _key: str, work: _Work) -> None:
        if isinstance(work.event, AssignmentTicketEvent):
            self._handle_ticket_event(work.event)
        else:
            self._handle_lifecycle_event(work.event)

    def _seen_delivery(self, key: str, delivery_id: str) -> bool:
        deliveries = self._delivery_ids.get(key, [])
        if deliveries:
            self._delivery_ids.move_to_end(key)
        return delivery_id in deliveries

    def _remember_delivery(self, key: str, delivery_id: str) -> None:
        deliveries = self._delivery_ids.setdefault(key, [])
        deliveries.append(delivery_id)
        del deliveries[: -self._delivery_capacity]
        self._delivery_ids.move_to_end(key)
        self._trim_metadata()

    def _trim_metadata(self) -> None:
        while len(self._generation) > self._metadata_capacity:
            self._generation.popitem(last=False)
        while len(self._delivery_ids) > self._metadata_capacity:
            self._delivery_ids.popitem(last=False)

    # ─── explicit no-push reconcile ─────────────────────────────────────────

    def bootstrap(self, now: datetime | None = None) -> PickupPlan:
        """Manually reconcile configured no-push providers once, never on a timer.

        This is the supported fallback for a tracker that cannot forward an
        authenticated assignment delivery. Operators invoke it at bootstrap or
        after repairing a webhook. It intentionally retains the old expensive
        discovery only behind that explicit policy boundary.
        """
        return self.tick(now)

    def tick(self, now: datetime | None = None) -> PickupPlan:  # noqa: PLR0912
        """Compatibility alias for explicit manual reconciliation, not a periodic job."""
        moment = now if now is not None else self._clock()
        if self._cfg.assign_bot:
            self._bootstrap_assignments()
        if not self._cfg.pickup_enabled:
            return PickupPlan()
        try:
            handed = self._engine.log.keys()
        except GroveError as exc:
            logger.error("issue-ops pickup bootstrap skipped — {}", exc)
            return PickupPlan()
        candidates: list[PickupCandidate] = []
        active = 0
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
            except GroveError as exc:
                logger.warning("issue-ops pickup bootstrap skipping {}: {}", root, exc)
                continue
            for provider in manager.ticket_providers.providers():
                pkey = (str(root), provider.name)
                if not provider.configured or not self._ready(pkey, moment):
                    continue
                self._announce_identity(pkey, provider)
                try:
                    refs = provider.list_assigned()
                except GroveError as exc:
                    self._backoff[pkey] = moment + timedelta(
                        seconds=self._cfg.pickup_backoff_seconds
                    )
                    logger.warning(
                        "issue-ops pickup bootstrap could not read {}: {}", provider.name, exc
                    )
                    continue
                self._backoff.pop(pkey, None)
                for ref in refs:
                    if ref.kind != "issue" or (ref.status or "open") != "open":
                        continue
                    try:
                        handover_key = PickupEngine.key_for(manager, provider.name, ref.id)
                    except GroveError:
                        continue
                    if manager.find_by_ticket(provider.name, ref.id) is not None:
                        active += 1
                    elif handover_key.wire not in handed:
                        candidates.append(PickupCandidate(root, handover_key, ref))
        plan = PickupEngine.plan(candidates, active=active, ceiling=self._cfg.pickup_max_active)
        for candidate in plan.take:
            self._start(candidate, moment)
        return plan

    def _ready(self, pkey: _ProviderKey, now: datetime) -> bool:
        until = self._backoff.get(pkey)
        return until is None or now >= until

    def _announce_identity(self, pkey: _ProviderKey, provider: TicketProvider) -> None:
        if pkey in self._identity:
            return
        try:
            self._identity[pkey] = provider.viewer_login()
        except GroveError:
            return

    # ─── tracker event reconciliation ───────────────────────────────────────

    def _handle_ticket_event(self, event: AssignmentTicketEvent) -> None:
        manager = self._manager_for(event.target)
        if manager is None:
            return
        try:
            provider = manager.ticket_providers.get(event.target.provider)
            # The event is never authoritative assignment state. A fetch of ONLY
            # this target makes late "assigned" after "unassigned" harmless.
            ref = provider.get_ticket(event.target.ticket_id)
        except GroveError as exc:
            logger.warning("issue-ops assignment could not re-read {}: {}", event.target.key, exc)
            return
        if ref.kind != "issue":
            return
        if event.change == "unassigned":
            self._release_ticket(manager, ref.provider, ref.id)
            return
        if self._cfg.pickup_enabled and (ref.status or "open") == "open":
            self._pickup_target(manager, ref, self._clock())

    def _manager_for(self, target: AssignmentTicketIdentity) -> WorkspaceManager | None:
        # Registry state is authoritative and manager lookup is lazy. Scanning
        # configured roots is a configuration lookup, never a provider/fleet
        # scan: no tickets or workspaces are read on an inbound event.
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
            except GroveError:
                continue
            config = getattr(manager.config.tickets, target.provider, None)
            if (
                getattr(config, "owner", "") == target.owner
                and getattr(config, "repo", "") == target.repo
            ):
                return manager
        logger.debug("issue-ops assignment event names no configured repo: {}", target.key)
        return None

    def _pickup_target(self, manager: WorkspaceManager, ref: TicketRef, now: datetime) -> None:
        try:
            key = PickupEngine.key_for(manager, ref.provider, ref.id)
            if manager.find_by_ticket(ref.provider, ref.id) is not None:
                return
            if self._engine.log.contains(key):
                return
        except GroveError as exc:
            logger.warning("issue-ops assignment could not prepare {}: {}", ref.id, exc)
            return
        # No host-wide scan: active capacity is an index fact maintained by
        # lifecycle events. A lost lifecycle event fails closed (defer), never
        # invents extra running work.
        active = self._active_pickups()
        plan = PickupEngine.plan(
            [PickupCandidate(manager.repo_root, key, ref)],
            active=active,
            ceiling=self._cfg.pickup_max_active,
        )
        if not plan.take:
            logger.info("issue-ops assignment deferred {} — pickup capacity is full", key.wire)
            return
        self._start(plan.take[0], now)

    def _active_pickups(self) -> int:
        with self._lock:
            return sum(entry.workspace_id is not None for entry in self._assigned.values())

    def _start(self, candidate: PickupCandidate, now: datetime) -> None:
        try:
            manager = self._registry.get(candidate.repo_root)
            state = self._engine.hand_over(manager, key=candidate.key, source="poll", now=now)
        except GroveError as exc:
            logger.warning(
                "issue-ops pickup could not start {} (it stays claimed): {}",
                candidate.key.wire,
                exc,
            )
            return
        self._record_holder(candidate.key.wire, state.id)

    # ─── lifecycle reconciliation ───────────────────────────────────────────

    def _handle_lifecycle_event(self, event: AssignmentLifecycleEvent) -> None:
        if event.lifecycle == "killed":
            self._release_workspace(event.workspace_id)
            return
        # Read exactly one workspace rather than every repo's fleet. The manager
        # event may be stale after a kill; `resolve_workspace` then honestly says
        # absent and no release happens except on the killed event itself.
        try:
            manager, state = self._registry.resolve_workspace(event.workspace_id)
        except GroveError:
            return
        self._index_workspace(manager, state.id, state.ticket_refs)

    def _index_workspace(
        self, manager: WorkspaceManager, workspace_id: str, refs: list[TicketRef]
    ) -> None:
        wanted: set[str] = set()
        for ref in refs:
            wire = self._assign_once(
                manager, ref.provider, ref.id, ref.kind, workspace_id=workspace_id
            )
            if wire is not None:
                wanted.add(wire)
        self._release_workspace_except(workspace_id, wanted)

    def _record_holder(self, wire: str, workspace_id: str) -> None:
        with self._lock:
            assigned = self._assigned.get(wire)
            if assigned is not None:
                self._assigned[wire] = _Assigned(
                    assigned.repo_root, assigned.provider, assigned.ticket_id, workspace_id
                )

    def _release_workspace(self, workspace_id: str) -> None:
        self._release_workspace_except(workspace_id, set())

    def _release_ticket(
        self, manager: WorkspaceManager, provider_name: TicketProviderName, ticket_id: str
    ) -> None:
        """Release only a matching assignment Grove previously recorded as its own."""
        try:
            wire = PickupEngine.key_for(manager, provider_name, ticket_id).wire
        except GroveError:
            return
        with self._lock:
            entry = self._assigned.pop(wire, None)
        if entry is not None:
            self._release(entry)

    def _release_workspace_except(self, workspace_id: str, wanted: set[str]) -> None:
        with self._lock:
            release = [
                (wire, entry)
                for wire, entry in self._assigned.items()
                if entry.workspace_id == workspace_id and wire not in wanted
            ]
            for wire, _entry in release:
                del self._assigned[wire]
        for _wire, entry in release:
            self._release(entry)

    # ─── publish edge and explicit assignment ownership ─────────────────────

    def assign_now(
        self,
        repo_root: str,
        provider_name: TicketProviderName,
        ticket_id: str,
        kind: TicketKind = "issue",
        *,
        workspace_id: str | None = None,
    ) -> bool:
        """Assign on the publisher's demand edge and retain release ownership.

        ``workspace_id`` names the holder, and passing it is what keeps the
        pickup ceiling honest: ``_active_pickups`` counts only entries that
        resolve to a live workspace, so an edge assignment recorded without one
        is invisible to capacity until some unrelated lifecycle event fills it
        in. In that window the fleet reads emptier than it is and a ticket event
        takes work past ``pickup_max_active``. The publisher always knows the
        answer — it is publishing FOR a workspace — so the identity travels with
        the assignment rather than being reconstructed later.
        """
        try:
            manager = self._registry.get(Path(repo_root))
        except GroveError as exc:
            logger.debug("issue-ops assign_now could not resolve {}: {}", repo_root, exc)
            return False
        return (
            self._assign_once(manager, provider_name, ticket_id, kind, workspace_id=workspace_id)
            is not None
        )

    def _assign_once(
        self,
        manager: WorkspaceManager,
        provider_name: TicketProviderName,
        ticket_id: str,
        kind: TicketKind = "issue",
        *,
        workspace_id: str | None = None,
    ) -> str | None:
        """Assign exactly issues and retain only assignments Grove itself made."""
        if kind != "issue" or not self._cfg.assign_bot:
            return None
        try:
            key = PickupEngine.key_for(manager, provider_name, ticket_id)
            provider = manager.ticket_providers.get(provider_name)
        except GroveError:
            return None
        with self._lock:
            existing = self._assigned.get(key.wire)
            if existing is not None:
                if workspace_id is not None and existing.workspace_id != workspace_id:
                    self._assigned[key.wire] = _Assigned(
                        existing.repo_root, existing.provider, existing.ticket_id, workspace_id
                    )
                return key.wire
        if self._engine.assign_bot(provider, ticket_id):
            with self._lock:
                self._assigned[key.wire] = _Assigned(
                    str(manager.repo_root), provider_name, ticket_id, workspace_id
                )
            return key.wire
        return None

    def _bootstrap_assignments(self) -> None:
        """Explicitly index live work only during manual bootstrap reconciliation."""
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
                states = manager.list()
            except GroveError as exc:
                logger.warning("issue-ops assignment bootstrap skipping {}: {}", root, exc)
                continue
            for state in states:
                self._index_workspace(manager, state.id, state.ticket_refs)

    def _release(self, entry: _Assigned) -> None:
        try:
            manager = self._registry.get(Path(entry.repo_root))
            provider = manager.ticket_providers.get(entry.provider)
        except GroveError:
            return
        self._engine.release_bot(provider, entry.ticket_id)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)


__all__ = ["AssigneePoller"]
