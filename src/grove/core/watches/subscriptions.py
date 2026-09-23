"""Keep one standing ticket watch per ticket attached to each running workspace.

Nobody registers these by hand: an agent cannot forget to, and a watch cannot
outlive the work it serves. The workspace record is the source of truth, and
this class makes the watch registry agree with it:

* a running workspace gets one :class:`TicketPredicate` watch for each ref in
  ``ticket_refs``;
* a detached ref removes only its standing ticket watch; a workspace that is
  paused, killed or gone has every pending recipient-owned watch cancelled.

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
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.watches import TicketPredicate, WatchRegistration
from grove.core.errors import GroveError
from grove.core.tickets.reads import TicketReads
from grove.core.watches.scheduler import WatchScheduler
from grove.core.workspace import WorkspaceState, WorkspaceStatus

if TYPE_CHECKING:
    from grove.core.activity import DashboardDelta

#: Reads one workspace record, or ``None`` once it has been killed.
WorkspaceLookup = Callable[[str], WorkspaceState | None]


class TicketSubscriptions:
    """Reconcile the standing ticket watches of one workspace at a time against its record."""

    def __init__(self, *, scheduler: WatchScheduler, workspaces: WorkspaceLookup) -> None:
        self._scheduler = scheduler
        self._workspaces = workspaces
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

        Also cancels ticket watches whose workspace is in none of
        ``workspace_ids``, so a record deleted while the daemon was down cannot
        leave a watch pending indefinitely.
        """
        known = set(workspace_ids)
        for workspace_id in known:
            self.reconcile(workspace_id)
        for row in self._scheduler.list().watches:
            if row.state == "pending" and row.recipient.workspace_id not in known:
                self._scheduler.cancel(row.id)

    def reconcile(self, workspace_id: str) -> None:
        """Make this workspace's ticket watches match its current record. Idempotent.

        Best-effort: an unreadable record or registry is logged and left for the
        next event, because the failure must never propagate into the lifecycle
        verb whose event triggered it.
        """
        try:
            state = self._workspaces(workspace_id)
            wanted = self._wanted(workspace_id, state)
            inactive = state is None or state.status is not WorkspaceStatus.RUNNING
            held: dict[tuple[str, str], str] = {}
            for row in self._scheduler.list(workspace_id=workspace_id).watches:
                if row.state != "pending":
                    continue
                if inactive:
                    self._scheduler.cancel(row.id)
                elif isinstance(row.predicate, TicketPredicate):
                    held[(row.predicate.provider, row.predicate.ticket_id)] = row.id
            for key, watch_id in held.items():
                if key not in wanted:
                    self._scheduler.cancel(watch_id)
            for key, predicate in wanted.items():
                if key not in held:
                    self._scheduler.register(
                        WatchRegistration(
                            recipient=MailboxAddress(workspace_id=workspace_id),
                            predicate=predicate,
                            # Check exactly as often as a read can be fresh:
                            # a faster cadence would only re-read the cache.
                            every=TicketReads.TTL,
                            note="Grove watches the tickets attached to this workspace.",
                        )
                    )
        except GroveError as exc:
            logger.warning("ticket watches for {} not reconciled: {}", workspace_id, exc)

    def _wanted(
        self, workspace_id: str, state: WorkspaceState | None
    ) -> dict[tuple[str, str], TicketPredicate]:
        """One predicate per attached ref, or none when the workspace is not running.

        Reads the PERSISTED intent: ``RUNNING`` is what a live workspace
        records, while ``PAUSED`` and ``ERROR`` are not watched.
        """
        if state is None or state.status is not WorkspaceStatus.RUNNING:
            return {}
        return {
            (ref.provider, ref.id): TicketPredicate(
                workspace_id=workspace_id,
                provider=ref.provider,
                ticket_id=ref.id,
                ticket_kind=ref.kind,
            )
            for ref in state.ticket_refs
        }


__all__ = ["TicketSubscriptions", "WorkspaceLookup"]
