"""What a workspace was CALLED and what it CLAIMED, after the workspace is gone.

A workspace carries a title, a description, a task phase and a set of attached
tickets. Every one of those lives only in ``state.json`` or in a phase file
inside the worktree, and ``kill`` deletes both — which is the normal end of a
task, not an exceptional one. So the usage audit keeps the session rows and
loses every human-readable fact about them: measured on the reference host
2026-09-14, two of the ten workspace ids in ``usage-v7.sqlite3`` already named
workspaces that no longer existed, and those sessions can never be identified
again.

**This store is the durable half of a deliberate pair, and the split is the
whole design.** ``usage.sqlite3`` is a cache derived wholly from transcripts:
its filename carries the schema version, ``UsageStore._open`` drops the file
outright when that version moves, and ``_apply_retention`` deletes rows past
``usage.retention_days``. All three are correct for a projection that can be
rebuilt. None of them is survivable for a title, because a title is
re-derivable only while the workspace still exists — so a column there would
discard exactly the fact that cannot be recomputed. ``paths.quota_state_path``
and ``paths.session_turns_path`` already encode this same reasoning; this is the
third instance of a settled pattern, not a new judgement.

The usage cache ATTACHes this file and joins against it. That direction is the
only coupling: nothing here reads the cache, so *deleting the cache is still
always safe*, and this file's schema migrates rather than rebuilding.

**Recording is FORWARD-ONLY and nothing here reconstructs the past.** A
workspace killed before this shipped is unrecoverable — its record is gone and
the only remaining sources are guesses. A guessed title is indistinguishable on
the wire from a recorded one, which would make every degraded answer read as a
confident one; the same argument declined a ``base_commit`` backfill. Absent
reads as absent, and the collection grows from here.

**Every write is best-effort and never raises into its caller.** The producers
are ``JsonWorkspaceStore.save`` (on the lifecycle path) and the ~1 Hz activity
tick, so a failure here must cost one row rather than a ``create`` or a whole
dashboard. Failures log once, at DEBUG for the ordinary contended case and
WARNING for a real one.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from grove.core import paths
from grove.core.phase import normalize_phase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Sequence

    from grove.core.contracts.tickets import TicketRef
    from grove.core.phase import PhaseReport

SCHEMA_VERSION: Final = 1
"""Bumping this MIGRATES; it must never drop the file.

The opposite of ``usage._schema.SCHEMA_VERSION``, whose bump discards a cache
that can be rebuilt. Nothing here can be rebuilt, so a future version adds
columns or backfills them and leaves the rows in place.
"""

_NAMES: Final = """
CREATE TABLE IF NOT EXISTS workspace_names (
    workspace_id TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    repo_root    TEXT,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    deleted_at   INTEGER
);
"""
"""The join target: one row per workspace, holding its CURRENT name.

``deleted_at`` marks a workspace whose record ``kill`` removed. The row itself
survives — that is the entire point — so this is a tombstone rather than a
delete, and it lets a client say "this workspace is gone" instead of rendering a
name that implies it is still there.
"""

_NAME_HISTORY: Final = """
CREATE TABLE IF NOT EXISTS workspace_name_history (
    workspace_id TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT,
    recorded_at  INTEGER NOT NULL
);
"""
"""Every (title, description) pair a workspace has held, append-only.

**Deliberately no primary key, and that is a bug fix rather than an
omission.** The obvious ``PRIMARY KEY (workspace_id, recorded_at)`` drops a
legitimate rename: ``recorded_at`` is a whole-second epoch, and create-then-name
is *routinely* sub-second — the web composer does it, and so does ``grove edit``
run immediately after ``create``. Measured before the fix, four saves spanning
two distinct titles recorded ONE history row. Dedupe here is the caller's
``previous`` read (which is exact, not time-based), so the table needs no
uniqueness constraint and must not have one that discards by time.
"""

_PROGRESS: Final = """
CREATE TABLE IF NOT EXISTS workspace_progress_history (
    workspace_id TEXT NOT NULL,
    recorded_at  INTEGER NOT NULL,
    phase        TEXT NOT NULL DEFAULT '',
    blocked      INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    ticket_key   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (workspace_id, ticket_key, phase, blocked, note)
);
"""
"""The timeline of reported progress, one row per distinct claim.

``ticket_key`` is ``TicketRef.key`` (``"<provider>:<id>"``) for a per-ticket
claim and ``''`` for the workspace's own.

