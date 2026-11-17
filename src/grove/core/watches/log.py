"""The durable registry of every watch Grove holds.

Several processes write one state directory, as with
:class:`~grove.core.issueops.handover.HandoverLog`, but the two take OPPOSITE
answers to a damaged file, and the difference is worth stating because the two
look alike.

**A damaged handover log refuses; a damaged watch log recovers.** Reading an
unreadable handover log as empty spawns workspaces without bound, so its poller
skips the whole tick. A watch log's contents are either rebuilt (standing watches,
by reconcile) or bounded by a deadline (every wait), so the worst case of
starting empty is a callback that never arrives. Refusing instead made one
flipped bit a crash loop for the whole daemon (2026-10-15).

**A settled row is RETAINED, not deleted.** It is the record that this callback
was already delivered, which is what stops a restart from re-sending one; it is
also the only place a human can see that a watch expired or could not be
delivered. Rows are pruned by AGE, well after anything could still act on them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core import paths
from grove.core.contracts.watches import RETIRED_KINDS, WatchPredicate, WatchView
from grove.core.errors import GroveError
from grove.core.record_file import RecordCodec, RecordFile

_VERSION = 2

#: How long a settled row survives. Long enough that a human debugging "why did
#: my agent never wake up" the next morning still finds it, short enough that the
#: file does not grow forever on a busy host.
RETENTION = timedelta(days=7)

#: States that are over. Nothing in these is ever re-scheduled or re-delivered.
TERMINAL_STATES = frozenset({"fired", "expired", "cancelled", "undeliverable"})


class WatchLog:
    """Every watch, live and recently settled, as one checksummed records file.

    Reads are a frozen snapshot (:class:`~grove.core.record_file.RecordFile`),
    so the scheduler's many ``get``/``list`` calls cost one ``stat`` each while
    the file is unchanged, where they used to re-parse and re-validate the
    whole log (measured 21.4 ms per call on a 766-row log). Every mutation
    holds the cross-process file lock across its read-modify-write, because the
    daemon's scheduler and a `grove watch` CLI verb are two processes racing
    for the same rows.

    **One damaged row costs that row, never the log.** A line whose checksum
    fails is quarantined and the rest load; a file with no trustworthy header
    is moved aside and the log starts empty, logged at ERROR. That is safe here
    and only here: standing watches are re-registered by reconcile, and every
    other row is a wait with a deadline, so the worst case is a callback that
    never arrives instead of a daemon that never starts (2026-10-15).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.user_watches_path()
        self._file: RecordFile[WatchView] = RecordFile(
            self._path,
            store="watches",
            version=_VERSION,
            codec=RecordCodec(key=_key, encode=_encode, decode=_decode),
            legacy=_legacy_rows,
            on_unreadable="quarantine",
        )

    @property
    def path(self) -> Path:
        return self._path

    # ─── reads ──────────────────────────────────────────────────────────────

    def all(self) -> list[WatchView]:
        """Every row, newest registration first."""
        rows = list(self._file.records().values())
        rows.sort(key=lambda row: row.created_at, reverse=True)
        return rows

    def active(self) -> list[WatchView]:
        """Only the rows that still need scheduling — what a reload re-arms."""
        return [row for row in self.all() if row.state not in TERMINAL_STATES]

    def get(self, watch_id: str) -> WatchView | None:
        return self._file.get(watch_id)

    # ─── mutations ──────────────────────────────────────────────────────────

    def put(self, watch: WatchView) -> None:
        """Write one row, replacing any earlier version of it."""
        self._file.mutate(lambda records: {**records, watch.id: watch})

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
        return self._update(
            watch_id,
            lambda row: {
                "state": state,
                "settled_at": now,
                "next_due": None,
                "outcome": outcome if outcome is not None else row.outcome,
                "receipt": receipt,
                "receipt_detail": receipt_detail,
            },
        )

    def record_receipt(
        self, watch_id: str, *, receipt: str, detail: str | None = None
    ) -> WatchView | None:
        """Attach what the transport said to an already-settled row."""
        return self._update(watch_id, lambda _row: {"receipt": receipt, "receipt_detail": detail})

    def reschedule(self, watch_id: str, next_due: datetime) -> None:
        """Record when a still-pending watch will next be evaluated.

        This is the one write on the polling path, so it is also the one place a
        careless implementation would turn "watch something for six hours" into
        720 file rewrites. The scheduler calls it only when the due time actually
        MOVES, and an unchanged due time writes nothing here either.
        """
        self._update(watch_id, lambda _row: {"next_due": next_due})

    def carry_on(self, watch_id: str, predicate: WatchPredicate, next_due: datetime) -> None:
        """Keep a standing watch pending with what it has now seen, and when to look again.

        One write, and only when the watcher reported a move, so an unchanged
        ticket costs no disk I/O. It is persisted BEFORE anything is mailed,
        for the same reason ``settle`` is: a crash between the two then loses
        one notification rather than repeating it on every restart.
        """
        self._update(
            watch_id,
            lambda row: (
                {"predicate": predicate, "next_due": next_due} if row.state == "pending" else None
            ),
        )

    def prune(self, now: datetime) -> int:
        """Drop settled rows past :data:`RETENTION`. Returns how many went."""
        cutoff = now - RETENTION
        dropped = [0]

        def change(records: Mapping[str, WatchView]) -> dict[str, WatchView] | None:
            keep = {
                key: row
                for key, row in records.items()
                if row.state not in TERMINAL_STATES
                or (row.settled_at is not None and row.settled_at > cutoff)
            }
            dropped[0] = len(records) - len(keep)
            return keep if dropped[0] else None

        self._file.mutate(change)
        return dropped[0]

    def _update(
        self, watch_id: str, fields: Callable[[WatchView], dict[str, Any] | None]
    ) -> WatchView | None:
        """Copy one row with ``fields`` applied, under the file lock. ``None`` if absent."""
        written: list[WatchView] = []

        def change(records: Mapping[str, WatchView]) -> dict[str, WatchView] | None:
            row = records.get(watch_id)
            if row is None:
                return None
            update = fields(row)
            if update is None:
                written.append(row)
                return None
            updated = row.model_copy(update=update)
            written.append(updated)
            return {**records, watch_id: updated}

        self._file.mutate(change)
        return written[0] if written else None


