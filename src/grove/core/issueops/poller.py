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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.tickets import TicketKind, TicketProviderName
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


@dataclass(frozen=True, slots=True)
class _Assigned:
    """Where a ticket Grove assigned came from, so the release can reach it again.

    The workspace that occasioned the assignment is deliberately NOT in here: by
    the time a release is due that record is gone (``kill`` deletes it outright),
    so the only durable coordinates are the ones that name the TICKET.
    """

    repo_root: str
    provider: TicketProviderName
    ticket_id: str


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
        # Tickets this process assigned, and where each came from. The outbound
        # write is idempotent upstream, so the memo saves a round-trip per
        # workspace per tick; it resets on restart and self-heals in one call.
        # It carries the repo/provider/id rather than just the key because it is
        # ALSO the release set — a ticket is unassigned only if Grove is the one
        # that assigned it, and the release has to resolve a provider to do it.
        self._assigned: dict[str, _Assigned] = {}
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

    def assign_now(
        self,
        repo_root: str,
        provider_name: TicketProviderName,
        ticket_id: str,
        kind: TicketKind = "issue",
    ) -> bool:
        """Assign the bot to one ticket immediately, recording Grove's ownership.

        The seam an edge-triggered caller uses instead of waiting up to a poll
        interval — the publisher calls it as it upserts a sticky comment, which
        is the moment a ticket is *deterministically* known to be Grove's work.

        **It exists so there is exactly ONE ownership memo.** An assignment made
        anywhere else would be absent from ``_assigned``, and ``_release_ended``
        releases only what that memo holds — so a directly-assigned ticket would
        never be released and the board would keep claiming Grove was working it
        forever. That is the same leak this poller was just fixed to close, so a
        second assignment path must feed the same memo rather than run beside it.

        Idempotent and cheap on the repeat: the memo short-circuits a ticket
        already assigned this process, so a per-flush call costs nothing after
        the first. Returns whether the ticket is now Grove-owned.
        """
        try:
            mgr = self._registry.get(Path(repo_root))
        except GroveError as exc:
            logger.debug("issue-ops assign_now could not resolve {}: {}", repo_root, exc)
            return False
        return self._assign_once(mgr, provider_name, ticket_id, kind) is not None

    def _reconcile_assignments(self) -> None:
        """Assign the bot to every ticket a live workspace holds, and release the rest.

        Both directions run off ONE sweep of the live fleet, because they are the
        same question asked twice: the tickets a live workspace holds are what the
        assignment means, so a key Grove assigned that is no longer in that set is
        precisely a ticket whose workspace has ended. Reconciling rather than
        hooking teardown is what makes the release correct for every way a
        workspace can stop — `kill`, a detach, a record that vanished — including
        the ones that never run a verb Grove could have hooked.
        """
        live: set[str] = set()
        answered: set[str] = set()
        for root in self._registry.known_roots():
            try:
                mgr = self._registry.get(root)
                states = mgr.list()
            except GroveError as exc:
                # A repo that could not be read has not said its tickets are
                # gone. Releasing on that silence would unassign a live
                # workspace's ticket over a transient config error, so an
                # unreadable repo is skipped by the release too (below).
                logger.warning("issue-ops assignment skipping {}: {}", root, exc)
                continue
            answered.add(str(root))
            for state in states:
                for ref in state.ticket_refs:
                    wire = self._assign_once(mgr, ref.provider, ref.id, ref.kind)
                    if wire is not None:
                        live.add(wire)
        self._release_ended(live, answered)

    def _assign_once(
        self,
        mgr: WorkspaceManager,
        provider_name: TicketProviderName,
        ticket_id: str,
        kind: TicketKind = "issue",
    ) -> str | None:
        """Assign the bot once per ticket; return its key iff GROVE owns the assignment.

        ``None`` covers four different situations that all mean the same thing
        here — not an issue, unkeyable, unassignable, or already assigned by
        somebody else — because none of them makes this ticket Grove's to
        release later.

        **The issues-only rule lives HERE because two callers need it and only
        one of them used to have it.** The reconcile sweep filtered kind itself
        while the publisher's edge-triggered ``assign_now`` did not, so a pull
        request was assigned on the publish edge, recorded as owned, then found
        missing from the sweep's live set and RELEASED on the next tick — an
        assign/unassign flap writing noise to somebody else's tracker once per
        interval, forever. A rule two callers share belongs at the seam they
        share, or the copy that is missing is the one nobody notices.

        A pull request already records its author, so assigning one adds noise
        rather than a fact a reader did not have.
        """
        if kind != "issue":
            return None
        try:
            key = PickupEngine.key_for(mgr, provider_name, ticket_id)
            provider = mgr.ticket_providers.get(provider_name)
        except GroveError:
            return None
        if key.wire in self._assigned:
            return key.wire
        if self._engine.assign_bot(provider, ticket_id):
            self._assigned[key.wire] = _Assigned(
                repo_root=str(mgr.repo_root), provider=provider_name, ticket_id=ticket_id
            )
            return key.wire
        return None

    def _release_ended(self, live: set[str], answered: set[str]) -> None:
        """Unassign the bot from the tickets it assigned whose workspace has ended.

        Only keys in ``_assigned`` are ever released, and that memo is the whole
        safety argument: it holds what GROVE assigned in THIS process, so a
        ticket a human assigned to the bot by hand is never touched, and a
        restart forgets rather than sweeping somebody else's board.

        A repo that failed to answer this tick is skipped rather than treated as
        empty — the failure direction that matters, since reading "no live
        workspaces" out of an error would unassign every ticket on that repo.
        """
        for wire, entry in list(self._assigned.items()):
            if wire in live or entry.repo_root not in answered:
                continue
            del self._assigned[wire]  # dropped either way: released, or unreleasable
            try:
                mgr = self._registry.get(Path(entry.repo_root))
                provider = mgr.ticket_providers.get(entry.provider)
            except GroveError:
                continue
            self._engine.release_bot(provider, entry.ticket_id)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)


__all__ = ["AssigneePoller"]
