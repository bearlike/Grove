"""The notification broker — a debounced edge-trigger over the activity bus.

``NotificationBroker`` is the atomic owner of the channel set plus the
edge/debounce state. It **subscribes to the existing ``ActivityService`` delta
bus** and adds *no* new status computation (issue #70's KISS rule): the activity
service already blends every session's :class:`AgentActivityState`, so a
notification is simply the *rising edge* of a session crossing into a notifiable
state (``WAITING`` = turn finished, ``BLOCKED`` = awaiting input, ``ERROR``).

Decision and I/O are deliberately split (CLAUDE.md, "side effects at the edges,
pure logic in the middle"):

- :meth:`evaluate` — **pure** given the injected clock. Folds one
  ``DashboardDelta`` against the per-session last-state + per-workspace debounce
  memory and returns the ``Notification``s to send. No I/O, fully unit-testable.
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

from grove.core.activity import DashboardDelta
from grove.core.agents import AgentActivityState
from grove.core.config import NotificationsConfig
from grove.core.notifications.channel import Notification, NotificationChannel
from grove.core.notifications.gotify import GotifyNotificationChannel
from grove.core.notifications.webhook import WebhookNotificationChannel


class NotificationBroker:
    """Owns channels + edge/debounce state; turns activity deltas into pushes.

    Long-lived alongside the daemon. ``bind`` wires it to an ``ActivityService``
    bus and starts the dispatch worker; ``close`` unwinds both and releases the
    channels. Built via :meth:`from_config` — ``None`` when notifications are off
    or no channel is configured, so the daemon stays oblivious to the policy.
    """

    # Each notifiable channel: its config sub-section attribute + the factory that
    # consumes that sub-section. Adding email/Slack/Teams is one entry here plus
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
        debounce: timedelta = timedelta(seconds=30),
        deep_link_base_url: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._channels = tuple(channels)
        # Only states with a reason phrase can fire; intersect so a misconfigured
        # WORKING/STARTING can never push.
        self._notify_states = notify_states & frozenset(Notification.REASONS)
        self._debounce = debounce
        self._deep_link_base = deep_link_base_url.rstrip("/")
        self._clock = clock if clock is not None else self._utcnow
        # Per-session last observed state — the edge detector. Per-workspace last
        # fire time — the debounce. Unbounded like the registry: loopback, small N.
        self._last_state: dict[str, AgentActivityState] = {}
        self._last_fired: dict[str, datetime] = {}
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
        the state enum.
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
        return cls(
            channels=channels,
            notify_states=frozenset(AgentActivityState(name) for name in cfg.on),
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

        Only ``session_activity`` deltas carry the recomputed ``WorkspaceActivity``
        (lifecycle ``workspace_changed`` deltas have no session payload — nothing
        to edge-detect). A session fires on the **rising edge**: the state is a
        notify target now and was something else the last time we saw it.

        Two guards stop notification storms:
        - **First observation seeds silently.** A session first seen already in
          WAITING (a daemon restart mid-turn) records its state but does not
          fire — otherwise every already-finished workspace buzzes on boot.
        - **Per-workspace debounce.** After a fire, that workspace is quiet for
          ``debounce`` regardless of session flapping (WAITING→WORKING→WAITING on
          a tool round-trip), so the phone buzzes once per attention episode.
        """
        row = delta.workspace
        if delta.kind != "session_activity" or row is None:
            return []
        now = self._clock()
        out: list[Notification] = []
        with self._lock:
            for session in row.sessions:
                current = session.activity.state
                previous = self._last_state.get(session.session.session_id)
                self._last_state[session.session.session_id] = current
                if not self._is_rising_edge(current, previous):
                    continue
                if self._debounced(row.state.id, now):
                    logger.debug("notify debounced workspace={} state={}", row.state.id, current)
                    continue
                self._last_fired[row.state.id] = now
                out.append(
                    Notification.from_activity(
                        row, session, state=current, deep_link_base=self._deep_link_base, now=now
                    )
                )
        return out

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
                    "notify sent channel={} workspace={} state={}",
                    channel.name,
                    notification.workspace_id,
                    notification.state.value,
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