def _key(row: WatchView) -> str:
    return row.id


def _encode(row: WatchView) -> dict[str, Any]:
    return row.model_dump(mode="json")


def _decode(value: dict[str, Any]) -> WatchView | None:
    """A row, or ``None`` for a kind this release retired; raises for a newer build's row."""
    if _retired(value):
        # A kind this release REPLACED, not one a newer build owns: kept aside
        # it would sit in the log forever, unreadable by every build. The next
        # write drops it; its replacement is registered by the standing-watch
        # reconcile.
        return None
    return WatchView.model_validate(value)


def _legacy_rows(raw: bytes) -> list[dict[str, Any]]:
    """The version-1 single-document log as raw rows, refusing any other version."""
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1:
        found = data.get("version") if isinstance(data, dict) else None
        raise GroveError(f"watch log has version {found!r}; refusing to treat it as empty")
    rows = data.get("watches")
    if not isinstance(rows, dict):
        raise GroveError("`watches` must be an object")
    for key, row in rows.items():
        # The old format stored each row under its id AND inside it, and nothing
        # compared the two, so a flipped bit in either was carried forward by
        # every rewrite while lookups by id silently missed the row (measured in
        # a live log on 2026-10-15: `...3864...` keyed a row whose id read
        # `...3824...`). The records format keys by the row's own id, which
        # repairs it; say so rather than repairing in silence.
        if isinstance(row, dict) and row.get("id") != key:
            logger.warning(
                "watch log row keyed {} carries id {}; keeping it under its own id",
                key,
                row.get("id"),
            )
    return [row for row in rows.values() if isinstance(row, dict)]


def _retired(value: Any) -> bool:
    """Whether a raw row names a predicate kind this release retired."""
    predicate = value.get("predicate") if isinstance(value, dict) else None
    return isinstance(predicate, dict) and predicate.get("kind") in RETIRED_KINDS


def utcnow() -> datetime:
    """The clock the scheduler injects, isolated so tests can replace it."""
    return datetime.now(UTC)


__all__ = ["RETENTION", "TERMINAL_STATES", "WatchLog", "utcnow"]
