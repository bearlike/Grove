"""The watch backbone's guarantees, written from the incidents they prevent.

Every test here corresponds to a way an agent that HALTED could be left waiting
forever, or to a way the mechanism could cost more than the sleep loop it
replaces. The resource tests count probes and file writes rather than timing
anything, because a timing assertion on a busy host measures the host.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from grove.core.contracts.mailboxes import MailboxAddress, MailboxReceipt
from grove.core.contracts.watches import (
    DEFAULT_DEADLINE,
    CommandPredicate,
    TimerPredicate,
    WatchOutcome,
    WatchRegistration,
    WatchView,
)
from grove.core.errors import GroveError
from grove.core.watches.log import WatchLog
from grove.core.watches.mailbox import MailboxWatchCourier
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.watcher import TimerWatcher, Watcher, WatcherRegistry

WS = "a" * 32
OTHER = "b" * 32


class FakeClock:
    """A clock the test moves by hand, so nothing sleeps for real."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class CountingWatcher(Watcher[CommandPredicate]):
    """Answers `None` until told otherwise, and counts every evaluation."""

    kind = "command"

    def __init__(self, settle_after: int | None = None) -> None:
        self.calls = 0
        self._settle_after = settle_after

    def observe(self, predicate, now):
        self.calls += 1
        if self._settle_after is not None and self.calls >= self._settle_after:
            return WatchOutcome(ok=True, summary="settled")
        return None


class RecordingCourier:
    """Stands in for the mailbox, recording what would have been delivered."""

    def __init__(self, receipt: str = "delivered", detail: str | None = None) -> None:
        self.sent: list[tuple[str, WatchOutcome]] = []
        self._receipt = receipt
        self._detail = detail

    def __call__(self, watch, outcome):
        self.sent.append((watch.id, outcome))
        return self._receipt, self._detail


class ExplodingCourier:
    """A transport that dies mid-send — the crash-shaped delivery case."""

    def __call__(self, watch, outcome):
        raise RuntimeError("transport went away")


def _registration(**kwargs) -> WatchRegistration:
    base = {
        "recipient": MailboxAddress(workspace_id=WS),
        "predicate": CommandPredicate(argv=["true"], workspace_id=WS),
        "every": timedelta(seconds=30),
        "deadline": timedelta(hours=1),
    }
    base.update(kwargs)
    return WatchRegistration(**base)


async def _build(tmp_path, *, watcher=None, courier=None, clock=None):
    clock = clock or FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    watcher = watcher or CountingWatcher()
    scheduler = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([watcher, TimerWatcher()]),
        deliver=courier or RecordingCourier(),
        clock=clock,
    )
    scheduler.prime()
    return scheduler, log, watcher, clock


# ─── the idle guarantee ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_empty_registry_schedules_no_wakeup_at_all(tmp_path):
    """The whole cost argument: waiting must be free.

    ``None`` means the loop waits on an event rather than a clock, so no timer
    exists to fire. A scheduler that returned any number here would be waking
    up forever to discover it has nothing to do — the periodic sweep this
    design exists to avoid.
    """
    scheduler, _log, watcher, clock = await _build(tmp_path)
    try:
        assert scheduler.seconds_until_next() is None
        clock.advance(3600)
        assert await scheduler.tick() == 0
        assert watcher.calls == 0
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_many_pending_watches_cost_nothing_until_one_is_due(tmp_path):
    """Cost tracks what is DUE, not what exists.

    Fifty watches on 30s intervals must evaluate ZERO times while only ten
    seconds have passed. This drives ``tick`` directly rather than yielding to
    the event loop: a test that merely awaits real time passes even against a
    scheduler that scans every row, because no real time passes in 0.4s.
    """
    scheduler, _log, watcher, clock = await _build(tmp_path)
    try:
        for _ in range(50):
            scheduler.register(_registration())
        clock.advance(10)
        assert await scheduler.tick() == 0
        assert watcher.calls == 0

        # And when they ARE due, each is evaluated exactly once.
        clock.advance(25)
        assert await scheduler.tick() == 50
        assert watcher.calls == 50
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_the_next_wakeup_is_the_earliest_deadline_not_a_fixed_interval(tmp_path):
    """The heap's minimum IS the wakeup. Nothing polls on a fixed cadence."""
    scheduler, _log, _watcher, _clock = await _build(tmp_path)
    try:
        scheduler.register(_registration(deadline=timedelta(hours=6), every=timedelta(minutes=30)))
        assert scheduler.seconds_until_next() == pytest.approx(1800, abs=1)
        scheduler.register(_registration(every=timedelta(seconds=30)))
        assert scheduler.seconds_until_next() == pytest.approx(30, abs=1)
    finally:
        scheduler.close()


