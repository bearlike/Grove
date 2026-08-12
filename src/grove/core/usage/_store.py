"""The usage cache's SQLite mechanics — open it, version it, write it, bucket it.

Pure mechanism. Nothing here knows what a provider is, what a token class means
or which card a number ends up on: it opens a connection, guarantees the shape
in ``_schema.py``, hands out a cursor under one lock, and answers the one
calendar question SQL cannot answer for itself.

Three properties are load-bearing:

**Rebuild, never migrate.** A schema-version mismatch deletes the file and
starts again. Every row is derived from a transcript still on disk, so the
expensive-and-dangerous half of a persistence layer buys nothing here.

**One lock, one writer.** WAL lets readers run through a write, but Python's
``sqlite3`` connection is not a concurrency primitive — the daemon calls
``refresh()`` from one executor thread while a request reads from another. The
connection is opened ``check_same_thread=False`` and every statement runs under
:attr:`UsageStore.lock`, so "safe to call concurrently" is a property of this
class rather than a rule each caller has to remember.

**Day boundaries are computed, never stored.** :func:`day_boundaries` turns a
range plus an IANA zone into exact per-day epoch spans via ``zoneinfo``, and the
queries JOIN against them. A fixed hour offset is wrong twice a year in every
zone that observes DST, and a stored ``day`` column would answer every reader in
whatever zone the indexer happened to run in.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from grove.core import paths
from grove.core.usage._schema import DDL, SCHEMA_VERSION

SCHEMA_VERSION_KEY: Final = "schema_version"
"""``meta`` row holding the version the file on disk was written under."""

LAST_REFRESH_KEY: Final = "last_refresh_at"
"""``meta`` row holding the epoch second of the last completed refresh."""

MAX_DAY_BUCKETS: Final = 3660
"""Ten years of day buckets. A bound rather than a policy: an unbounded range
(a caller passing ``datetime.min``) would otherwise materialize millions of rows
into a temp table before the first byte of the answer is computed."""


@dataclass(frozen=True, slots=True)
class DayBucket:
    """One calendar day in the reader's chosen zone, as an epoch half-open span.

    ``day`` is the ``YYYY-MM-DD`` label the wire carries; ``start`` is inclusive
    and ``end`` exclusive, so consecutive buckets tile the range with no gap and
    no overlap even across a DST transition (where a day is 23 or 25 hours).
    """

    day: str
    start: int
    end: int


def resolve_zone(tz: str) -> tzinfo:
    """``tz`` as a ``tzinfo``, falling back to UTC for an unknown name.

    The contract (``UsageFilters.tz``) promises a bad clock preference degrades
    rather than blanking the page, and this is the single place that promise is
    kept — every bucketing path resolves its zone here.
    """
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.debug("usage: unknown timezone {!r}, bucketing in UTC", tz)
        return UTC


def day_boundaries(since: datetime, until: datetime, tz: str) -> tuple[DayBucket, ...]:
    """Exact per-day epoch spans covering ``since``..``until`` in zone ``tz``.

    The shared seam behind the activity graph and every detector that reasons
    about a day: both must agree on where a day starts, or the heatmap and the
    finding it links to describe different sets of sessions.

    Boundaries are built by *date* arithmetic on local midnights and converted
    once, never by adding 86400 seconds — a spring-forward day is 82800 seconds
    long and a fixed stride would drift a full hour, silently, for the rest of
    the series. A day whose local midnight does not exist (a zone that jumps
    over it) still yields a monotonic boundary, because ``datetime`` resolves
    the offset for the nominal wall time.

    Empty when ``until`` precedes ``since``; capped at :data:`MAX_DAY_BUCKETS`.
    """
    zone = resolve_zone(tz)
    if until < since:
        return ()
    first = since.astimezone(zone).date()
    last = until.astimezone(zone).date()
    out: list[DayBucket] = []
    current = first
    while current <= last and len(out) < MAX_DAY_BUCKETS:
        nxt = current + timedelta(days=1)
        out.append(
            DayBucket(
                day=current.isoformat(),
                start=_local_midnight(current, zone),
                end=_local_midnight(nxt, zone),
            )
        )
        current = nxt
    return tuple(out)


def _local_midnight(day: date, zone: tzinfo) -> int:
    return int(datetime.combine(day, time.min, tzinfo=zone).timestamp())


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """What makes one transcript file "the same file, unchanged".

    ``(inode, size, mtime_ns)`` is the identity the incremental ingest keys on.
    An inode change is a rotation, any non-growth size change is a rewrite, and
    both mean the byte cursor describes bytes that no longer exist — so that one
    source resets rather than appending onto a stale fold. Exactly the rule
    ``TranscriptCache`` applies one layer down, restated here because this layer
    decides whether to call the adapter at all.
    """

    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> FileFingerprint | None:
        """``path``'s fingerprint, or ``None`` when it cannot be stat'd."""
        try:
            stat = path.stat()
        except OSError:
            return None
        return cls(inode=stat.st_ino, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def complete_bytes(path: Path, *, chunk: int = 65536) -> int:
    """Byte offset just past the file's last complete line.

    The honest value for ``ingested_files.cursor``: a JSONL writer mid-append
    leaves a partial trailing line, and counting it as consumed would skip that
    record forever once the line completed without changing the file's size
    class. Scans backwards in chunks rather than reading the file, because a
    single tool-result line can be megabytes and the answer is always near the
    end. ``0`` when the file holds no newline at all or cannot be read.
    """
    try:
        with path.open("rb") as handle:
            end = handle.seek(0, 2)
            pos = end
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                handle.seek(pos)
                window = handle.read(step)
                found = window.rfind(b"\n")
                if found != -1:
                    return pos + found + 1
    except OSError as exc:
        logger.debug("usage: cursor probe failed for {}: {}", path, exc)
    return 0


class UsageStore:
    """The derived SQLite cache: connection lifecycle, schema guarantee, one lock.

    Construct with an explicit ``db_path`` in tests; production passes
    ``paths.usage_db_path()``. The file is created (with its parent) on first
    use, brought to :data:`SCHEMA_VERSION` — by deletion and rebuild if it was
    written under another version — and left in WAL mode.

    ``busy_timeout_ms`` is ``UsageConfig.busy_timeout_ms`` (default 5000):
    WAL lets readers run through a write, but two WRITERS — one in this
    process's executor thread and one in another process entirely (the TUI's
    Usage screen, ``grove usage backfill``) — can still collide, and SQLite's
    own default busy timeout is 0, which raises ``database is locked``
    immediately rather than waiting. The default here is a policy call an
    operator may reasonably override, so it arrives as a constructor
    parameter rather than a literal baked into ``_connect_prepared``.
    """

    def __init__(self, db_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self._path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock(self) -> threading.RLock:
        """The mutex every statement on this store's connection is held under.

        Exposed so a caller composing several statements into one logical unit
        (an ingest's delete-then-insert) holds it across the whole unit rather
        than re-acquiring per statement and letting a reader see the gap.
        """
        return self._lock

    def connect(self) -> sqlite3.Connection:
        """The open connection, opening and preparing the file on first call."""
        with self._lock:
            if self._conn is None:
                self._conn = self._open()
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """A transaction: committed on a clean exit, rolled back on any raise."""
        conn = self.connect()
        with self._lock:
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """One read, under the lock. Rows are ``sqlite3.Row`` (index by name)."""
        conn = self.connect()
        with self._lock:
            return conn.execute(sql, tuple(params)).fetchall()

    def query_tuples(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        """Like :meth:`query`, but rows are plain tuples, not ``sqlite3.Row``.

        For a hot per-EVENT loop (a wide window is up to ~330k rows) rather
        than a per-session one: ``Row.__getitem__`` resolves a string key by
        scanning the cursor description on every access, paid once per
        accessed column per row, for a caller that already fixed the column
        order in its own ``SELECT``. Swapping ``row_factory`` for the
        duration of one call is safe under :attr:`lock` because this store
        holds exactly one connection, shared by every caller under that same
        lock.
        """
        conn = self.connect()
        with self._lock:
            previous = conn.row_factory
            conn.row_factory = None
            try:
                return conn.execute(sql, tuple(params)).fetchall()
            finally:
                conn.row_factory = previous

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def meta(self, key: str) -> str | None:
        value = self.scalar("SELECT value FROM meta WHERE key = ?", (key,))
        return str(value) if value is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def size_bytes(self) -> int:
        """The cache's on-disk size, for the status seam. ``0`` when absent."""
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def drop(self) -> None:
        """Delete the database and its WAL sidecars — the rebuild primitive.

        Public because "delete the cache and refresh" must reproduce identical
        results, so a caller (and the test that pins it) needs the same door the
        version check uses rather than reaching for ``unlink`` on a path it
        guessed.
        """
        with self._lock:
            self.close()
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(self._path) + suffix)
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover - unwritable state dir
                    logger.warning("usage: could not remove {}: {}", candidate, exc)

    # ── internal ──────────────────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        paths.ensure_dir(self._path.parent)
        conn = self._connect_prepared()
        stored = self._stored_version(conn)
        if stored == SCHEMA_VERSION:
            return conn
        if stored is not None:
            logger.info(
                "usage: cache schema {} != {}, rebuilding {}", stored, SCHEMA_VERSION, self._path
            )
            conn.close()
            self._conn = None
            self.drop()
            conn = self._connect_prepared()
        self._apply_schema(conn)
        return conn

    def _connect_prepared(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
        return conn

    @staticmethod
    def _stored_version(conn: sqlite3.Connection) -> int | None:
        """The version this file was written under, or ``None`` for a fresh one.

        An unreadable ``meta`` (a truncated or foreign file at that path) reads
        as a version mismatch rather than an error — the cache is disposable and
        a corrupt one must not take a whole page down with it.
        """
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
            ).fetchone()
        except sqlite3.DatabaseError:
            return -1
        if row is None:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _apply_schema(conn: sqlite3.Connection) -> None:
        for statement in DDL:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION)),
        )
        conn.commit()
