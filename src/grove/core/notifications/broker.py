"""The notification broker — a debounced edge-trigger over the activity bus.

``NotificationBroker`` is the atomic owner of the channel set plus the
edge/debounce/dedupe memory. It **subscribes to the existing ``ActivityService``
delta bus** and adds *no* new status computation (the KISS rule): the
activity service already blends every session's :class:`AgentActivityState` and
already bridges every ``WorkspaceEvent`` onto the same bus, so the broker only
has to notice the edges in what is already flowing past it.

Three triggers, one per thing a human actually wants pushed:

- **agent-state edge** — a session crossed into a notifiable state (WAITING =
  turn finished, BLOCKED, ERROR). Debounced per workspace. A WAITING edge is
  additionally held back while a known tracker still shows work in flight
  (see :meth:`_is_busy`) and then made to sit through
  ``cfg.waiting_quiet_minutes`` of continued silence before it actually
  dispatches — see :meth:`due_quiet` and "the quiet-window push" below.
- **question edge** — a new *unanswered* question appeared. Deduped by question
  id, never by time: a second question is a second thing the human owes an
  answer to, and dropping it would strand the agent.
- **lifecycle edge** — the workspace itself broke, went offline, or was
  orphaned (the ``workspace_changed`` deltas the broker used to discard).

## The quiet-window push

A WAITING transcript state means the top-level turn stopped generating — not
that the task is finished. Grove already computes three independent "still
working" signals and none of them needed a new subsystem:
``AgentActivity.active_subagents`` (an in-session Task/Agent tool_use spawned
but not yet returned), the hook's live per-agent ``FleetSummary``
(``row.fleet``, which sees a sub-agent before its own transcript file
necessarily exists), and the harness's own steer queue (``row.queue``,
``WorkspaceQueueView``'s live count). :meth:`_is_busy` is the OR of the three;
while it is true, a WAITING edge does not queue at all — the effective state
fed to the edge detector reads as WORKING instead (see :meth:`_effective_state`),
so the rising edge simply has not happened yet.

None of those three trackers can see an arbitrary backgrounded shell command
(``Bash ... run_in_background``): the tool call returns immediately with a
shell id and Grove has no close event to watch for it ever finishing. That gap
is exactly why ``cfg.waiting_quiet_minutes`` exists — the user's own proposed
fallback, "N minutes of complete silence since the last full completion". Once
:meth:`_is_busy` agrees nothing is left, the edge does not dispatch immediately;
it is parked in ``_pending_quiet`` with the moment it settled, and only leaves
that dict — via :meth:`due_quiet` — once that much real wall-clock time has
passed with **no** intervening delta un-settling it (see the cancellation
clause in :meth:`_state_edge`, keyed on "previous was WAITING, effective no
longer is"). ``waiting_quiet_minutes=0`` disables the wait and restores the
old immediate-fire behaviour, with the busy-gate still applied.

:meth:`due_quiet` mirrors :meth:`evaluate`: pure given ``now``, no I/O, fully
testable with a fake clock. The one genuinely new piece of I/O is
:meth:`bind`'s self-rescheduling ``Timer`` — nothing else on the bus can ever
wake the broker to check a workspace that produces no further deltas, which is
precisely the silent case this feature exists for. It arms only while
``_pending_quiet`` is non-empty and disarms itself the moment it drains, so a
fleet with nothing queued for the quiet window costs nothing between ticks.

Decision and I/O are deliberately split (CLAUDE.md, "side effects at the edges,
pure logic in the middle"):

- :meth:`evaluate` — **pure** given the injected clock. Folds one
  ``DashboardDelta`` against the memory and returns the ``Notification``s to
  send. No I/O, fully unit-testable.
- :meth:`dispatch` — fans one notification out to every channel, each wrapped in
  the best-effort guard. The bus callback runs ``evaluate`` synchronously (fast,
  on the poll thread) and submits ``dispatch`` to a single-worker pool, so
  channel HTTP never blocks or breaks the activity poll.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock, Timer
from typing import Any

from loguru import logger

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivityState
from grove.core.config import NotificationsConfig
from grove.core.notifications.channel import (
    Notification,
    NotificationChannel,
    WorkspaceIdentity,
)
from grove.core.notifications.gotify import GotifyNotificationChannel
from grove.core.notifications.webhook import WebhookNotificationChannel


@dataclass(slots=True, frozen=True)
class _PendingQuiet:
    """A WAITING push held back, waiting out the quiet window before it fires.

    Built once, at the moment every known busy-tracker first agreed nothing was
    left — ``occurred_at`` on the notification is that moment, not whenever the
    sweep eventually gets to it, so the phrase reads correctly ("finished its
    turn") relative to when it actually did.
    """

    notification: Notification
    settled_since: datetime


class NotificationBroker:
    """Owns channels + edge/dedupe memory; turns activity deltas into pushes.

    Long-lived alongside the daemon. ``bind`` wires it to an ``ActivityService``
    bus and starts the dispatch worker; ``close`` unwinds both and releases the
    channels. Built via :meth:`from_config` — ``None`` when notifications are off
    or no channel is configured, so the daemon stays oblivious to the policy.
    """

    # Each notifiable channel: its config sub-section attribute + the factory that
    # consumes that sub-section. Adding email/Slack/Web-Push is one entry here plus
    # the config field and the channel class — no other site changes. Typed as a
    # factory callable because each channel's __init__ takes its own config type.
    _CHANNEL_TYPES: tuple[tuple[str, Callable[[Any], NotificationChannel]], ...] = (
        ("gotify", GotifyNotificationChannel),
        ("webhook", WebhookNotificationChannel),
    )

    # How often the quiet-window timer re-checks ``_pending_quiet`` — a
    # mechanism-only granularity (accuracy against a ~15 minute default), not a
    # user policy, so it is a constant rather than another config field.
    _SWEEP_INTERVAL: timedelta = timedelta(seconds=60)

    def __init__(
        self,
        *,
        channels: Sequence[NotificationChannel],
        notify_states: frozenset[AgentActivityState] = frozenset(Notification.REASONS),
        notify_questions: bool = True,
        notify_lifecycle: frozenset[str] = frozenset(Notification.LIFECYCLE),
        debounce: timedelta = timedelta(seconds=30),
        warmup: timedelta = timedelta(seconds=5),
        waiting_quiet: timedelta = timedelta(minutes=15),
        deep_link_base_url: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._channels = tuple(channels)
        # Only states/events with a reason phrase can fire; intersect so a
        # misconfigured WORKING (or an event we have no words for) can never push.
        self._notify_states = notify_states & frozenset(Notification.REASONS)
        self._notify_questions = notify_questions
        self._notify_lifecycle = notify_lifecycle & frozenset(Notification.LIFECYCLE)
        self._debounce = debounce
        self._warmup = warmup
        self._waiting_quiet = waiting_quiet
        self._deep_link_base = deep_link_base_url.rstrip("/")
        self._clock = clock if clock is not None else self._utcnow
        # Per-session last observed state — the state edge detector. Per-session
        # open-question ids — the question dedupe. Per-workspace last fire time —
        # the debounce. Per-workspace identity — what a lifecycle delta (which
        # carries no activity row) renders from. Unbounded like the registry:
        # loopback, small N.
        self._last_state: dict[str, AgentActivityState] = {}
        self._open_questions: dict[str, frozenset[str]] = {}
        self._last_fired: dict[str, datetime] = {}
        self._identity: dict[str, WorkspaceIdentity] = {}
        # Per-workspace WAITING push held back for the quiet window — see
        # "The quiet-window push" above. Same unbounded-but-small contract as
        # the maps above.
        self._pending_quiet: dict[str, _PendingQuiet] = {}
        self._sweep_timer: Timer | None = None
        # When this broker stops treating an unseen session as pre-existing.
        # Set on the first fold rather than at construction, because a broker
        # built early and bound late would otherwise burn its whole window
        # before a single delta arrived.
        self._warm_at: datetime | None = None
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._unsub: Callable[[], None] | None = None

    @classmethod
    def from_config(cls, cfg: NotificationsConfig) -> NotificationBroker | None:
        """Build a broker from config, or ``None`` when there is nothing to do.

        ``None`` when notifications are disabled, or enabled with no channel
        configured (nothing to deliver to). Each channel reads its own secret
        from the environment at construction (the env-ref pattern), so this never
        sees a token. The single construction edge where config strings coerce to
        the engine's own enums.
        """
        if not cfg.enabled:
            return None
        channels = [
            channel_cls(getattr(cfg, attr))
            for attr, channel_cls in cls._CHANNEL_TYPES
            if getattr(cfg, attr).enabled
        ]
        if not channels:
            logger.warning("notifications enabled but no channel configured; broker disabled")
            return None
        if cfg.deep_link_is_loopback:
            # The push still lands; only the *tap* dies (the phone resolves
            # localhost to itself). Silent by nature, so say it once at startup.
            logger.warning(
                "notifications: deep_link_base_url={} is loopback — a push tapped on "
                "another device cannot reach it. Set notifications.deep_link_base_url "
                "to an origin your phone can resolve.",
                cfg.deep_link_base_url,
            )
        return cls(
            channels=channels,
            notify_states=frozenset(AgentActivityState(name) for name in cfg.on),
            notify_questions=cfg.on_question,
            notify_lifecycle=frozenset(cfg.on_lifecycle),
            debounce=timedelta(seconds=cfg.debounce_seconds),
            waiting_quiet=timedelta(minutes=cfg.waiting_quiet_minutes),
            deep_link_base_url=cfg.deep_link_base_url,
        )

    @property
    def channels(self) -> tuple[NotificationChannel, ...]:
        return self._channels

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Subscribe to a delta bus and start the dispatch worker.

        Takes the bus's ``subscribe`` callable (``ActivityService.subscribe``)
        rather than the service itself, so the broker depends only on the bus
        shape and tests drive it with a bare stub. Does NOT start the
        quiet-window ``Timer`` eagerly — ``_on_delta`` arms it lazily the first
        time something is actually parked, and it disarms itself once drained.
        """
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-notify")
        self._unsub = subscribe(self._on_delta)

    def close(self) -> None:
        """Unsubscribe, stop the quiet-window timer, drain the worker, release channels."""
        if self._unsub is not None:
            with contextlib.suppress(Exception):
                self._unsub()
            self._unsub = None
        with self._lock:
            timer, self._sweep_timer = self._sweep_timer, None
        if timer is not None:
            timer.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        for channel in self._channels:
            with contextlib.suppress(Exception):
                channel.close()

    # ─── pure decision ───────────────────────────────────────────────────────

    def evaluate(self, delta: DashboardDelta) -> list[Notification]:
        """Fold one delta into notifications. Pure given the clock; updates memory.

        The two delta kinds carry different payloads, so each gets its own
        detector: ``session_activity`` carries the recomputed ``WorkspaceActivity``
        (questions + per-session state to edge-detect against), while
        ``workspace_changed`` carries only an id + the manager's event detail.
        """
        now = self._clock()
        with self._lock:
            if delta.kind == "workspace_changed":
                return self._evaluate_lifecycle(delta, now)
            row = delta.workspace
            if row is None:
                return []
            self._identity[row.state.id] = WorkspaceIdentity.from_state(row.state)
            out: list[Notification] = []
            for session in row.sessions:
                out.extend(self._evaluate_session(row, session, now))
            return out

    def due_quiet(self, now: datetime | None = None) -> list[Notification]:
        """Pop and return every parked WAITING push whose quiet window elapsed.

        Mirrors :meth:`evaluate`: pure given ``now`` (defaults to the injected
        clock), no I/O, deterministic over ``_pending_quiet`` — a test can call
        this directly with a fake clock and never touch the real ``Timer``. The
        one real-time caller is :meth:`_quiet_tick`, the I/O edge :meth:`bind`
        arms; nothing on the delta bus can otherwise wake the broker to check a
        workspace that stays perfectly silent, which is the exact case this
        exists for.
        """
        moment = now if now is not None else self._clock()
        with self._lock:
            due = [
                workspace_id
                for workspace_id, pending in self._pending_quiet.items()
                if moment - pending.settled_since >= self._waiting_quiet
            ]
            fired: list[Notification] = []
            for workspace_id in due:
                pending = self._pending_quiet.pop(workspace_id)
                self._last_fired[workspace_id] = moment
                fired.append(pending.notification)
            return fired

    # ─── internal: the three detectors (pure, called under the lock) ─────────

    def _evaluate_session(
        self, row: WorkspaceActivity, session: SessionActivity, now: datetime
    ) -> list[Notification]:
        """One session's edges. A question outranks the state edge behind it.

        Both are the same attention episode — an agent that asks a question is
        BLOCKED *because of* that question — so firing both would buzz twice for
        one event. The question notification is strictly richer (it carries the
        prompt and the options), so it wins and stamps the debounce that keeps
        the state edge quiet.

        **Both detectors run before either notification is chosen**, because each
        one *folds its own memory* on every tick: skipping ``_state_edge`` when a
        question wins would leave that session's last-seen state stale (WORKING),
        so the WORKING→BLOCKED edge behind the question stays pending and rings a
        second, redundant time the moment the debounce window lapses. Suppressing
        the state edge means recording it, not merely out-voting it.
        """
        state = self._state_edge(row, session, now)
        question = self._question_edge(row, session, now)
        if question is not None:
            return [question]
        return [state] if state is not None else []

    def _is_cold(self, now: datetime) -> bool:
        """Whether an unseen session should be read as pre-existing rather than new.

        **The storm this guards against is a property of the BROKER, not of a
        session**, and conflating the two silently cost every brand-new
        workspace its first question push: a session opened by `create` is
        unseen for the same reason a session that predates a daemon restart is,
        so seeding on "unseen" swallowed exactly the case the human most needs —
        an agent that reads its task and immediately asks something.

        A window is the right shape here even though the question *dedupe*
        deliberately refuses one. Dedupe is exact because "the same question id"
        is exact; a restart burst is inherently a span of startup time with no
        exact marker in the data, so the matching guard is temporal. It opens on
        the first fold rather than at construction, so a broker built early and
        bound late does not spend the window before any delta arrives.
        """
        if self._warm_at is None:
            self._warm_at = now + self._warmup
        return now < self._warm_at

    def _question_edge(
        self, row: WorkspaceActivity, session: SessionActivity, now: datetime
    ) -> Notification | None:
        """Fire on a question id we have never seen open before.

        **Deduped by id, not by time** — the debounce window is deliberately not
        consulted. Two questions 5 seconds apart are two answers the agent is
        waiting on; suppressing the second would leave it stranded with the human
        believing they were done. Re-asking the *same* id can never re-fire, so
        the storm guard is exact rather than temporal.

        First observation of a session seeds silently **only while the broker is
        cold** (:meth:`_is_cold`) — a daemon restart must not re-push every
        question already on screen, but a session that opens after the broker is
        warm is genuinely new and its first question is genuinely owed.
        """
        session_id = session.session.session_id
        open_now = Notification.unanswered(session)
        open_ids = frozenset(question.id for question in open_now)
        seen = self._open_questions.get(session_id)
        self._open_questions[session_id] = open_ids
        if not self._notify_questions or (seen is None and self._is_cold(now)):
            return None  # disabled, or a question that predates this broker
        # A warm broker meeting a session for the first time has seen nothing,
        # so every open question is fresh — that IS the new-workspace case.
        already = seen or frozenset()
        fresh = tuple(question for question in open_now if question.id not in already)
        if not fresh:
            return None
        self._last_fired[row.state.id] = now  # the state edge behind it stays quiet
        # A question means the turn is not actually over — a WAITING push parked
        # for this workspace's quiet window was wrong the moment it stopped being
        # true, so drop it rather than let the sweep confirm a stale completion.
        self._pending_quiet.pop(row.state.id, None)
        return Notification.for_questions(
            row, session, questions=fresh, deep_link_base=self._deep_link_base, now=now
        )

    def _is_busy(self, row: WorkspaceActivity, session: SessionActivity) -> bool:
        """Whether a known tracker says real work is still behind a WAITING turn.

        The OR of three signals Grove already computes, none of them new: the
        in-session sub-agent count (``active_subagents`` — a Task/Agent
        ``tool_use`` spawned but not yet returned), the hook's live per-agent
        fleet (``row.fleet`` — sees a sub-agent before its own transcript file
        necessarily exists), and the harness's own steer queue (``row.queue`` —
        a message typed while the agent was busy). Deliberately blind to an
        arbitrary backgrounded shell command: ``Bash ... run_in_background``
        returns its ``tool_result`` immediately, so there is no open tool call
        left to count — see ``cfg.waiting_quiet_minutes`` for the fallback that
        covers exactly that gap.
        """
        if session.activity.active_subagents > 0:
            return True
        if row.fleet is not None and row.fleet.active > 0:
            return True
        return row.queue is not None and row.queue.pending > 0

    def _effective_state(
        self, row: WorkspaceActivity, session: SessionActivity, current: AgentActivityState
    ) -> AgentActivityState:
        """The state fed to the edge detector: WAITING reads as WORKING while busy.

        The turn genuinely stopped generating (``current`` really is WAITING),
        but a human-facing "done" is not — a top-level turn can end while an
        async sub-agent spawn keeps running. Substituting WORKING (itself never
        notifiable) means the rising edge simply has not happened yet, with no
        second notion of "state" to keep in sync elsewhere.
        """
        if current is AgentActivityState.WAITING and self._is_busy(row, session):
            return AgentActivityState.WORKING
        return current

    def _state_edge(
        self, row: WorkspaceActivity, session: SessionActivity, now: datetime
    ) -> Notification | None:
        """Fire on the **rising edge** into a notifiable state, once per episode.

        Two guards stop notification storms:
        - **First observation seeds silently.** A session first seen already in
          WAITING (a daemon restart mid-turn) records its state but does not
          fire — otherwise every already-finished workspace buzzes on boot. This
          is also what keeps the quiet-window push idempotent across a restart:
          the very first post-restart observation always has ``previous is
          None``, so it can never enter :meth:`_queue_quiet` no matter how long
          the workspace had already been quiet before the daemon (re)started —
          only a genuine transition observed AFTER this broker came up can.
        - **Per-workspace debounce.** After a fire, that workspace is quiet for
          ``debounce`` regardless of session flapping (WAITING→WORKING→WAITING on
          a tool round-trip), so the phone buzzes once per attention episode.

        WAITING is special-cased twice more, both driven by
        :meth:`_effective_state`: it never becomes a rising edge at all while
        :meth:`_is_busy` says so (the edge just hasn't happened yet), and once it
        does, it is *parked* rather than fired immediately (:meth:`_queue_quiet`)
        — dispatch waits for :meth:`due_quiet` to confirm the quiet window
        elapsed with nothing un-settling it. BLOCKED/ERROR/IDLE are unaffected:
        those already mean "the human is needed right now."
        """
        session_id = session.session.session_id
        current = session.activity.state
        effective = self._effective_state(row, session, current)
        previous = self._last_state.get(session_id)
        self._last_state[session_id] = effective
        if previous is AgentActivityState.WAITING and effective is not AgentActivityState.WAITING:
            # The settled episode a pending quiet-window push was counting on
            # broke before it elapsed (work resumed, or the state moved straight
            # to BLOCKED/ERROR) — drop it; a fresh completion must re-settle from
            # scratch, and the sweep must not confirm a completion that stopped
            # being true.
            self._pending_quiet.pop(row.state.id, None)
        if not self._is_rising_edge(effective, previous):
            return None
        if self._debounced(row.state.id, now):
            logger.debug("notify debounced workspace={} state={}", row.state.id, effective)
            return None
        if effective is AgentActivityState.WAITING and self._waiting_quiet > timedelta(0):
            self._queue_quiet(row, session, now)
            return None
        self._last_fired[row.state.id] = now
        return Notification.from_activity(
            row, session, state=current, deep_link_base=self._deep_link_base, now=now
        )

    def _queue_quiet(
        self, row: WorkspaceActivity, session: SessionActivity, settled_at: datetime
    ) -> None:
        """Park a WAITING push instead of firing it — see "The quiet-window push".

        Builds the ``Notification`` now (so ``occurred_at`` reads as the actual
        completion moment, not whenever the sweep gets to it) and holds it in
        ``_pending_quiet`` keyed by workspace, then arms the sweep timer if it
        is not already running.
        """
        notification = Notification.from_activity(
            row,
            session,
            state=session.activity.state,
            deep_link_base=self._deep_link_base,
            now=settled_at,
        )
        self._pending_quiet[row.state.id] = _PendingQuiet(
            notification=notification, settled_since=settled_at
        )

    def _evaluate_lifecycle(self, delta: DashboardDelta, now: datetime) -> list[Notification]:
        """Fire on a notifiable ``WorkspaceEvent`` bridged onto the bus.

        This is the "the work was interrupted" arm: a create that failed at
        ``init_script``, a tmux session that vanished under a running agent, a
        worktree deleted from under a workspace. The delta carries no activity row
        (only an id + the manager's ``detail``), so identity comes from the cache
        the activity arm keeps — falling back to the detail itself for a workspace
        that broke before it ever reported activity.

        Debounced per workspace like a state edge: a failing ``create`` rolls back
        through several phases and emits an error per phase; the human needs the
        first one, not all of them.
        """
        event = delta.detail.get("event", "")
        if event not in self._notify_lifecycle:
            return []
        if self._debounced(delta.workspace_id, now):
            logger.debug("notify debounced workspace={} event={}", delta.workspace_id, event)
            return []
        self._last_fired[delta.workspace_id] = now
        identity = self._identity.get(delta.workspace_id) or WorkspaceIdentity.unresolved(
            delta.workspace_id, repo_root=delta.repo_root, detail=delta.detail
        )
        return [
            Notification.for_lifecycle(
                identity,
                event=event,
                detail=delta.detail,
                deep_link_base=self._deep_link_base,
                now=now,
            )
        ]

    # ─── side effects ────────────────────────────────────────────────────────

    def dispatch(self, notification: Notification) -> None:
        """Fan one notification out to every channel, best-effort per channel.

        Each ``deliver`` is isolated: a channel that raises (HTTP error, bad
        config) logs a structured warning and never blocks the others or the
        worker. This is the boundary the activity path is protected from.
        """
        for channel in self._channels:
            try:
                channel.deliver(notification)
            except Exception as exc:  # one dead sink must not break the others
                logger.warning(
                    "notify channel={} failed for workspace={}: {}",
                    channel.name,
                    notification.workspace_id,
                    exc,
                )
            else:
                logger.info(
                    "notify sent channel={} workspace={} trigger={} event={}",
                    channel.name,
                    notification.workspace_id,
                    notification.trigger,
                    notification.event,
                )

    # ─── internal ────────────────────────────────────────────────────────────

    def _on_delta(self, delta: DashboardDelta) -> None:
        """Bus callback — evaluate on the emitting thread, dispatch off the pool."""
        pool = self._pool
        if pool is None:
            return
        for notification in self.evaluate(delta):
            pool.submit(self.dispatch, notification)
        # Outside evaluate()'s lock scope on purpose: arming a real Timer is I/O,
        # and evaluate() (and the _queue_quiet it can call) must stay pure enough
        # to unit-test with a fake clock and no thread ever spawned.
        self._ensure_quiet_sweep()

    def _ensure_quiet_sweep(self) -> None:
        """Arm the quiet-window timer iff something is parked and none is running.

        Idle otherwise — an empty ``_pending_quiet`` costs one dict check per
        delta and no thread at all, which is what keeps a fleet with nothing
        "about to notify" free (root CLAUDE.md, "gate the body so an idle fleet
        costs nothing"). :meth:`_quiet_tick` is what lets the timer disarm
        itself the moment the dict drains, rather than ticking forever.
        """
        with self._lock:
            if self._sweep_timer is not None or not self._pending_quiet:
                return
            self._sweep_timer = Timer(self._SWEEP_INTERVAL.total_seconds(), self._quiet_tick)
            self._sweep_timer.daemon = True
            self._sweep_timer.name = "grove-notify-quiet"
            self._sweep_timer.start()

    def _quiet_tick(self) -> None:
        """The real-time edge :meth:`due_quiet` needs — dispatch, then re-arm if due."""
        with self._lock:
            self._sweep_timer = None
        pool = self._pool
        if pool is None:
            return
        for notification in self.due_quiet():
            pool.submit(self.dispatch, notification)
        self._ensure_quiet_sweep()

    def _is_rising_edge(
        self, current: AgentActivityState, previous: AgentActivityState | None
    ) -> bool:
        if current not in self._notify_states:
            return False
        if previous is None:
            return False  # first sighting: seed only (avoids restart storms)
        return previous not in self._notify_states

    def _debounced(self, workspace_id: str, now: datetime) -> bool:
        last = self._last_fired.get(workspace_id)
        return last is not None and (now - last) < self._debounce

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(tz=UTC)