**EVERY key column is NOT NULL with an empty-string default, and each one is
load-bearing: SQLite treats NULLs in a UNIQUE/PRIMARY KEY as DISTINCT, so one
nullable key column defeats the dedupe entirely.** ``note`` is the column that
proved it — ``grove phase build`` with no note is the ordinary case, and
measured before the fix, **500 identical note-less ticks wrote 500 rows**
(~86,400 per day per workspace at the tick's cadence). The absent-vs-empty
distinction the rest of this tree defends is deliberately given up HERE and only
here, because a note is prose for a human and `''` and NULL render identically,
whereas an unbounded table is a real fault. The boundary translates both ways so
no consumer sees the artifact.

**The key deliberately spans the CLAIM, not the time.** An agent rewrites its
phase file on every transition and the tick reads it ~1 Hz, so a key carrying
``recorded_at`` would append a row per second forever; keying on the claim's
content makes ``INSERT OR IGNORE`` the dedupe and leaves ``recorded_at`` as the
first time this claim was seen. The cost is that a phase revisited after a
detour (``verify`` → ``plan`` → ``verify``) keeps its FIRST timestamp
and records no second row, which is the right trade for a store that must not
grow without bound on an idle fleet.
"""

_TICKETS: Final = """
CREATE TABLE IF NOT EXISTS workspace_tickets (
    workspace_id TEXT NOT NULL,
    ticket_key   TEXT NOT NULL,
    provider     TEXT NOT NULL,
    ticket_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, ticket_key)
);
"""
"""Every ticket a workspace has been attached to, surviving its teardown.

Keyed by ``(workspace_id, ticket_key)`` so ``kind`` is UPDATEd in place when a
branch parse guessed ``issue`` and a later attach corrects it to
``pull_request`` — mirroring ``attach_ticket``'s own idempotency, which keys on
``(provider, id)`` and deliberately excludes ``kind``. A DETACH does not delete
the row: the question this answers is "what work did this workspace do", and a
ref that was attached for a week is part of that answer.
"""

_META: Final = """
CREATE TABLE IF NOT EXISTS workspace_history_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
"""Prefixed, NOT ``meta``, because this file is ATTACHed into another database.

SQLite resolves an unqualified table name across main and every attached schema,
so a second ``meta`` here SHADOWS the usage cache's own whenever main does not
have one yet — which is exactly a fresh cache. Measured before the rename: the
cache read THIS table's version, concluded ``schema 1 != 7`` and dropped and
rebuilt itself on every single startup. **A table in a file that gets attached
lives in the HOST's namespace, so it must be named as if the host's tables were
its siblings.**
"""

_INDEXES: Final = (
    "CREATE INDEX IF NOT EXISTS ix_progress_workspace ON workspace_progress_history(workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_name_history_workspace ON workspace_name_history(workspace_id)",
)

DDL: Final = (_NAMES, _NAME_HISTORY, _PROGRESS, _TICKETS, _META, *_INDEXES)

SCHEMA_VERSION_KEY: Final = "schema_version"


@dataclass(frozen=True, slots=True)
class WorkspaceName:
    """One workspace's recorded name, live or long since killed."""

    workspace_id: str
    title: str
    description: str | None
    repo_root: str | None
    first_seen: datetime
    last_seen: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class NameChange:
    """A (title, description) pair a workspace held, and when it was first seen."""

    title: str
    description: str | None
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ProgressEntry:
    """One distinct progress claim, workspace-level or per ticket.

    ``ticket_key`` is ``None`` for the workspace's own claim — translated from
    the table's ``''`` at the boundary, so no consumer has to know that the
    empty string is a primary-key artifact.
    """

    recorded_at: datetime
    phase: str | None
    blocked: bool
    note: str | None
    ticket_key: str | None


