"""Idle suspension: freeze a workspace whose agent has been at rest past a cooldown (#970).

One host-wide standing watch on the existing scheduler, never a loop of its own.
Each check reads the maintained activity projection for candidates, confirms
each one against a freshly computed row (the projection can hold a stale state
while no dashboard is open), and asks the workspace's manager to ``freeze``.
The next message thaws it (``WorkspaceManager.send_message``).

Idle is the agent axis only, and conservative by construction: the primary
session is ``IDLE`` or ``WAITING`` with no question pending, no sub-agent and no
queued steer, and its transcript has not moved for ``idle.cooldown_seconds``. A
pending human question is ``BLOCKED`` and therefore never idle.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from threading import Lock

from loguru import logger

from grove.core.activity import WorkspaceActivity
from grove.core.agents.model import AgentActivityState
from grove.core.contracts.watches import HOST_ADDRESS, IdlePredicate, WatchRegistration
from grove.core.errors import GroveError
from grove.core.telemetry.semconv import GroveIdentityAttr
from grove.core.trace import SpanRecord, SpanSink
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.watcher import StillWatching, Watcher
from grove.core.workspace import WorkspaceStatus

#: The agent states that may be at rest. Everything else is doing something or
#: waiting on a person, and neither may be suspended under it.
_RESTING = frozenset({AgentActivityState.IDLE, AgentActivityState.WAITING})


@dataclass(frozen=True, slots=True)
class IdleRule:
    """Decides, from one activity row, whether a workspace has been idle past its cooldown."""

    cooldown: timedelta
    #: How long a frozen workspace waits before it is hibernated; ``None`` never.
    hibernate_after: timedelta | None = None

    def hibernate(self, row: WorkspaceActivity, now: datetime) -> bool:
        """Whether *row* has been frozen long enough to hibernate at *now*.

        Only the frozen stamp is read: a frozen workspace is at rest by
        construction, and a message thaws it, which clears the stamp and so
        restarts this clock. Whether its runtime can hibernate is the verb's
        question, not this rule's.
        """
        frozen = row.state.frozen_at
        return (
            self.hibernate_after is not None
            and frozen is not None
            and row.state.hibernated_at is None
            and now - frozen >= self.hibernate_after
        )

    def idle(self, row: WorkspaceActivity, now: datetime) -> bool:
        """Whether *row* may be frozen at *now*.

        Args:
            row: the workspace's activity row.
            now: the instant of this check.

        Returns:
            ``True`` only for a running, unfrozen workspace whose primary
            session is at rest with nothing pending, whose transcript last
            moved more than ``cooldown`` ago, and which was not thawed within
            ``cooldown``; a row with no readable session or no event time is
            never idle.
        """
        state = row.state
        if state.frozen_at is not None or state.status is WorkspaceStatus.PAUSED:
            return False
        if not row.sessions:
            return False
        primary = row.sessions[0].activity
        if primary.state not in _RESTING or primary.questions or primary.active_subagents:
            return False
        if row.queue is not None or (row.fleet is not None and row.fleet.active):
            return False
        last = primary.last_event_at
        # A thaw is activity too: a woken workspace still carries its last
        # pre-freeze event, already past the cooldown, so without this it was
        # frozen again seconds after a wake, before its worker dialled in (#1083).
        thawed = state.thawed_at
        if thawed is not None and now - thawed < self.cooldown:
            return False
        return last is not None and now - last >= self.cooldown


#: Every workspace row the projection currently holds.
Candidates = Callable[[], Iterable[WorkspaceActivity]]
#: A freshly computed row for one workspace, or ``None`` when it cannot be read.
FreshRow = Callable[[WorkspaceActivity], WorkspaceActivity | None]
#: Freeze one workspace through its own manager.
Freeze = Callable[[WorkspaceActivity], None]
#: Hibernate one frozen workspace through its own manager.
Hibernate = Callable[[WorkspaceActivity], None]
#: Submit one lifecycle action without occupying the watch worker.
IdleSubmit = Callable[[str, Callable[[], None]], Future[None]]


class IdleWatcher(Watcher[IdlePredicate]):
    """Freezes every workspace idle past its cooldown, and hibernates every one frozen
    past its second limit, once per check; never settles."""

    kind = "idle"

    def __init__(
        self,
        *,
        candidates: Candidates,
        fresh: FreshRow,
        freeze: Freeze,
        rule_for: Callable[[WorkspaceActivity], IdleRule],
        hibernate: Hibernate | None = None,
        submit: IdleSubmit | None = None,
    ) -> None:
        """Bind the reads and the verb this watch acts through.

        Args:
            candidates: the maintained rows, a cheap first filter.
            fresh: recomputes one row so a stale projection cannot freeze a working agent.
            freeze: suspends one workspace.
            rule_for: the cooldown from that workspace's own repo cascade.
            hibernate: releases one frozen workspace to a snapshot, or
                ``None`` where nothing can (every workspace stays frozen).
            submit: hands a lifecycle action to a bounded background runner, or
                ``None`` for an in-process caller that intentionally runs it here.
        """
        self._candidates = candidates
        self._fresh = fresh
        self._freeze = freeze
        self._rule_for = rule_for
        self._hibernate = hibernate
        self._submit = submit
        self._in_flight: set[str] = set()
        self._in_flight_lock = Lock()

    def observe(self, predicate: IdlePredicate, now: datetime) -> StillWatching:
        for row in self._candidates():
            rule = self._rule_for(row)
            hibernate = self._hibernate
            if hibernate is not None and rule.hibernate(row, now):
                self._act(row, "hibernate", partial(hibernate, row))
                continue
            if not rule.idle(row, now):
                continue
            current = self._fresh(row)
            if current is None or not rule.idle(current, now):
                continue

            self._act(current, "freeze", partial(self._freeze, current))
        return StillWatching(predicate)

    def _act(self, row: WorkspaceActivity, verb: str, action: Callable[[], None]) -> None:
        """Run one idle lifecycle action without letting it stall later observations."""
        if self._submit is None:
            try:
                action()
            except GroveError as exc:
                logger.warning("idle: could not {} {}: {}", verb, row.state.id, exc)
            return
        with self._in_flight_lock:
            if row.state.id in self._in_flight:
                return
            self._in_flight.add(row.state.id)
        try:
            future = self._submit(row.state.id, action)
        except Exception as exc:
            with self._in_flight_lock:
                self._in_flight.discard(row.state.id)
            logger.warning("idle: could not submit {} {}: {}", verb, row.state.id, exc)
            return

        def finished(done: Future[None]) -> None:
            try:
                done.result()
            except Exception as exc:
                # The unchanged row is eligible on the next observation, so a
                # transient lifecycle failure retries without taking down the scheduler.
                logger.warning("idle: could not {} {}: {}", verb, row.state.id, exc)
            finally:
                with self._in_flight_lock:
                    self._in_flight.discard(row.state.id)

        future.add_done_callback(finished)

    @staticmethod
    def ensure(scheduler: WatchScheduler, *, every: timedelta) -> None:
        """Hold exactly one pending idle watch checking at *every*. Idempotent across restarts."""
        held = [
            row
            for row in scheduler.list(workspace_id=HOST_ADDRESS.workspace_id).watches
            if row.state == "pending" and row.predicate.kind == "idle"
        ]
        if any(row.every == every for row in held):
            return
        for row in held:
            scheduler.cancel(row.id)
        scheduler.register(
            WatchRegistration(
                recipient=HOST_ADDRESS,
                predicate=IdlePredicate(),
                every=every,
                note="Grove suspends workspaces whose agent has been idle past the cooldown.",
            )
        )


class FreezeSpans:
    """One span per freeze or thaw, fed by the manager's lifecycle events."""

    def __init__(self, sink: SpanSink | None) -> None:
        """Args:
        sink: where spans go, or ``None`` when telemetry is off.
        """
        self._sink = sink

    def record(self, kind: str, workspace_id: str, at: datetime) -> None:
        """Emit the span for one ``frozen``/``thawed`` event; any other kind is ignored.

        Best-effort: telemetry can never fail the transition it describes.
        """
        if self._sink is None or kind not in {"frozen", "thawed"}:
            return
        try:
            self._sink(
                SpanRecord(
                    trace_id=int.from_bytes(secrets.token_bytes(16)),
                    span_id=int.from_bytes(secrets.token_bytes(8)),
                    parent_span_id=None,
                    name=f"workspace {kind}",
                    kind="tool",
                    start_time=at,
                    end_time=at,
                    attributes={GroveIdentityAttr.WORKSPACE_ID: workspace_id},
                )
            )
        except Exception as exc:
            logger.debug("idle: span not emitted: {}", exc)


__all__ = ["FreezeSpans", "IdleRule", "IdleWatcher"]
