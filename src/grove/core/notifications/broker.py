"""The notification broker — a debounced edge-trigger over the activity bus.

``NotificationBroker`` is the atomic owner of the channel set plus the
edge/debounce/dedupe memory. It **subscribes to the existing ``ActivityService``
delta bus** and adds *no* new status computation (the KISS rule): the
activity service already blends every session's :class:`AgentActivityState` and
already bridges every ``WorkspaceEvent`` onto the same bus, so the broker only
has to notice the edges in what is already flowing past it.

Three triggers, one per thing a human actually wants pushed:

- **agent-state edge** — a session crossed into a notifiable state (WAITING =
  turn finished, BLOCKED, ERROR). Debounced per workspace.
- **question edge** — a new *unanswered* question appeared. Deduped by question
  id, never by time: a second question is a second thing the human owes an
  answer to, and dropping it would strand the agent.
- **lifecycle edge** — the workspace itself broke, went offline, or was
  orphaned (the ``workspace_changed`` deltas the broker used to discard).

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
from datetime import UTC, datetime, timedelta
from threading import Lock
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

    def __init__(
        self,
        *,
        channels: Sequence[NotificationChannel],
        notify_states: frozenset[AgentActivityState] = frozenset(Notification.REASONS),
        notify_questions: bool = True,
        notify_lifecycle: frozenset[str] = frozenset(Notification.LIFECYCLE),
        debounce: timedelta = timedelta(seconds=30),
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
        shape and tests drive it with a bare stub.
        """
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-notify")
        self._unsub = subscribe(self._on_delta)

    def close(self) -> None:
        """Unsubscribe, drain the worker, and release every channel."""
        if self._unsub is not None:
            with contextlib.suppress(Exception):
                self._unsub()
            self._unsub = None
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

    def _question_edge(
        self, row: WorkspaceActivity, session: SessionActivity, now: datetime
    ) -> Notification | None:
        """Fire on a question id we have never seen open before.

        **Deduped by id, not by time** — the debounce window is deliberately not
        consulted. Two questions 5 seconds apart are two answers the agent is
        waiting on; suppressing the second would leave it stranded with the human
        believing they were done. Re-asking the *same* id can never re-fire, so
        the storm guard is exact rather than temporal.

        First observation of a session seeds silently, like the state detector: a
        daemon restart must not re-push every question already on screen.
        """
        session_id = session.session.session_id
        open_now = Notification.unanswered(session)
        open_ids = frozenset(question.id for question in open_now)
        seen = self._open_questions.get(session_id)
        self._open_questions[session_id] = open_ids
        if not self._notify_questions or seen is None:
            return None  # disabled, or first sighting (seed only — no restart storm)
        fresh = tuple(question for question in open_now if question.id not in seen)
        if not fresh:
            return None
        self._last_fired[row.state.id] = now  # the state edge behind it stays quiet
        return Notification.for_questions(
            row, session, questions=fresh, deep_link_base=self._deep_link_base, now=now
        )

    def _state_edge(
        self, row: WorkspaceActivity, session: SessionActivity, now: datetime
    ) -> Notification | None:
        """Fire on the **rising edge** into a notifiable state, once per episode.

        Two guards stop notification storms:
        - **First observation seeds silently.** A session first seen already in
          WAITING (a daemon restart mid-turn) records its state but does not
          fire — otherwise every already-finished workspace buzzes on boot.
        - **Per-workspace debounce.** After a fire, that workspace is quiet for
          ``debounce`` regardless of session flapping (WAITING→WORKING→WAITING on
          a tool round-trip), so the phone buzzes once per attention episode.
        """
        current = session.activity.state
        previous = self._last_state.get(session.session.session_id)
        self._last_state[session.session.session_id] = current
        if not self._is_rising_edge(current, previous):
            return None
        if self._debounced(row.state.id, now):
            logger.debug("notify debounced workspace={} state={}", row.state.id, current)
            return None
        self._last_fired[row.state.id] = now
        return Notification.from_activity(
            row, session, state=current, deep_link_base=self._deep_link_base, now=now
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