@dataclass(frozen=True, slots=True)
class RecordedTicket:
    """A ticket this workspace was attached to, with the window it was attached."""

    ticket_key: str
    provider: str
    ticket_id: str
    kind: str
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceHistory:
    """Everything recorded about one workspace — the per-request read."""

    name: WorkspaceName | None
    names: tuple[NameChange, ...]
    progress: tuple[ProgressEntry, ...]
    tickets: tuple[RecordedTicket, ...]

    @property
    def is_empty(self) -> bool:
        """Nothing recorded — which is EVERY workspace that predates this store.

        Clients gate their affordance on this: an icon opening onto an empty
        dialog reads as a broken feature, where an absent icon reads as an
        absent fact.
        """
        return not (self.names or self.progress or self.tickets)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _at(raw: int | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromtimestamp(raw, tz=UTC)


class WorkspaceHistoryStore:
    """Durable names, progress claims and tickets — one SQLite file, one writer lock.

    Mirrors ``UsageStore``'s connection discipline (WAL, a busy timeout, one
    connection under one lock) because the same three processes reach it: the
    daemon, a ``grove`` CLI verb and the TUI. It does NOT take
    ``paths.exclusive_lock``: SQLite in WAL mode owns its own cross-process
    concurrency, and unlike ``state.json`` this file is never republished by
    rename, so there is no stage file for a lock to guard.
    """

    def __init__(self, path: Path | None = None, *, busy_timeout_ms: int = 5000) -> None:
        self._path = path if path is not None else paths.workspace_history_path()
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    def ensure_schema(self) -> None:
        """Create the file and apply the DDL now, rather than on first write.

        Exists for the ATTACH: ``sqlite3``'s ``ATTACH`` on a path with no
        database creates an EMPTY one, so a caller that attaches before anything
        has written here gets a schema with none of the tables its join names —
        failing at query time, where the cause is invisible. Construction alone
        is deliberately lazy (every CLI verb builds a store), so the attacher
        has to ask.
        """
        with self._lock:
            self._connect()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── writes (best-effort, never raise) ─────────────────────────────────

    def record_name(
        self,
        workspace_id: str,
        *,
        title: str,
        description: str | None,
        repo_root: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Upsert the current name and append a history row IF the pair changed.

        Called from ``JsonWorkspaceStore.save``, which every title assignment
        passes through — create, rename and every other mutation — so one
        capture covers all of them and a future verb cannot forget it.

        Re-recording an unchanged pair only moves ``last_seen``. That is what
        keeps the history a list of renames rather than a log of saves, and it
        is asserted directly: an unchanged save must append nothing.
        """
        stamp = _epoch(now or datetime.now(tz=UTC))
        try:
            with self._write() as conn:
                previous = conn.execute(
                    "SELECT title, description FROM workspace_names WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
                conn.execute(
                    "INSERT INTO workspace_names("
                    "workspace_id, title, description, repo_root, first_seen, last_seen"
                    ") VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET "
                    "title=excluded.title, description=excluded.description, "
                    # A later save must not blank a repo_root an earlier one
                    # recorded, so an absent value yields to what is stored.
                    "repo_root=COALESCE(excluded.repo_root, workspace_names.repo_root), "
                    "last_seen=excluded.last_seen",
                    (workspace_id, title, description, repo_root, stamp, stamp),
                )
                if previous is not None and (previous[0], previous[1]) == (title, description):
                    return
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_name_history("
                    "workspace_id, title, description, recorded_at) VALUES(?,?,?,?)",
                    (workspace_id, title, description, stamp),
                )
        except sqlite3.DatabaseError as exc:
            self._note_failure("record_name", workspace_id, exc)

    def mark_deleted(self, workspace_id: str, *, now: datetime | None = None) -> None:
        """Tombstone a workspace whose record was just deleted.

        The name row and every claim survive; only ``deleted_at`` is set, so a
        usage row can still resolve its title and a client can say the workspace
        is gone rather than implying it is live. A workspace never recorded is
        not invented here — an unknown id inserts nothing.
        """
        stamp = _epoch(now or datetime.now(tz=UTC))
        try:
            with self._write() as conn:
                conn.execute(
                    "UPDATE workspace_names SET deleted_at = ?, last_seen = ? "
                    "WHERE workspace_id = ?",
                    (stamp, stamp, workspace_id),
                )
        except sqlite3.DatabaseError as exc:
            self._note_failure("mark_deleted", workspace_id, exc)

    def record_progress(
        self,
        workspace_id: str,
        report: PhaseReport | None,
        *,
        now: datetime | None = None,
    ) -> None:
        """Record the workspace's claim and each ticket's, deduped by CONTENT.

        Called from the ~1 Hz activity tick, which already reads the phase, so
        this adds no I/O to the poll path beyond the insert itself. ``INSERT OR
        IGNORE`` against a content-shaped primary key is what keeps a tick that
        sees the same claim a thousand times from writing a thousand rows — the
        single most important property of this method, and the one its test
        mutates to prove.
        """
        if report is None:
            return
        stamp = _epoch(now or datetime.now(tz=UTC))
        # `or ""` on every key column, for the NULL-is-distinct reason the table
        # documents. A note is optional on both the workspace claim and each
        # ticket's, so both call sites need it.
        rows: list[tuple[str, int, str, int, str, str]] = [
            (workspace_id, stamp, report.phase, int(report.blocked), report.note or "", "")
        ]
        rows.extend(
            (
                workspace_id,
                stamp,
                claim.phase,
                int(claim.blocked),
                claim.note or "",
                claim.ticket,
            )
            for claim in report.tickets
        )
        try:
            with self._write() as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO workspace_progress_history("
                    "workspace_id, recorded_at, phase, blocked, note, ticket_key"
                    ") VALUES(?,?,?,?,?,?)",
                    rows,
                )
        except sqlite3.DatabaseError as exc:
            self._note_failure("record_progress", workspace_id, exc)

    def record_tickets(
        self,
        workspace_id: str,
        refs: Sequence[TicketRef],
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record new refs or kind corrections, returning whether SQLite changed.

        The activity reader may call this on every poll. Its conditional upsert
        changes neither ``last_seen`` nor the ticket row for an identical ref,
        so a read is not misrepresented as fresh ticket activity.
        """
        if not refs:
            return False
        stamp = _epoch(now or datetime.now(tz=UTC))
        try:
            with self._write() as conn:
                return self._upsert_tickets(conn, workspace_id, refs, stamp)
        except sqlite3.DatabaseError as exc:
            self._note_failure("record_tickets", workspace_id, exc)
            return False

    def record_ticket_transition(
        self,
        workspace_id: str,
        previous: Sequence[TicketRef],
        current: Sequence[TicketRef],
        *,
        now: datetime | None = None,
    ) -> bool:
        """Atomically record one durable ticket membership transition.

        ``JsonWorkspaceStore.save`` supplies the state before and after its
        atomic publication. Attach and kind-correction rows are captured from
        the new state; a detach ends the stored attachment window without
        deleting the work history. Exact replays return ``False`` before taking
        a SQLite write transaction.
        """
        previous_by_key = {ref.key: ref for ref in previous}
        current_by_key = {ref.key: ref for ref in current}
        changed = [
            ref
            for key, ref in current_by_key.items()
            if (before := previous_by_key.get(key)) is None or before.kind != ref.kind
        ]
        detached = tuple(key for key in previous_by_key if key not in current_by_key)
        if not changed and not detached:
            return False
        stamp = _epoch(now or datetime.now(tz=UTC))
        try:
            with self._write() as conn:
                recorded = self._upsert_tickets(conn, workspace_id, changed, stamp)
                for ticket_key in detached:
                    result = conn.execute(
                        "UPDATE workspace_tickets SET last_seen = ? "
                        "WHERE workspace_id = ? AND ticket_key = ? AND last_seen IS NOT ?",
                        (stamp, workspace_id, ticket_key, stamp),
                    )
                    recorded = recorded or result.rowcount > 0
                return recorded
        except sqlite3.DatabaseError as exc:
            self._note_failure("record_ticket_transition", workspace_id, exc)
            return False

    def _upsert_tickets(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        refs: Sequence[TicketRef],
        stamp: int,
    ) -> bool:
        """Write refs only when their durable ticket identity or kind changed."""
        recorded = False
        for ref in refs:
            result = conn.execute(
                "INSERT INTO workspace_tickets("
                "workspace_id, ticket_key, provider, ticket_id, kind, first_seen, last_seen"
                ") VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id, ticket_key) DO UPDATE SET "
                "kind=excluded.kind, last_seen=excluded.last_seen "
                "WHERE workspace_tickets.kind IS NOT excluded.kind",
                (
                    workspace_id,
                    ref.key,
                    ref.provider,
                    ref.id,
                    ref.kind,
                    stamp,
                    stamp,
                ),
            )
            recorded = recorded or result.rowcount > 0
        return recorded

    # ── reads ─────────────────────────────────────────────────────────────

    def history_for(self, workspace_id: str) -> WorkspaceHistory:
        """Everything recorded about one workspace, newest claim first.

        Best-effort like the writes: an unreadable store answers an EMPTY
        history rather than raising, because the caller is a render path and a
        missing timeline must not take a page down.
        """
        try:
            with self._lock:
                conn = self._connect()
                name_row = conn.execute(
                    "SELECT workspace_id, title, description, repo_root, first_seen, last_seen, "
                    "deleted_at FROM workspace_names WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
                names = conn.execute(
                    # `rowid DESC` breaks the tie, and the tie is the COMMON
                    # case rather than an edge: `recorded_at` is a whole second
                    # and create-then-rename routinely lands inside one, so
                    # ordering by the timestamp alone leaves two renames in
                    # insertion order — i.e. oldest-first, the exact opposite of
                    # what this read contracts. SQLite's rowid is monotonic for
                    # an append-only table, which this one is.
                    "SELECT title, description, recorded_at FROM workspace_name_history "
                    "WHERE workspace_id = ? ORDER BY recorded_at DESC, rowid DESC",
                    (workspace_id,),
                ).fetchall()
                progress = conn.execute(
                    "SELECT recorded_at, phase, blocked, note, ticket_key "
                    "FROM workspace_progress_history WHERE workspace_id = ? "
                    "ORDER BY recorded_at DESC, ticket_key ASC",
                    (workspace_id,),
                ).fetchall()
                tickets = conn.execute(
                    "SELECT ticket_key, provider, ticket_id, kind, first_seen, last_seen "
                    "FROM workspace_tickets WHERE workspace_id = ? ORDER BY first_seen ASC",
                    (workspace_id,),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.warning("workspace history unreadable for {}: {}", workspace_id, exc)
            return WorkspaceHistory(name=None, names=(), progress=(), tickets=())
        return WorkspaceHistory(
            name=self._name(name_row),
            names=tuple(
                NameChange(
                    title=row[0],
                    description=row[1],
                    # A row cannot exist without its timestamp (NOT NULL), so
                    # the narrowing below is a type concern, not a real branch.
                    recorded_at=_at(row[2]) or datetime.fromtimestamp(0, tz=UTC),
                )
                for row in names
            ),
            progress=tuple(
                ProgressEntry(
                    recorded_at=_at(row[0]) or datetime.fromtimestamp(0, tz=UTC),
                    # Empty back to None on the way out, so the storage artifact
                    # that the dedupe needs never reaches a consumer.
                    phase=normalize_phase(row[1]) if row[1] else None,
                    blocked=bool(row[2]),
                    note=row[3] or None,
                    ticket_key=row[4] or None,
                )
                for row in progress
            ),
            tickets=tuple(
                RecordedTicket(
                    ticket_key=row[0],
                    provider=row[1],
                    ticket_id=row[2],
                    kind=row[3],
                    first_seen=_at(row[4]) or datetime.fromtimestamp(0, tz=UTC),
                    last_seen=_at(row[5]) or datetime.fromtimestamp(0, tz=UTC),
                )
                for row in tickets
            ),
        )

    def names_for(self, workspace_ids: Iterable[str]) -> dict[str, WorkspaceName]:
        """Recorded names for the given ids — the bulk read a listing wants."""
        wanted = list(dict.fromkeys(workspace_ids))
        if not wanted:
            return {}
        placeholders = ",".join("?" * len(wanted))
        try:
            with self._lock:
                rows = (
                    self._connect()
                    .execute(
                        "SELECT workspace_id, title, description, repo_root, first_seen, "
                        "last_seen, deleted_at FROM workspace_names "
                        f"WHERE workspace_id IN ({placeholders})",
                        wanted,
                    )
                    .fetchall()
                )
        except sqlite3.DatabaseError as exc:
            logger.warning("workspace names unreadable: {}", exc)
            return {}
        resolved = {}
        for row in rows:
            name = self._name(row)
            if name is not None:
                resolved[name.workspace_id] = name
        return resolved

    # ── internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _name(row: sqlite3.Row | None) -> WorkspaceName | None:
        if row is None:
            return None
        epoch = datetime.fromtimestamp(0, tz=UTC)
        return WorkspaceName(
            workspace_id=row[0],
            title=row[1],
            description=row[2],
            repo_root=row[3],
            first_seen=_at(row[4]) or epoch,
            last_seen=_at(row[5]) or epoch,
            deleted_at=_at(row[6]),
        )

    def _note_failure(self, op: str, workspace_id: str, exc: Exception) -> None:
        """One line per failed write — a lost row must never reach the caller.

        DEBUG for a busy database (ordinary under three concurrent processes and
        self-correcting, since the next save or tick re-records), WARNING for
        anything else, which is a real fault worth seeing.
        """
        if isinstance(exc, sqlite3.OperationalError):
            logger.debug("workspace history {} deferred for {}: {}", op, workspace_id, exc)
            return
        logger.warning("workspace history {} failed for {}: {}", op, workspace_id, exc)

    def _write(self) -> _Transaction:
        return _Transaction(self)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            paths.ensure_dir(self._path.parent)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
            for statement in DDL:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO workspace_history_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
            )
            conn.commit()
            self._conn = conn
        return self._conn


class _Transaction:
    """Commit on a clean exit, roll back on any raise — ``UsageStore.write``'s shape."""

    def __init__(self, store: WorkspaceHistoryStore) -> None:
        self._store = store

    def __enter__(self) -> sqlite3.Connection:
        self._store._lock.acquire()
        try:
            return self._store._connect()
        except BaseException:
            self._store._lock.release()
            raise

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        conn = self._store._conn
        try:
            if conn is not None:
                if exc_type is None:
                    conn.commit()
                else:
                    conn.rollback()
        finally:
            self._store._lock.release()
