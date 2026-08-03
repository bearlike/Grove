"""The assignee poll — the daemon's background worker over :class:`PickupEngine`.

One bounded, best-effort worker on its own timer, copying the discipline
``TicketStatusPublisher`` established in this package: :meth:`from_config`
returns ``None`` when there is nothing to do, :meth:`bind` starts a
single-worker pool and a self-arming timer, :meth:`close` unwinds both, and
unbound it runs :meth:`tick` inline on the caller's thread — which is the seam
every test drives, with no threads and no clock.

**Why a poll at all**, when this tree's standing preference is to subscribe to an
edge that already exists: there is no edge. The tracker is somebody else's
service and it pushes nothing to a loopback daemon; the existing inbound path
(`@grove` in a comment) needs a CI workflow AND a host-labeled runner, which a
deployment with zero runners can never have. A poll is what makes inbound
automation reachable there at all. It is gated the way the doctrine asks: both
halves default off, so a daemon that was not asked for this does no work.

**The two halves are independently gated and share one worker.** ``assign_bot``
reconciles the outbound assignment over live workspaces; ``pickup_enabled``
scans for inbound work. Either one alone builds the poller; neither builds
nothing.

**Assignment is reconciled on the tick rather than inline at create**, and that
is forced rather than chosen: the ticket layer's own rule is that a provider
method doing network must not become reachable from a lifecycle path, which is
what keeps ``create``/``attach_ticket`` deterministic and offline-safe. So the
tick sweeps live workspaces' refs instead, memoized per ticket for the process,
and a freshly attached ticket is marked within one interval. The explicit
`grove tickets handover` command assigns immediately, for the human who wants it
now.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.tickets import TicketProviderName
from grove.core.errors import GroveError
from grove.core.issueops.handover import HandoverLog
from grove.core.issueops.pickup import PickupCandidate, PickupEngine, PickupPlan

if TYPE_CHECKING:
    from grove.core.config import IssueOpsConfig
    from grove.core.contracts.tickets import TicketRef
    from grove.core.manager import WorkspaceManager
    from grove.core.registry import RepoRegistry
    from grove.core.tickets.provider import TicketProvider

# A backoff/identity key: which repo's configured provider we are talking to.
# The repo is in it because two repos' configs can point one provider NAME at
# different hosts and different credentials, so one of them rate-limiting says
# nothing about the other.
_ProviderKey = tuple[str, TicketProviderName]


class AssigneePoller:
    """Polls each configured tracker for the bot's assigned issues and starts work."""

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
        self._interval = timedelta(seconds=config.pickup_interval_seconds)
        self._backoff_for = timedelta(seconds=config.pickup_backoff_seconds)
        # Skip-until per provider, set on any provider failure. Not compounding:
        # one failure buys one window, and a successful poll clears the entry.
        self._backoff: dict[_ProviderKey, datetime] = {}
        # Which identity each provider's credential turned out to be. Resolved
        # once and LOGGED, because "assigned to me" is only the intended rule
        # while the credential really is the bot's — a deployment that
        # configured a person's token would otherwise silently pick up that
        # person's tickets, which is alarming rather than helpful. Grove does
        # not refuse it (the token is deliberately the only identity there is),
        # it says whose queue it is draining.
        self._identity: dict[_ProviderKey, str] = {}
        # Tickets already assigned this process. The outbound write is idempotent
        # upstream, so this memo is purely about not spending a round-trip per
        # workspace per tick; it resets on restart and self-heals in one call.
        self._assigned: set[str] = set()
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._timer: threading.Timer | None = None
        self._scheduling = False  # true only while bound

    @classmethod
    def from_config(
        cls,
        cfg: IssueOpsConfig,
        *,
        registry: RepoRegistry | None,
        log: HandoverLog | None = None,
    ) -> AssigneePoller | None:
        """Build a poller, or ``None`` when neither half is enabled.

        Both halves default off deliberately: this spawns real agents and writes
        to somebody else's tracker, and a feature with that blast radius does not
        get to be on by default.
        """
        if not (cfg.pickup_enabled or cfg.assign_bot):
            return None
        assert registry is not None  # enabled requires a registry to resolve against
        return cls(config=cfg, registry=registry, engine=PickupEngine(log=log))

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def bind(self) -> None:
        """Start the dispatch worker and arm the first tick."""
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-pickup")
        self._scheduling = True
        self._arm()

    def close(self) -> None:
        """Stop scheduling, cancel the pending tick, and let an in-flight one go.

        ``wait=False`` on purpose: a tick can be midway through a ``create``,
        which for a container workspace is minutes of ``devcontainer up``, and
        shutdown must never block on side-effecting work already in flight.
        """
        self._scheduling = False
        with self._lock:
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _arm(self) -> None:
        if not self._scheduling:
            return
        with self._lock:
            if self._timer is not None:
                return
            timer = threading.Timer(self._interval.total_seconds(), self._on_timer)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            self._timer = None
        pool = self._pool
        if pool is not None:
            with contextlib.suppress(RuntimeError):  # shutting down
                pool.submit(self._run_tick)
        self._arm()

    def _run_tick(self) -> None:
        """The worker body — swallows everything, exactly like the publisher's."""
        try:
            self.tick()
        except Exception as exc:  # a bad tick must never kill the poller
            logger.warning("issue-ops pickup tick failed (swallowed): {}", exc)

    # ─── the tick ───────────────────────────────────────────────────────────

    def tick(self, now: datetime | None = None) -> PickupPlan:
        """One pass: reconcile assignments, then pick up what is eligible.

        Returns the plan so a caller (and every test) can see what was deferred
        without reading the log.
        """
        moment = now if now is not None else self._clock()
        if self._cfg.assign_bot:
            self._reconcile_assignments()
        if not self._cfg.pickup_enabled:
            return PickupPlan()

        try:
            handed = self._engine.log.keys()
        except GroveError as exc:
            # Fail CLOSED. An unreadable marker file is indistinguishable from
            # "nothing has ever been handed over", and acting on that reading is
            # precisely the unbounded self-trigger loop the marker exists to
            # prevent — so the whole tick is skipped and says why.
            logger.error("issue-ops pickup skipped this tick — {}", exc)
            return PickupPlan()

        candidates, active = self._scan(handed, moment)
        plan = PickupEngine.plan(candidates, active=active, ceiling=self._cfg.pickup_max_active)
        for deferred in plan.defer:
            logger.info(
                "issue-ops pickup deferred {} to the next tick — {} of {} pickup workspaces "
                "are already working",
                deferred.key.wire,
                plan.active + len(plan.take),
                self._cfg.pickup_max_active,
            )
        for candidate in plan.take:
            self._start(candidate, moment)
        return plan

    def _scan(self, handed: set[str], now: datetime) -> tuple[list[PickupCandidate], int]:
        """Ask every configured tracker what it has for us. All the I/O lives here.

        ``active`` counts the assigned tickets that ALREADY have a live
        workspace — which is what the ceiling is about, and it costs nothing
        extra because the same ``find_by_ticket`` call decides eligibility.
        """
        candidates: list[PickupCandidate] = []
        active = 0
        for root in self._registry.known_roots():
            try:
                mgr = self._registry.get(root)
            except GroveError as exc:  # one unreadable repo must not blind the rest
                logger.warning("issue-ops pickup skipping {}: {}", root, exc)
                continue
            for provider in mgr.ticket_providers.providers():
                pkey = (str(root), provider.name)
                if not self._ready(pkey, provider, now):
                    continue
                for ref in self._assigned_refs(pkey, provider, now):
                    try:
                        key = PickupEngine.key_for(mgr, provider.name, ref.id)
                    except GroveError:
                        continue  # a tracker with no owner/repo cannot be keyed
                    if mgr.find_by_ticket(provider.name, ref.id) is not None:
                        active += 1
                        continue
                    if key.wire in handed:
                        continue
                    candidates.append(PickupCandidate(repo_root=root, key=key, ref=ref))
        return candidates, active

    def _ready(self, pkey: _ProviderKey, provider: TicketProvider, now: datetime) -> bool:
        """Is this provider usable right now — configured, and not backed off?"""
        if not provider.configured:
            return False
        until = self._backoff.get(pkey)
        return until is None or now >= until

    def _assigned_refs(
        self, pkey: _ProviderKey, provider: TicketProvider, now: datetime
    ) -> list[TicketRef]:
        """The open issues assigned to this credential's own account.

        ``list_assigned`` already asks the tracker for "issues where I am AN
        assignee" — verified against a live Gitea on an issue carrying a human
        assignee alongside Grove's, which came back — so a ticket a human is
        also on is picked up, per the product rule, with no widening and no
        per-issue read. GitHub's ``filter=assigned`` is the documented analogue
        and is INFERRED to behave the same way; it has not been exercised
        against a live GitHub here.

        Any failure backs the whole provider off, because a 403 or a 429 is a
        statement about the credential or the budget rather than about whichever
        issue happened to be asked for.
        """
        self._announce_identity(pkey, provider)
        try:
            refs = provider.list_assigned()
        except GroveError as exc:
            self._backoff[pkey] = now + self._backoff_for
            logger.warning(
                "issue-ops pickup backing {} off for {}s after a failed poll: {}",
                provider.name,
                self._backoff_for.total_seconds(),
                exc,
            )
            return []
        self._backoff.pop(pkey, None)
        return [r for r in refs if r.kind == "issue" and (r.status or "open") == "open"]

    def _announce_identity(self, pkey: _ProviderKey, provider: TicketProvider) -> None:
        """Log, once, whose queue this poll is actually draining.

        The whole design rests on the configured token being Grove's own bot
        account: that is what makes "assigned to me" mean "assigned to Grove".
        Nothing in the code can enforce it — the token IS the only identity there
        is — so the honest defence is to say out loud which account answered, and
        let an operator who sees their own name there fix their config.
        """
        if pkey in self._identity:
            return
        try:
            login = provider.viewer_login()
        except GroveError as exc:
            logger.debug("issue-ops could not resolve {}'s identity: {}", provider.name, exc)
            return
        self._identity[pkey] = login
        logger.info(
            "issue-ops pickup is polling {} ({}) as {!r} — every open issue assigned to "
            "that account is treated as work for Grove",
            provider.name,
            provider.context or "no scope",
            login,
        )

    def _start(self, candidate: PickupCandidate, now: datetime) -> None:
        try:
            mgr = self._registry.get(candidate.repo_root)
            self._engine.hand_over(mgr, key=candidate.key, source="poll", now=now)
        except GroveError as exc:
            # The marker is already claimed by now, so a failed create is NOT
            # retried on the next tick. That is the deliberate trade: a lost
            # pickup a human can re-trigger, over a create loop nothing stops.
            logger.warning(
                "issue-ops pickup could not start a workspace for {} (it stays claimed, "
                "so it will not be retried automatically): {}",
                candidate.key.wire,
                exc,
            )

    # ─── the outbound half ──────────────────────────────────────────────────

    def _reconcile_assignments(self) -> None:
        """Put the bot's name on every ticket a live workspace currently holds."""
        for root in self._registry.known_roots():
            try:
                mgr = self._registry.get(root)
                states = mgr.list()
            except GroveError as exc:
                logger.warning("issue-ops assignment skipping {}: {}", root, exc)
                continue
            for state in states:
                for ref in state.ticket_refs:
                    # Issues only: the queue is issues, and a pull request
                    # already records its author, so assigning one adds noise
                    # rather than a fact a reader did not have.
                    if ref.kind != "issue":
                        continue
                    self._assign_once(mgr, ref.provider, ref.id)

    def _assign_once(
        self, mgr: WorkspaceManager, provider_name: TicketProviderName, ticket_id: str
    ) -> None:
        try:
            key = PickupEngine.key_for(mgr, provider_name, ticket_id)
            provider = mgr.ticket_providers.get(provider_name)
        except GroveError:
            return
        if key.wire in self._assigned:
            return
        if self._engine.assign_bot(provider, ticket_id):
            self._assigned.add(key.wire)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)


__all__ = ["AssigneePoller"]
