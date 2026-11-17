"""The reaper's durable memory: when each candidate was first seen, and every action."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from grove.core import paths
from grove.core.preflight import CheckResult
from grove.core.reaper.ledger import ResourceClass, ResourceRef

#: What a pass did about one candidate. ``mark`` records first sight; ``sweep``
#: is a removal; ``report`` is a sweep a dry run declined to perform; ``clear``
#: means a marked candidate went away on its own, so a later reappearance starts
#: a fresh grace instead of inheriting a stale one.
Action = Literal["mark", "sweep", "report", "clear"]

#: How the action ended.
Outcome = Literal["ok", "failed"]


@dataclass(frozen=True, slots=True)
class ReaperRow:
    """One line of the append-only action log."""

    ref: ResourceRef
    action: Action
    reason: str
    outcome: Outcome
    error: str
    at: datetime


class ReaperStore:
    """Append-only SQLite rows under the state dir; first-seen is derived, never updated.

    First-seen is the earliest ``mark`` row for a ref since it was last swept
    or cleared, so a daemon restart between mark and sweep keeps the grace running
    from the original sighting rather than starting it over.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS reaper_log (
            cls TEXT NOT NULL,
            key TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reaper_log_ref ON reaper_log (cls, key);
        CREATE TABLE IF NOT EXISTS reaper_last_pass (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            at TEXT NOT NULL,
            standing TEXT NOT NULL
        );
    """

    # The earliest mark since the ref's last successful sweep or its last clear.
    _FIRST_SEEN = (
        "SELECT MIN(at) FROM reaper_log WHERE cls = ? AND key = ? AND action = 'mark' "
        "AND at > COALESCE((SELECT MAX(at) FROM reaper_log WHERE cls = ? AND key = ? "
        "AND (action = 'clear' OR (action = 'sweep' AND outcome = 'ok'))), '')"
    )

    def __init__(self, path: Path | None = None) -> None:
        """Open (and create) the log.

        Args:
            path: The database file; ``None`` is the per-user state location.
        """
        self._path = path if path is not None else paths.reaper_db_path()

    def _connect(self) -> sqlite3.Connection:
        paths.ensure_dir(self._path.parent)
        connection = sqlite3.connect(self._path)
        connection.executescript(self._SCHEMA)
        return connection

    def first_seen(self, refs: Iterable[ResourceRef]) -> dict[ResourceRef, datetime]:
        """When each still-unswept candidate was first marked; absent if never."""
        wanted = list(refs)
        if not wanted:
            return {}
        found: dict[ResourceRef, datetime] = {}
        with self._connect() as db:
            for ref in wanted:
                row = db.execute(self._FIRST_SEEN, (ref.cls, ref.key, ref.cls, ref.key)).fetchone()
                if row and row[0]:
                    found[ref] = datetime.fromisoformat(row[0])
        return found

    def marked(self) -> frozenset[ResourceRef]:
        """Every candidate with a mark that has not since been swept or cleared."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT cls, key, workspace_id FROM reaper_log WHERE action = 'mark'"
            ).fetchall()
            return frozenset(
                ResourceRef(cls, key, workspace_id)
                for cls, key, workspace_id in rows
                if db.execute(self._FIRST_SEEN, (cls, key, cls, key)).fetchone()[0]
            )

    def record(self, rows: Iterable[ReaperRow], *, standing: dict[ResourceClass, int]) -> None:
        """Append state-changing actions and replace the latest pass summary.

        A dry-run ``report`` repeats unchanged candidates every interval, so it
        belongs only in the in-memory :class:`PassReport`; persisting each one
        made the ledger grow by one row per leak per pass. The singleton summary
        retains the doctor result without turning that observation into history.
        """
        records = tuple(rows)
        batch = [
            (
                r.ref.cls,
                r.ref.key,
                r.ref.workspace_id,
                r.action,
                r.reason,
                r.outcome,
                r.error,
                r.at.astimezone(UTC).isoformat(),
            )
            for r in records
            if r.action != "report"
        ]
        at = (
            records[-1].at.astimezone(UTC).isoformat() if records else datetime.now(UTC).isoformat()
        )
        with self._connect() as db:
            # Reports from older builds are duplicate observations, not action
            # history. Prune them with the first new pass before they make every
            # first-seen lookup scan an ever-growing table.
            db.execute("DELETE FROM reaper_log WHERE action = 'report'")
            db.executemany("INSERT INTO reaper_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
            db.execute(
                "INSERT OR REPLACE INTO reaper_last_pass VALUES (1, ?, ?)",
                (at, json.dumps(standing, sort_keys=True)),
            )

    def doctor_row(self) -> CheckResult:
        """The ``grove doctor`` "leaked resources" row, read from the latest pass.

        ``optional`` by scope: a leak is a fact worth seeing, never a reason to
        refuse a create, so it must stay out of every readiness set.
        """
        last = self.last_pass()
        if last is None:
            return CheckResult(
                name="leaked resources",
                ok=True,
                detail="the reaper has not run on this host yet",
                required_for="optional",
            )
        at, standing = last
        when = at.strftime("%Y-%m-%d %H:%M UTC")
        if not standing:
            return CheckResult(
                name="leaked resources",
                ok=True,
                detail=f"none at the last pass ({when})",
                required_for="optional",
            )
        found = ", ".join(f"{n} {cls}" for cls, n in sorted(standing.items()))
        return CheckResult(
            name="leaked resources",
            ok=False,
            detail=f"{found} at the last pass ({when})",
            hint="`grove vacuum` lists them; `grove vacuum --apply` removes what is past its grace",
            required_for="optional",
        )

    def last_pass(self) -> tuple[datetime, dict[ResourceClass, int]] | None:
        """When the latest pass wrote, and how many candidates per class it left standing.

        ``None`` when the reaper has never recorded anything.
        """
        with self._connect() as db:
            current = db.execute(
                "SELECT at, standing FROM reaper_last_pass WHERE singleton = 1"
            ).fetchone()
            if current is not None:
                at, standing = current
                return datetime.fromisoformat(at), json.loads(standing)
            # A daemon upgraded before its first new pass still serves the last
            # result the old append-only format wrote.
            latest = db.execute("SELECT MAX(at) FROM reaper_log").fetchone()[0]
            if not latest:
                return None
            counts = db.execute(
                "SELECT cls, COUNT(*) FROM reaper_log WHERE at = ? "
                "AND action IN ('mark', 'report', 'sweep') "
                "AND (action != 'sweep' OR outcome = 'failed') GROUP BY cls",
                (latest,),
            ).fetchall()
        return datetime.fromisoformat(latest), dict(counts)


__all__ = ["Action", "Outcome", "ReaperRow", "ReaperStore"]