# ─── the wake guarantee ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registering_wakes_a_waiting_scheduler_immediately(tmp_path):
    """A new watch must not wait out somebody else's interval.

    Without the wake event a timer registered to fire now would sit until the
    previously-scheduled watch came due — which for a six-hour watch is six
    hours of an agent halted for nothing.
    """
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, _log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    await scheduler.start()
    try:
        scheduler.register(_registration(deadline=timedelta(hours=6)))
        await asyncio.sleep(0)
        scheduler.register(
            _registration(predicate=TimerPredicate(at=clock.now), deadline=timedelta(hours=1))
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(courier.sent) == 1
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_timer_costs_no_probe(tmp_path):
    """A timer's own instant is its due time, so it is evaluated exactly once."""
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(
            _registration(predicate=TimerPredicate(at=clock.now + timedelta(seconds=60)))
        )
        clock.advance(30)
        await scheduler.tick()
        assert log.get(watch.id).state == "pending"
        clock.advance(31)
        await scheduler.tick()
        assert log.get(watch.id).state == "fired"
        assert len(courier.sent) == 1
    finally:
        scheduler.close()


# ─── the durability guarantees ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_outcome_is_persisted_before_delivery_is_attempted(tmp_path):
    """A crash mid-send must leave the conclusion on disk, not lose it.

    Delivering first and persisting second loses the outcome entirely when the
    process dies between them: the heap entry is gone and nothing records what
    the watch concluded. Here the courier raises, and the row must still say
    fired with an unknown receipt.
    """
    clock = FakeClock()
    scheduler, log, _watcher, _ = await _build(
        tmp_path,
        watcher=CountingWatcher(settle_after=1),
        courier=ExplodingCourier(),
        clock=clock,
    )
    try:
        watch = scheduler.register(_registration())
        clock.advance(31)
        await scheduler.tick()
        row = log.get(watch.id)
        assert row.state == "fired"
        assert row.outcome is not None
        assert row.receipt == "unknown"
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_an_unknown_receipt_is_never_retried(tmp_path):
    """`unknown` means the bytes may have landed. A second copy is worse."""
    clock = FakeClock()
    scheduler, log, _watcher, _ = await _build(
        tmp_path,
        watcher=CountingWatcher(settle_after=1),
        courier=ExplodingCourier(),
        clock=clock,
    )
    try:
        watch = scheduler.register(_registration())
        clock.advance(31)
        await scheduler.tick()
        clock.advance(600)
        await scheduler.tick()
        assert log.get(watch.id).state == "fired"
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_dead_recipient_settles_undeliverable_rather_than_failing(tmp_path):
    """The predicate may have settled correctly; the agent had simply gone.

    That is its own state — not a failed watch, and not a delivered one — and
    it must be visible rather than silently dropped.
    """
    clock = FakeClock()
    scheduler, log, _watcher, _ = await _build(
        tmp_path,
        watcher=CountingWatcher(settle_after=1),
        courier=RecordingCourier(receipt="rejected", detail="not_live"),
        clock=clock,
    )
    try:
        watch = scheduler.register(_registration())
        clock.advance(31)
        await scheduler.tick()
        row = log.get(watch.id)
        assert row.state == "undeliverable"
        assert row.receipt_detail == "not_live"
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_deadline_delivers_an_expiry_rather_than_going_silent(tmp_path):
    """An agent that halted must never be left with no signal.

    This is the guarantee a sleep loop cannot make: a killed shell loop simply
    vanishes, while an expired watch says so.
    """
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(_registration(deadline=timedelta(minutes=1)))
        clock.advance(120)
        await scheduler.tick()
        row = log.get(watch.id)
        assert row.state == "expired"
        assert len(courier.sent) == 1
        assert courier.sent[0][1].ok is False
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_an_unevaluable_watcher_never_kills_the_loop(tmp_path):
    """One broken watcher must not stop every other watch on the host."""

    class Broken(Watcher[CommandPredicate]):
        kind = "command"

        def observe(self, predicate, now):
            raise RuntimeError("forge exploded")

    clock = FakeClock()
    scheduler, log, _watcher, _ = await _build(tmp_path, watcher=Broken(), clock=clock)
    try:
        watch = scheduler.register(_registration())
        clock.advance(31)
        await scheduler.tick()
        # Still pending — a failure to observe is not an outcome.
        assert log.get(watch.id).state == "pending"
    finally:
        scheduler.close()


# ─── the registry itself ────────────────────────────────────────────────────


def test_a_corrupt_registry_raises_rather_than_reading_as_empty(tmp_path):
    """Empty and unreadable are different facts and must not be conflated."""
    path = tmp_path / "watches.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GroveError, match="corrupt watch log"):
        WatchLog(path).all()


def test_a_version_mismatch_refuses_rather_than_discarding(tmp_path):
    path = tmp_path / "watches.json"
    path.write_text(json.dumps({"version": 99, "watches": {}}), encoding="utf-8")
    with pytest.raises(GroveError, match="refusing to treat it as empty"):
        WatchLog(path).all()


def test_rescheduling_to_the_same_instant_writes_nothing(tmp_path):
    """The one write on the polling path must not fire once per tick.

    A six-hour watch at 30s is 720 ticks; rewriting the file on each is how a
    mechanism that should be free becomes a disk-I/O problem.
    """
    clock = FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    scheduler = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    watch = scheduler.register(_registration())
    due = clock.now + timedelta(seconds=30)
    log.reschedule(watch.id, due)
    before = log.path.stat().st_mtime_ns
    log.reschedule(watch.id, due)
    assert log.path.stat().st_mtime_ns == before


def test_settled_rows_are_retained_then_pruned_by_age(tmp_path):
    """The row is the record that a callback already went out."""
    clock = FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    scheduler = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    watch = scheduler.register(_registration())
    log.settle(watch.id, state="fired", now=clock.now, outcome=WatchOutcome(ok=True, summary="x"))
    assert log.get(watch.id) is not None
    assert log.prune(clock.now + timedelta(days=1)) == 0
    assert log.prune(clock.now + timedelta(days=8)) == 1
    assert log.get(watch.id) is None


def test_startup_prunes_terminal_rows_after_the_diagnostic_retention_window(tmp_path):
    """A cancelled callback remains inspectable for seven days, then is reaped."""
    clock = FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    seed = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    watch = seed.register(_registration())
    seed.cancel(watch.id)
    clock.advance(timedelta(days=8).total_seconds())

    recovered = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    try:
        assert recovered.prime() == 0
        assert log.get(watch.id) is None
    finally:
        recovered.close()


@pytest.mark.asyncio
async def test_a_reload_spreads_recovered_watches_across_their_interval(tmp_path):
    """Forty recovered watches must not all become due in the same instant."""
    clock = FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    seed = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    for _ in range(40):
        seed.register(_registration())

    revived = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    revived.prime()
    try:
        due = {entry.when for entry in revived._heap}
        assert len(due) > 1, "every recovered watch became due at the same instant"
    finally:
        revived.close()


@pytest.mark.asyncio
async def test_cancelling_during_a_probe_does_not_deliver_its_stale_outcome(tmp_path):
    """A lifecycle cancellation must win even after evaluation has begun."""

    class BlockingWatcher(Watcher[CommandPredicate]):
        kind = "command"

        def __init__(self) -> None:
            self.started = Event()
            self.release = Event()

        def observe(self, predicate, now):
            self.started.set()
            assert self.release.wait(timeout=1)
            return WatchOutcome(ok=True, summary="settled")

    clock = FakeClock()
    courier = RecordingCourier()
    watcher = BlockingWatcher()
    scheduler, log, _watcher, _ = await _build(
        tmp_path, watcher=watcher, courier=courier, clock=clock
    )
    try:
        watch = scheduler.register(_registration())
        clock.advance(31)
        tick = asyncio.create_task(scheduler.tick())
        for _ in range(20):
            if watcher.started.is_set():
                break
            await asyncio.sleep(0)
        assert watcher.started.is_set()

        await asyncio.to_thread(scheduler.cancel, watch.id)
        watcher.release.set()
        await tick

        assert log.get(watch.id).state == "cancelled"
        assert courier.sent == []
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_cancelling_settles_the_row_and_stops_evaluation(tmp_path):
    """A cancelled watch must never be probed or delivered again.

    Asserting only the stored state is vacuous: a scheduler that kept probing a
    cancelled row would still show ``cancelled`` in the log. The evaluation
    count and the courier are what actually pin it — so this drives the watch
    past its due time and demands that nothing happened.
    """
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(_registration())
        scheduler.cancel(watch.id)
        assert log.get(watch.id).state == "cancelled"
        assert log.active() == []

        clock.advance(120)
        await scheduler.tick()
        assert watcher.calls == 0, "a cancelled watch was still evaluated"
        assert courier.sent == [], "a cancelled watch still delivered a callback"
    finally:
        scheduler.close()


# ─── the contract's own refusals ────────────────────────────────────────────


def test_an_interval_below_the_floor_is_refused():
    """One registration must not be able to become a rate-limit incident."""
    with pytest.raises(ValueError, match="at least"):
        _registration(every=timedelta(seconds=1))


def test_a_deadline_beyond_the_ceiling_is_refused():
    with pytest.raises(ValueError, match="may not exceed"):
        _registration(deadline=timedelta(days=30))


def test_a_flag_shaped_program_is_refused():
    """The value-becomes-syntax class, at the registration boundary."""
    with pytest.raises(ValueError, match="cannot begin with"):
        CommandPredicate(argv=["--upload-pack=evil"], workspace_id=WS)


# ─── the deadline guarantee: no watch can stall a halted session ────────────


@pytest.mark.asyncio
async def test_an_open_ended_watch_with_no_deadline_expires_at_the_default(tmp_path):
    """Omitting the deadline must NOT mean "wait forever".

    The agent halted on this watch. A subject that never settles has to hand
    the session back at the default, with a message saying the condition was
    not met — pinned at the boundary so the default cannot silently regrow.
    """
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(
            WatchRegistration(
                recipient=MailboxAddress(workspace_id=WS),
                predicate=CommandPredicate(argv=["true"], workspace_id=WS),
            )
        )
        assert watch.expires_at - watch.created_at == DEFAULT_DEADLINE

        clock.advance(DEFAULT_DEADLINE.total_seconds() - 1)
        await scheduler.tick()
        assert log.get(watch.id).state == "pending"
        assert courier.sent == []

        clock.advance(2)
        await scheduler.tick()
        assert log.get(watch.id).state == "expired"
        assert len(courier.sent) == 1
        outcome = courier.sent[0][1]
        assert outcome.ok is False
        assert "NOT met" in outcome.summary
        assert "15m" in outcome.summary
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_the_caller_sets_the_deadline_and_it_is_honoured_exactly(tmp_path):
    """The agent owns the maximum wait; a longer deadline must not be cut short."""
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(_registration(deadline=timedelta(minutes=45)))
        clock.advance(DEFAULT_DEADLINE.total_seconds() + 60)
        await scheduler.tick()
        assert log.get(watch.id).state == "pending", "a 45m deadline expired at the default"

        clock.advance(45 * 60)
        await scheduler.tick()
        assert log.get(watch.id).state == "expired"
        assert "45m" in courier.sent[0][1].summary
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_long_timer_with_no_deadline_fires_rather_than_expiring(tmp_path):
    """A timer's settle time is known, so the open-ended default must not cap it.

    Otherwise every timer longer than 15 minutes would report "deadline reached"
    instead of firing — the watch would be guaranteed to fail.
    """
    clock = FakeClock()
    courier = RecordingCourier()
    scheduler, log, _watcher, _ = await _build(tmp_path, courier=courier, clock=clock)
    try:
        watch = scheduler.register(
            WatchRegistration(
                recipient=MailboxAddress(workspace_id=WS),
                predicate=TimerPredicate(at=clock.now + timedelta(minutes=40)),
            )
        )
        clock.advance(40 * 60)
        await scheduler.tick()
        assert log.get(watch.id).state == "fired"
        assert courier.sent[0][1].ok is True
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_watch_is_rechecked_at_the_interval_it_asked_for(tmp_path):
    """The cadence must not drift.

    It was once derived from `next_due - created_at`, which grows on every re-arm,
    so a 30s watch was probed at 30, 60, 120, 240, 480s and learned its subject
    had settled minutes late.
    """
    clock = FakeClock()
    scheduler, _log, watcher, _ = await _build(tmp_path, clock=clock)
    try:
        scheduler.register(_registration(every=timedelta(seconds=30), deadline=timedelta(hours=1)))
        for _ in range(10 * 60):
            clock.advance(1)
            await scheduler.tick()
        assert watcher.calls == 20
    finally:
        scheduler.close()


@pytest.mark.asyncio
async def test_a_reload_keeps_each_watchs_own_interval(tmp_path):
    """A restart re-arms on the stored cadence, not a guessed one."""
    clock = FakeClock()
    log = WatchLog(tmp_path / "watches.json")
    seed = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    seed.register(_registration(every=timedelta(minutes=5), deadline=timedelta(hours=1)))
    revived = WatchScheduler(
        log=log,
        watchers=WatcherRegistry([CountingWatcher(), TimerWatcher()]),
        deliver=RecordingCourier(),
        clock=clock,
    )
    revived.prime()
    try:
        row = log.active()[0]
        assert row.every == timedelta(minutes=5)
        (entry,) = revived._heap
        assert entry.when - clock.now <= timedelta(minutes=5)
    finally:
        revived.close()


def test_an_expiry_and_a_failed_result_read_differently_in_the_mailbox():
    """ "We gave up waiting" and "it finished red" ask for opposite next steps.

    Both carry ``ok=False``, so a subject keyed on ``ok`` alone told a recipient
    its build had failed when Grove had merely stopped waiting for it.
    """

    class Capture:
        def __init__(self):
            self.sent = []

        def send(self, request):
            self.sent.append(request)
            return MailboxReceipt(stage="delivered", created_at=datetime.now(UTC))

    delivery = Capture()
    courier = MailboxWatchCourier(delivery)
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    base = WatchView(
        id="wch_" + "c" * 32,
        recipient=MailboxAddress(workspace_id=WS),
        predicate=CommandPredicate(argv=["true"], workspace_id=WS),
        state="expired",
        note="ci on my PR",
        created_at=now,
        expires_at=now + DEFAULT_DEADLINE,
    )
    failure = WatchOutcome(ok=False, summary="failed: lint")

    courier(base, failure)
    courier(base.model_copy(update={"state": "fired"}), failure)

    expired, red = delivery.sent
    assert "deadline reached" in expired.subject.lower()
    assert "deadline" not in red.subject.lower()
    assert "Nothing is still being checked" in expired.body
    assert expired.body.count("ci on my PR") <= 1


# ─── the per-workspace listing ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workspace_listing_is_scoped_by_recipient(tmp_path):
    """A workspace's page lists the watches that call back INTO it, settled ones
    included, and no stranger's — even one whose predicate runs in its tree.

    The cross-wired row is the load-bearing fixture: OTHER registered a command
    that runs in WS's worktree but calls back to OTHER. Keying the filter on the
    predicate's workspace instead of the recipient would list it on WS's page.
    """
    scheduler, _log, _watcher, _clock = await _build(tmp_path)
    mine = scheduler.register(_registration(note="mine"))
    settled = scheduler.register(_registration(note="mine, settled"))
    scheduler.cancel(settled.id)
    scheduler.register(
        _registration(
            recipient=MailboxAddress(workspace_id=OTHER),
            predicate=CommandPredicate(argv=["true"], workspace_id=WS),
            note="theirs",
        )
    )

    listed = scheduler.list(workspace_id=WS).watches

    assert {row.note for row in listed} == {"mine", "mine, settled"}
    assert {row.id for row in listed} >= {mine.id, settled.id}
    assert len(scheduler.list().watches) == 3
