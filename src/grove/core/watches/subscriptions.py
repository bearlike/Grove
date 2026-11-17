"""Keep each running workspace's standing watches in line with its record.

Nobody registers these by hand: an agent cannot forget to, and a watch cannot
outlive the work it serves. The workspace record is the source of truth, and
this class makes the watch registry agree with it:

* a running workspace gets one :class:`TicketPredicate` watch for each ref in
  ``ticket_refs``, plus one :class:`TriggerPredicate` watch while any trigger
  rule is configured;
* a detached ref removes only its standing ticket watch; a workspace that is
  paused or otherwise not running loses its standing watches only, while the
  agent's own waits (timer, ci, command) outlive the pause and settle on their
  own terms; a workspace that is killed or gone has every pending watch
  addressed to it cancelled, because nothing could ever receive the mail.

It is driven by the activity bus (a workspace whose status or attached tickets
moved, or that ended) and by one pass over every workspace at start-up. Reconciling
against the record, rather than acting on each event's details, is what makes
every path equivalent. A lost event or a daemon restart is repaired by the next
reconcile, and replaying one twice changes nothing.

"Online and active" is enforced twice. There is no watch for a workspace that
is not running, and mail to one that stopped in between is refused by the
mailbox's own ``can_receive``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.watches import (
    HOST_KINDS,
    TicketPredicate,
    TriggerPredicate,
    WatchPredicate,
    WatchRegistration,
)
from grove.core.errors import GroveError
from grove.core.tickets.reads import TicketReads
from grove.core.watches.scheduler import WatchScheduler
from grove.core.workspace import WorkspaceState, WorkspaceStatus

if TYPE_CHECKING:
    from grove.core.activity import DashboardDelta

#: Reads one workspace record, or ``None`` once it has been killed.
WorkspaceLookup = Callable[[str], WorkspaceState | None]


#: One standing watch a workspace should hold, keyed so a held row can be matched to it.
_Key = tuple[str, ...]


class StandingWatches:
    """Reconcile the standing watches of one workspace at a time against its record."""

    def __init__(
        self,
        *,
        scheduler: WatchScheduler,
        workspaces: WorkspaceLookup,
        trigger_every: timedelta | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._workspaces = workspaces
        # `None` turns triggers off; reconcile then cancels any held trigger watch.
        self._trigger_every = trigger_every
        # What each workspace last looked like to this class, so a delta that
        # changes nothing relevant (the ~1 Hz activity churn) costs no I/O.
        self._seen: dict[str, tuple[str, tuple[str, ...]]] = {}
        self._unsubscribe: Callable[[], None] | None = None

    def bind(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Follow the activity bus, which carries every workspace change from any process."""
        self._unsubscribe = subscribe(self.observe)

    def close(self) -> None:
        """Stop following the bus. Pending watches stay in the durable log."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def observe(self, delta: DashboardDelta) -> None:
        """Reconcile one workspace when its status or attached tickets moved, or it ended.

        The bus rather than the manager's own events, because a ticket is often
        attached by ``grove tickets attach`` in ANOTHER process: that edge
        reaches the daemon only through the store-file watcher, which publishes
        here and never on this process's manager.
        """
        row = delta.workspace
        if row is None:
            if delta.kind == "workspace_changed":
                self._seen.pop(delta.workspace_id, None)
                self.reconcile(delta.workspace_id)
            return
        key = (row.state.status.value, tuple(ref.key for ref in row.state.ticket_refs))
        if self._seen.get(delta.workspace_id) == key:
            return
        self._seen[delta.workspace_id] = key
        self.reconcile(delta.workspace_id)

    def reconcile_all(self, workspace_ids: Iterable[str]) -> None:
        """Start-up pass: bring every known workspace's watches in line, and drop orphans.

        Also cancels watches whose workspace is in none of ``workspace_ids``, so
        a record deleted while the daemon was down cannot leave a watch pending
        indefinitely.
        """
        known = set(workspace_ids)
        for workspace_id in known:
            self.reconcile(workspace_id)
        # Best-effort like `reconcile`: this runs in the daemon's lifespan, so an
        # unreadable log raising here was a crash loop for the whole daemon over a
        # cleanup pass (2026-10-15). The next start retries the sweep.
        try:
            for row in self._scheduler.list().watches:
                if (
                    row.state == "pending"
                    and row.predicate.kind not in HOST_KINDS
                    and row.recipient.workspace_id not in known
                ):
                    self._scheduler.cancel(row.id)
        except GroveError as exc:
            logger.error("orphaned watches not swept at startup: {}", exc)

    def reconcile(self, workspace_id: str) -> None:
        """Make this workspace's standing watches match its current record. Idempotent.

        Best-effort: an unreadable record or registry is logged and left for the
        next event, because the failure must never propagate into the lifecycle
        verb whose event triggered it.
        """
        try:
            state = self._workspaces(workspace_id)
            gone = state is None
            running = state is not None and state.status is WorkspaceStatus.RUNNING
            wanted = self._wanted(workspace_id, state) if running else {}
            held: dict[_Key, str] = {}
            for row in self._scheduler.list(workspace_id=workspace_id).watches:
                if row.state != "pending":
                    continue
                key = self._key(row.predicate)
                # An agent's own wait outlives a pause: it halted on that watch,
                # and cancelling it would withhold the very mail (fired or
                # expired) that made halting safe. Only a deleted record ends it.
                if key is not None:
                    held[key] = row.id
                elif gone:
                    self._scheduler.cancel(row.id)
            for key, watch_id in held.items():
                if key not in wanted:
                    self._scheduler.cancel(watch_id)
            for key, registration in wanted.items():
                if key not in held:
                    self._scheduler.register(registration)
        except GroveError as exc:
            logger.warning("standing watches for {} not reconciled: {}", workspace_id, exc)

    @staticmethod
    def _key(predicate: WatchPredicate) -> _Key | None:
        """The identity of a standing watch, or ``None`` for a wait somebody registered."""
        if isinstance(predicate, TicketPredicate):
            return ("ticket", predicate.provider, predicate.ticket_id)
        if isinstance(predicate, TriggerPredicate):
            return ("trigger",)
        return None

    def _wanted(
        self, workspace_id: str, state: WorkspaceState | None
    ) -> dict[_Key, WatchRegistration]:
        """Every standing watch a RUNNING workspace should hold right now.

        Reads the PERSISTED intent: ``RUNNING`` is what a live workspace
        records, while ``PAUSED`` and ``ERROR`` are not watched. Whether the
        agent is actually working is the trigger watcher's own per-check question,
        not this one's: a running workspace holds the watch, and the watch
        decides each time whether there is anything to say.
        """
        recipient = MailboxAddress(workspace_id=workspace_id)
        wanted: dict[_Key, WatchRegistration] = {}
        for ref in state.ticket_refs if state else ():
            predicate = TicketPredicate(
                workspace_id=workspace_id,
                provider=ref.provider,
                ticket_id=ref.id,
                ticket_kind=ref.kind,
            )
            wanted[("ticket", ref.provider, ref.id)] = WatchRegistration(
                recipient=recipient,
                predicate=predicate,
                # Check exactly as often as a read can be fresh: a faster
                # cadence would only re-read the cache.
                every=TicketReads.TTL,
                note="Grove watches the tickets attached to this workspace.",
            )
        if self._trigger_every is not None:
            wanted[("trigger",)] = WatchRegistration(
                recipient=recipient,
                predicate=TriggerPredicate(workspace_id=workspace_id),
                every=self._trigger_every,
                note="Grove's configured instructions for this workspace's agent.",
            )
        return wanted


__all__ = ["StandingWatches", "WorkspaceLookup"]
