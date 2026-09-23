"""One task, one heap, one worker — for every watch on the host.

The cost model is the design, so it is worth stating before the code. A naive
implementation gives each waiter its own thread, or wakes every few seconds and
scans the registry; both scale their CPU with how many agents happen to be
waiting, which on a fleet host is exactly backwards — waiting should be free.

This is instead a **single asyncio task holding a min-heap keyed on the next due
time**. It sleeps until the earliest deadline, does precisely that one thing,
and sleeps again. With nothing registered it awaits an event indefinitely and
runs nothing at all. Registration and cancellation set that event, so a new
watch is armed immediately instead of waiting out somebody else's interval.

Three properties fall out, and each is pinned by a test:

* **Nothing periodic.** An empty registry performs no work, ever. There is no
  tick, no sweep and no scan — the heap's minimum IS the next wakeup.
* **Cost tracks watches that are DUE, not watches that exist.** A hundred rows
  on six-hour deadlines cost one timer between them.
* **A slow probe cannot multiply.** Every evaluation goes to one shared
  single-worker executor, so a hanging forge delays other probes by its own
  bounded timeout and can never become a hundred concurrent requests.

The deliberate trade is that last one: probes are serialized, so a slow watcher
delays its neighbours. That is measured rather than assumed — a probe is one
bounded read — and the remedy if it ever bites is a small FIXED worker count,
never one that scales with the fleet.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from loguru import logger

from grove.core.contracts.watches import (
    DEFAULT_DEADLINE,
    MAX_DEADLINE,
    MIN_INTERVAL,
    WatchList,
    WatchOutcome,
    WatchRegistration,
    WatchView,
)
from grove.core.errors import GroveError
from grove.core.watches.log import WatchLog, utcnow
from grove.core.watches.watcher import StillWatching, WatcherRegistry

#: How a callback is delivered. Injected rather than imported so the scheduler
#: is testable with no daemon, no mailbox and no workspaces — and so that what
#: delivery MEANS stays the mailbox's business, not this module's.
DeliverFn = Callable[[WatchView, WatchOutcome], tuple[str, str | None]]

#: How long past its instant a timer with no explicit deadline may run before it
#: expires instead of firing. Enough to absorb one late wake; a timer that has
#: not fired by then is stuck, and saying so is the whole point of a deadline.
_TIMER_GRACE = timedelta(minutes=1)


@dataclass(order=True)
class _Due:
    """One heap entry: when to look at a watch next, and which watch.

    ``watch_id`` is compared second so two entries due at the same instant order
    deterministically instead of raising on an unorderable payload.
    """

    when: datetime
    watch_id: str = field(compare=True)


class WatchScheduler:
    """Owns the heap, the single worker and the durable log.

    One instance per daemon. Construct it, ``await start()`` in the lifespan,
    ``close()`` on shutdown — the shape the notification broker and ticket
    publisher already use.
    """

    #: One worker, deliberately. See the module docstring: the fix for a slow
    #: probe is a small fixed number, never one per workspace.
    _WORKERS = 1

    def __init__(
        self,
        *,
        log: WatchLog,
        watchers: WatcherRegistry,
        deliver: DeliverFn,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._log = log
        self._watchers = watchers
        self._deliver = deliver
        self._clock = clock
        self._heap: list[_Due] = []
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._cancelled: set[str] = set()
        self._transition_lock = RLock()

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def prime(self) -> int:
        """Become ready to serve: open the worker and re-arm durable rows.

        Separate from :meth:`start` because "ready" and "running" are different
        states, and conflating them makes the loop untestable — a caller driving
        :meth:`tick` by hand while the background task also drains gives two
        consumers of one heap, each stealing entries from the other. The ticket
        publisher's ``_scheduling`` flag is the same split for the same reason.

        Recovered rows are spread with jitter across their own interval rather
        than all becoming due at once: forty watches reloaded after a restart
        would otherwise fire forty probes in the same instant, which is the
        thundering herd this whole design exists to avoid.
        """
        self._pool = ThreadPoolExecutor(max_workers=self._WORKERS, thread_name_prefix="grove-watch")
        now = self._clock()
        try:
            self._log.prune(now)
            rows = self._log.active()
        except GroveError as exc:
            # Surfaced rather than fatal: an unreadable log must not stop the
            # daemon from serving watches registered from here on.
            logger.error("watches: cannot read the registry, starting empty ({})", exc)
            rows = []
        for row in rows:
            spread = random.uniform(0, max(1.0, self._interval_of(row).total_seconds()))
            heapq.heappush(self._heap, _Due(now + timedelta(seconds=spread), row.id))
        if rows:
            logger.info("watches: re-armed {} watch(es) across their intervals", len(rows))
        return len(rows)

    async def start(self) -> None:
        """Prime, then run the loop. What the daemon's lifespan calls."""
        self.prime()
        self._task = asyncio.create_task(self._run(), name="grove-watch-scheduler")

    def close(self) -> None:
        """Stop waiting and release the worker. Never waits on work in flight."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # ─── the public verbs ───────────────────────────────────────────────────

    def register(self, registration: WatchRegistration) -> WatchView:
        """Record a watch durably and arm it, waking the scheduler immediately."""
        now = self._clock()
        deadline = self._deadline_for(registration, now)
        expires_at = None if deadline is None else now + deadline
        watch = WatchView(
            id="wch_" + uuid4().hex,
            recipient=registration.recipient,
            predicate=registration.predicate,
            state="pending",
            note=registration.note,
            every=registration.every,
            created_at=now,
            expires_at=expires_at,
            next_due=self._first_due(registration, now, expires_at),
        )
        self._log.put(watch)
        heapq.heappush(self._heap, _Due(watch.next_due or now, watch.id))
        self._wake.set()
        return watch

    def cancel(self, watch_id: str) -> WatchView | None:
        """Withdraw a watch. Its heap entry is dropped when next popped."""
        with self._transition_lock:
            row = self._log.get(watch_id)
            if row is None or row.state != "pending":
                return row
            self._cancelled.add(watch_id)
            settled = self._log.settle(watch_id, state="cancelled", now=self._clock())
        self._wake.set()
        return settled

    def list(self, *, workspace_id: str | None = None) -> WatchList:
        """Every watch, or only those that call back into one workspace.

        Scoped by RECIPIENT, never by who made the HTTP call: a watch registered
        on a workspace's behalf (``--for``) is still that workspace's watch,
        because that is where its callback lands.
        """
        rows = self._log.all()
        if workspace_id is not None:
            rows = [row for row in rows if row.recipient.workspace_id == workspace_id]
        return WatchList(watches=rows)

    # ─── the loop ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            timeout = self.seconds_until_next()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                # No heap entries => timeout is None => this waits forever, for
                # free, until something registers. That is the idle guarantee.
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            await self.tick()

    def seconds_until_next(self) -> float | None:
        """How long to wait, or ``None`` for "nothing is scheduled at all".

        Public because it IS the idle guarantee, and a guarantee nothing can
        read is a guarantee nothing can hold. ``None`` here is what makes an
        empty registry cost nothing: the loop waits on an event rather than on
        a clock, so no timer exists to fire.
        """
        if not self._heap:
            return None
        return max(0.0, (self._heap[0].when - self._clock()).total_seconds())

    async def tick(self) -> int:
        """Evaluate everything whose moment has arrived. Returns how many.

        Public for the reason above: the loop's whole cost contract is "only
        what is DUE", and that is only assertable against a seam a caller can
        drive with its own clock. Driving it directly is also how the daemon
        could ever be asked to catch up deterministically.
        """
        now = self._clock()
        visited = 0
        while self._heap and self._heap[0].when <= now:
            entry = heapq.heappop(self._heap)
            await self._visit(entry.watch_id, now)
            visited += 1
        return visited

    async def _visit(self, watch_id: str, now: datetime) -> None:
        if watch_id in self._cancelled:
            self._cancelled.discard(watch_id)
            return
        row = self._log.get(watch_id)
        if row is None or row.state != "pending":
            return

        if row.expires_at is not None and now >= row.expires_at:
            # This message IS the guarantee: the caller halted on this watch, so
            # its deadline passing must reach it as plainly as success would —
            # the condition was NOT met, how long we waited, what was watched,
            # and that nothing is pending any more. A session that never hears
            # this is a session stalled forever on a subject that never settled.
            self._settle_and_tell(
                row,
                state="expired",
                outcome=WatchOutcome(
                    ok=False,
                    summary=(
                        f"Deadline reached: the watch condition was NOT met. Waited "
                        f"{_duration(row.expires_at - row.created_at)} for "
                        f"{self._subject(row)}, and it had not settled by "
                        f"{row.expires_at.isoformat()}. {self._describe(row)} This watch "
                        "is closed and nothing further will be checked — carry on, or "
                        "register a new watch with a longer deadline if you still need "
                        "to wait."
                    ),
                ),
                now=now,
            )
            return

        watcher = self._watchers.get(row.predicate)
        if watcher is None:
            self._settle_and_tell(
                row,
                state="undeliverable",
                outcome=WatchOutcome(
                    ok=False,
                    summary=f"No watcher is registered for a {row.predicate.kind!r} watch.",
                ),
                now=now,
            )
            return

        try:
            loop = asyncio.get_running_loop()
            outcome = await loop.run_in_executor(self._pool, watcher.evaluate, row.predicate, now)
        except Exception as exc:
            logger.warning("watches: {} could not be evaluated ({})", row.id, exc)
            outcome = None

        self._apply_observation(row, outcome, now)

    def _apply_observation(
        self, row: WatchView, outcome: WatchOutcome | StillWatching | None, now: datetime
    ) -> None:
        """Apply a completed probe only while its durable row remains pending."""
        # Activity callbacks can cancel from a lifecycle worker while a probe is
        # in flight. One transition lock makes cancellation and outcome
        # application linearizable. A standing row remains pending, so its
        # notification stays under the lock; a terminal row settles first and
        # can mail outside it because cancellation no longer applies.
        with self._transition_lock:
            current = self._log.get(row.id)
            if current is None or current.state != "pending":
                self._cancelled.discard(row.id)
                return
            if outcome is None:
                self._rearm(row, now)
                return
            if isinstance(outcome, StillWatching):
                self._carry_on(row, outcome, now)
                self._deliver_carry_on(row, outcome)
                return
            settled = self._log.settle(row.id, state="fired", now=now, outcome=outcome)
            if settled is None:
                return
        self._tell_settled(settled, outcome)

    def _carry_on(self, row: WatchView, seen: StillWatching, now: datetime) -> None:
        """Persist a standing watch's new baseline and schedule its next look."""
        nxt = now + self._interval_of(row)
        self._log.carry_on(row.id, seen.predicate, nxt)
        heapq.heappush(self._heap, _Due(nxt, row.id))

    def _deliver_carry_on(self, row: WatchView, seen: StillWatching) -> None:
        """Best-effort mail for a standing watch whose baseline is already durable."""
        if seen.outcome is None:
            return
        try:
            receipt, detail = self._deliver(row, seen.outcome)
        except Exception as exc:
            logger.warning("watches: could not deliver {} ({})", row.id, exc)
            return
        if receipt != "delivered":
            logger.info("watches: {} change not delivered ({}: {})", row.id, receipt, detail)

    def _rearm(self, row: WatchView, now: datetime) -> None:
        """Not settled yet — put it back for one more interval, or until it expires."""
        nxt = now + self._interval_of(row)
        if row.expires_at is not None:
            nxt = min(nxt, row.expires_at)
        heapq.heappush(self._heap, _Due(nxt, row.id))
        self._log.reschedule(row.id, nxt)

    def _settle_and_tell(
        self, row: WatchView, *, state: str, outcome: WatchOutcome, now: datetime
    ) -> None:
        """Persist a conclusion while pending, then deliver it outside the transition lock."""
        with self._transition_lock:
            current = self._log.get(row.id)
            if current is None or current.state != "pending":
                return
            settled = self._log.settle(row.id, state=state, now=now, outcome=outcome)
        if settled is not None:
            self._tell_settled(settled, outcome)

    def _tell_settled(self, settled: WatchView, outcome: WatchOutcome) -> None:
        """Record the delivery receipt for an already durable terminal outcome."""
        try:
            receipt, detail = self._deliver(settled, outcome)
        except Exception as exc:
            logger.warning("watches: could not deliver {} ({})", settled.id, exc)
            self._log.record_receipt(settled.id, receipt="unknown", detail=str(exc))
            return
        if receipt == "rejected":
            # The predicate may well have settled correctly; the recipient had
            # simply stopped being live. That is its own state, not a failure of
            # the watch, and it is never retried.
            self._log.settle(
                settled.id,
                state="undeliverable",
                now=self._clock(),
                outcome=outcome,
                receipt=receipt,
                receipt_detail=detail,
            )
            return
        self._log.record_receipt(settled.id, receipt=receipt, detail=detail)

    # ─── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _first_due(
        registration: WatchRegistration, now: datetime, expires_at: datetime | None
    ) -> datetime:
        """A timer is due at its own instant; everything else after one interval.

        This is what makes a timer cost no probe at all: its heap entry lands
        exactly when it elapses, so the single evaluation it ever gets is the
        one that returns terminal. Nothing is ever first due after its deadline.
        """
        if expires_at is None:
            # A standing watch looks once right away, so its baseline is the
            # state at attach time rather than one interval later.
            return now
        if registration.predicate.kind == "timer":
            return min(registration.predicate.at, expires_at)
        return min(now + registration.every, expires_at)

    @staticmethod
    def _deadline_for(registration: WatchRegistration, now: datetime) -> timedelta | None:
        """The caller's deadline, the default for this predicate, or ``None`` for a standing watch.

        Every watch an agent HALTED on gets one — there is no "wait forever" —
        because the expiry message is what hands its session back. The one
        exception is the standing ``ticket`` watch, which nobody halts on.
        Open-ended predicates default to ``DEFAULT_DEADLINE``. A timer defaults
        to its own instant plus a small grace instead: its settle time is known,
        and capping it at the open-ended default would turn every long timer
        into a guaranteed expiry.
        """
        if registration.predicate.kind == "ticket":
            # Standing: nobody halted on it, so there is no turn to hand back.
            # Its workspace's lifecycle ends it (see TicketSubscriptions).
            return None
        if registration.deadline is not None:
            return registration.deadline
        if registration.predicate.kind == "timer":
            remaining = max(registration.predicate.at - now, timedelta(0))
            return min(remaining + _TIMER_GRACE, MAX_DEADLINE)
        return DEFAULT_DEADLINE

    @staticmethod
    def _interval_of(row: WatchView) -> timedelta:
        """How long until this row should be looked at again: what it asked for.

        Stored on the row rather than derived. It used to be recomputed as
        ``next_due - created_at``, which GROWS every time the watch is re-armed,
        so a watch asking for 30 s was probed at 30, 60, 120, 240, 480 s — and a
        long wait could learn its subject settled many minutes late.
        """
        return max(row.every, MIN_INTERVAL)

    @staticmethod
    def _describe(row: WatchView) -> str:
        if row.note:
            return f"You registered it with the note: {row.note!r}."
        return ""

    @staticmethod
    def _subject(row: WatchView) -> str:
        """What was being waited on, in words a fresh turn can act on."""
        p = row.predicate
        if p.kind == "ci":
            return f"CI checks on {p.owner}/{p.repo}@{p.head_sha} ({p.provider})"
        if p.kind == "command":
            return f"the command {' '.join(p.argv)!r} to exit with {p.terminal_exit_codes}"
        if p.kind == "ticket":
            return f"changes to {p.provider} ticket #{p.ticket_id}"
        return f"the timer set for {p.at.isoformat()}"


def _duration(span: timedelta) -> str:
    """``15m`` / ``1h 30m`` / ``45s`` — how long the caller was kept waiting."""
    total = int(span.total_seconds())
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


__all__ = ["DeliverFn", "WatchScheduler"]
