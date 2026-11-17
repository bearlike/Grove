"""The reaper as a standing watch on the existing scheduler: no loop of its own."""

from __future__ import annotations

from datetime import datetime, timedelta

from grove.core.contracts.watches import HOST_ADDRESS, ReaperPredicate, WatchRegistration
from grove.core.reaper.reaper import Reaper
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.watcher import StillWatching, Watcher


class ReaperWatcher(Watcher[ReaperPredicate]):
    """Runs one reconciliation pass per check and never settles.

    Answers ``StillWatching`` with no outcome, so nothing is ever mailed to
    :data:`HOST_ADDRESS`; what a pass did is in the reaper's own store.
    """

    kind = "reaper"

    def __init__(self, reaper: Reaper, *, dry_run: bool) -> None:
        """Bind the pass this watch runs.

        Args:
            reaper: The reconciler.
            dry_run: Report candidates past their grace instead of removing them.
        """
        self._reaper = reaper
        self._dry_run = dry_run

    def observe(self, predicate: ReaperPredicate, now: datetime) -> StillWatching:
        del now
        self._reaper.run(apply=not self._dry_run)
        return StillWatching(predicate)

    @staticmethod
    def ensure(scheduler: WatchScheduler, *, every: timedelta | None) -> None:
        """Hold exactly one pending reaper watch at *every*, or none when it is ``None``.

        Idempotent across restarts, and the only place a reaper watch is
        cancelled: the row is durable, so switching the reaper off must remove
        the one an earlier run registered.
        """
        held = [
            row
            for row in scheduler.list(workspace_id=HOST_ADDRESS.workspace_id).watches
            if row.state == "pending" and row.predicate.kind == "reaper"
        ]
        if every is not None and any(row.every == every for row in held):
            return
        for row in held:
            scheduler.cancel(row.id)
        if every is None:
            return
        scheduler.register(
            WatchRegistration(
                recipient=HOST_ADDRESS,
                predicate=ReaperPredicate(),
                every=every,
                note="Grove reconciles leaked tmux servers and processes on this host.",
            )
        )


__all__ = ["ReaperWatcher"]
