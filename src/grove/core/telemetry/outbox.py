"""A durable outbox before the daemon's span exporter: a crash or dead collector loses nothing.

The OTLP exporter holds spans in memory until a collector accepts them, so a
daemon restart, a SIGKILL or a collector outage drops whatever was queued, and
nothing records that it happened. The outbox writes each span to a small
SQLite file BEFORE handing it to the exporter, deletes it once a flush
acknowledges the batch it rode in, and on construction replays whatever a
previous process left, ahead of anything new.

Three properties keep it safe to run unattended:

* **Bounded.** At most :attr:`OutboxSink.CAPACITY` rows. When a dead backend
  fills it, the OLDEST rows are dropped and the count is logged, so a disk can
  never fill and the newest record of what happened survives.
* **Idempotent by span identity.** A row is keyed ``(trace_id, span_id)``,
  which are deterministic for every replayed observation, so re-enqueueing one
  replaces it rather than duplicating it, the same identity the export ledger
  reconciles on.
* **Best-effort at the edge.** A store that cannot be opened or written logs
  and passes spans straight through; telemetry must never fail the work it
  describes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from grove.core import paths
from grove.core.trace import SpanRecord, SpanSink

_DDL = """
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    record TEXT NOT NULL,
    UNIQUE (trace_id, span_id)
)
"""


class OutboxSink:
    """A :class:`SpanSink` that persists each span until the inner sink acknowledges it."""

    CAPACITY: ClassVar[int] = 50_000
    """Rows held before the oldest are dropped. At a few hundred bytes a span this
    is tens of megabytes, which is a day of a busy fleet with the collector down."""

    def __init__(self, inner: SpanSink, *, path: Path | None = None) -> None:
        """Wrap *inner* and send anything a previous process left behind first.

        Args:
            inner: the exporter-backed sink spans are delivered to.
            path: the outbox file; ``None`` uses ``paths.telemetry_outbox_path()``.
        """
        self._inner = inner
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        try:
            target = path or paths.telemetry_outbox_path()
            paths.ensure_dir(target.parent)
            self._db = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
            self._db.execute(_DDL)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("telemetry outbox unavailable, spans pass straight through: {}", exc)
            self._db = None
        self.drain()

    def __call__(self, record: SpanRecord) -> None:
        self._enqueue(record)
        self._inner(record)

    def flush(self) -> bool:
        """Flush the exporter; on acknowledgement, forget every span it carried."""
        try:
            acknowledged = self._inner.flush()
        except Exception as exc:
            logger.debug("telemetry flush not acknowledged, outbox keeps its rows: {}", exc)
            return False
        if acknowledged:
            self._clear()
        return acknowledged

    def drain(self) -> int:
        """Re-send every span a previous run left, oldest first, then flush once.

        Returns:
            How many spans were re-sent; zero when the outbox was empty or unavailable.
        """
        pending = list(self._pending())
        if not pending:
            return 0
        for record in pending:
            self._inner(record)
        logger.info("telemetry outbox: re-sent {} span(s) left by a previous run", len(pending))
        self.flush()
        return len(pending)

    def _enqueue(self, record: SpanRecord) -> None:
        if self._db is None:
            return
        try:
            with self._lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO outbox (trace_id, span_id, record) VALUES (?, ?, ?)",
                    (f"{record.trace_id:032x}", f"{record.span_id:016x}", _encode(record)),
                )
                self._bound()
        except sqlite3.Error as exc:
            logger.debug("telemetry outbox write failed: {}", exc)

    def _bound(self) -> None:
        assert self._db is not None
        (count,) = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()
        excess = count - self.CAPACITY
        if excess > 0:
            self._db.execute(
                "DELETE FROM outbox WHERE seq IN (SELECT seq FROM outbox ORDER BY seq LIMIT ?)",
                (excess,),
            )
            logger.warning("telemetry outbox full: dropped the {} oldest span(s)", excess)

    def _clear(self) -> None:
        if self._db is None:
            return
        try:
            with self._lock:
                self._db.execute("DELETE FROM outbox")
        except sqlite3.Error as exc:
            logger.debug("telemetry outbox clear failed: {}", exc)

    def _pending(self) -> Iterator[SpanRecord]:
        if self._db is None:
            return
        try:
            with self._lock:
                rows = self._db.execute("SELECT record FROM outbox ORDER BY seq").fetchall()
        except sqlite3.Error as exc:
            logger.warning("telemetry outbox unreadable, starting without replay: {}", exc)
            return
        for (raw,) in rows:
            try:
                yield _decode(raw)
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug("telemetry outbox row skipped: {}", exc)

    def close(self) -> None:
        """Release the store; unacknowledged rows stay for the next process."""
        if self._db is not None:
            self._db.close()
            self._db = None


def _encode(record: SpanRecord) -> str:
    data: dict[str, Any] = asdict(record)
    data["start_time"] = record.start_time.isoformat()
    data["end_time"] = record.end_time.isoformat()
    data["attributes"] = {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in record.attributes.items()
    }
    return json.dumps(data)


def _decode(raw: str) -> SpanRecord:
    data = json.loads(raw)
    return SpanRecord(
        trace_id=int(data["trace_id"]),
        span_id=int(data["span_id"]),
        parent_span_id=data["parent_span_id"],
        name=data["name"],
        kind=data["kind"],
        start_time=datetime.fromisoformat(data["start_time"]),
        end_time=datetime.fromisoformat(data["end_time"]),
        attributes={
            key: tuple(value) if isinstance(value, list) else value
            for key, value in data["attributes"].items()
        },
    )


__all__ = ["OutboxSink"]
