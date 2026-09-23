"""The durable registry of every watch Grove holds.

Same discipline as :class:`~grove.core.issueops.handover.HandoverLog` and for the
same reason — several processes over one state directory — but the failure modes
are chosen differently, and the difference is worth stating because the two files
look alike.

**A corrupt handover log raises; a corrupt watch log raises too, but what the
caller does about it is the opposite.** Reading an unreadable handover log as
empty spawns workspaces without bound, so the poller skips its whole tick. Here
an unreadable file means every agent waiting on a callback waits forever with no
signal, which is silent rather than explosive — so the scheduler surfaces it and
keeps serving the watches it already holds in memory rather than exiting.

**A settled row is RETAINED, not deleted.** It is the record that this callback
was already delivered, which is what stops a restart from re-sending one; it is
also the only place a human can see that a watch expired or could not be
delivered. Rows are pruned by AGE, well after anything could still act on them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grove.core import paths
from grove.core.contracts.watches import WatchPredicate, WatchView
from grove.core.errors import GroveError

_VERSION = 1

#: How long a settled row survives. Long enough that a human debugging "why did
#: my agent never wake up" the next morning still finds it, short enough that the
#: file does not grow forever on a busy host.
RETENTION = timedelta(days=7)

#: States that are over. Nothing in these is ever re-scheduled or re-delivered.
TERMINAL_STATES = frozenset({"fired", "expired", "cancelled", "undeliverable"})


class WatchLog:
    """Every watch, live and recently settled, as one locked JSON file.

    Reads are unlocked — :func:`paths.write_atomic` publishes by rename, so a
    reader sees one whole file or the previous whole file. Every mutation holds
    :func:`paths.exclusive_lock` across the read-modify-write, because the
    daemon's scheduler and a `grove watch` CLI verb are two processes racing for
    the same rows.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.user_watches_path()

    @property
    def path(self) -> Path:
        return self._path

    # ─── reads ──────────────────────────────────────────────────────────────

    def all(self) -> list[WatchView]:
        """Every row, newest registration first."""
        rows = list(self._load().values())
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows

    def active(self) -> list[WatchView]:
        """Only the rows that still need scheduling — what a reload re-arms."""
        return [row for row in self.all() if row.state not in TERMINAL_STATES]

    def get(self, watch_id: str) -> WatchView | None:
        return self._load().get(watch_id)

    # ─── mutations ──────────────────────────────────────────────────────────

    def put(self, watch: WatchView) -> None:
        """Write one row, replacing any earlier version of it."""
        with paths.exclusive_lock(self._path):
            records = self._load()
            records[watch.id] = watch
            self._write(records)

    def settle(
        self,
        watch_id: str,
        *,
        state: str,
        now: datetime,
        outcome: Any = None,
        receipt: str | None = None,
        receipt_detail: str | None = None,
    ) -> WatchView | None:
        """Move a row to a terminal state, returning what was written.

        **Called BEFORE the callback is delivered**, with ``receipt=None``, and
        the ordering is the point: persisting the outcome first means a crash
        between the two leaves a row that says *this settled and we do not know
        whether the message landed*, which is recoverable by a human. Delivering
        first and crashing loses the outcome entirely — the watch is gone from
        the heap and no record of what it concluded exists anywhere.
        """
        with paths.exclusive_lock(self._path):
            records = self._load()
            row = records.get(watch_id)
            if row is None:
                return None
            updated = row.model_copy(
                update={
                    "state": state,
                    "settled_at": now,
                    "next_due": None,
                    "outcome": outcome if outcome is not None else row.outcome,
                    "receipt": receipt,
                    "receipt_detail": receipt_detail,
                }
            )
            records[watch_id] = updated
            self._write(records)
        return updated

    def record_receipt(
        self, watch_id: str, *, receipt: str, detail: str | None = None
    ) -> WatchView | None:
        """Attach what the transport said to an already-settled row."""
        with paths.exclusive_lock(self._path):
            records = self._load()
            row = records.get(watch_id)
            if row is None:
                return None
            updated = row.model_copy(update={"receipt": receipt, "receipt_detail": detail})
            records[watch_id] = updated
            self._write(records)
        return updated

    def reschedule(self, watch_id: str, next_due: datetime) -> None:
        """Record when a still-pending watch will next be evaluated.

        This is the one write on the polling path, so it is also the one place a
        careless implementation would turn "watch something for six hours" into
        720 file rewrites. The scheduler calls it only when the due time actually
        MOVES, never once per tick per row.
        """
        with paths.exclusive_lock(self._path):
            records = self._load()
            row = records.get(watch_id)
            if row is None or row.next_due == next_due:
                return
            records[watch_id] = row.model_copy(update={"next_due": next_due})
            self._write(records)

    def carry_on(self, watch_id: str, predicate: WatchPredicate, next_due: datetime) -> None:
        """Keep a standing watch pending with what it has now seen, and when to look again.

        One write, and only when the watcher reported a move, so an unchanged
        ticket costs no disk I/O. It is persisted BEFORE anything is mailed,
        for the same reason ``settle`` is: a crash between the two then loses
        one notification rather than repeating it on every restart.
        """
        with paths.exclusive_lock(self._path):
            records = self._load()
            row = records.get(watch_id)
            if row is None or row.state != "pending":
                return
            records[watch_id] = row.model_copy(
                update={"predicate": predicate, "next_due": next_due}
            )
            self._write(records)

    def prune(self, now: datetime) -> int:
        """Drop settled rows past :data:`RETENTION`. Returns how many went."""
        cutoff = now - RETENTION
        with paths.exclusive_lock(self._path):
            records = self._load()
            keep = {
                key: row
                for key, row in records.items()
                if row.state not in TERMINAL_STATES
                or (row.settled_at is not None and row.settled_at > cutoff)
            }
            dropped = len(records) - len(keep)
            if dropped:
                self._write(keep)
        return dropped

    # ─── serialization ──────────────────────────────────────────────────────

    def _load(self) -> dict[str, WatchView]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise GroveError(f"corrupt watch log at {self._path}: {exc}") from exc
        except OSError as exc:
            raise GroveError(f"cannot read watch log at {self._path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            raise GroveError(
                f"watch log at {self._path} has version {data.get('version')!r} "
                f"(expected {_VERSION}); refusing to treat it as empty"
            )
        raw = data.get("watches")
        if not isinstance(raw, dict):
            raise GroveError(f"`watches` must be an object in {self._path}")
        return {key: WatchView.model_validate(value) for key, value in raw.items()}

    def _write(self, records: dict[str, WatchView]) -> None:
        payload = {
            "version": _VERSION,
            "watches": {key: row.model_dump(mode="json") for key, row in records.items()},
        }
        paths.write_atomic(self._path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def utcnow() -> datetime:
    """The clock the scheduler injects, isolated so tests can replace it."""
    return datetime.now(UTC)


__all__ = ["RETENTION", "TERMINAL_STATES", "WatchLog", "utcnow"]
