"""What a batch send asked of whom, and what Grove observed, surviving a restart.

A batch (one message to a frozen recipient list, ``POST /mailboxes/batch``) is
idempotent on ``request_id``. An in-memory map alone would forget across a
restart, so a retried id would SEND AGAIN; this journal is what remembers.

**The journal is the idempotency record, so it is durable state, not a cache.**
Nothing here can be recomputed from anywhere else: a delivered message leaves
no trace Grove can read back. Bumping :data:`SCHEMA_VERSION` therefore
migrates and never drops the file, ``workspace_history``'s rule.

**Two writes, in a fixed order, and the gap between them is a fact.** The
REQUEST row is written before anything is dispatched; one RECEIPT row per
recipient is appended after that recipient's delivery returns. A crash in
between leaves a recipient with a request and no receipt, which reads as
``unknown`` — never as permission to deliver again, because the bytes may
well have reached the pane.

**Append-only through this API.** Attribution (sender, payload, recipients) is
fixed when the request is recorded; a receipt is recorded at most once per
recipient and the first one stands.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from loguru import logger

from grove.core import paths
from grove.core.contracts.fleet import DelegationKind
from grove.core.contracts.mailboxes import MailboxAddress, MailboxReceipt

SCHEMA_VERSION: Final = 1
"""Bumping this MIGRATES; it must never drop the file (the rows are the replay record)."""

_REQUESTS: Final = """
CREATE TABLE IF NOT EXISTS delegation_requests (
    request_id       TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    sender_workspace TEXT NOT NULL,
    sender_agent     TEXT NOT NULL DEFAULT '',
    digest           TEXT NOT NULL,
    subject          TEXT NOT NULL,
    body             TEXT NOT NULL,
    recipients       TEXT NOT NULL,
    created_us       INTEGER NOT NULL
);
"""
"""One row per request, written BEFORE dispatch. ``recipients`` is the frozen
list as JSON, in request order: replay answers in that order.

``created_us`` is integer microseconds, not a whole second: two requests sent
inside one second are ordinary."""

_RECEIPTS: Final = """
CREATE TABLE IF NOT EXISTS delegation_receipts (
    request_id          TEXT NOT NULL,
    recipient_workspace TEXT NOT NULL,
    recipient_agent     TEXT NOT NULL DEFAULT '',
    stage               TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    detail              TEXT NOT NULL DEFAULT '',
    message_id          TEXT NOT NULL DEFAULT '',
    recorded_us         INTEGER NOT NULL,
    PRIMARY KEY (request_id, recipient_workspace, recipient_agent)
);
"""
"""One row per recipient, appended AFTER its delivery returns.

Every key column is NOT NULL with an empty default: ``recipient_agent`` is
``''`` for a workspace's primary agent, the common case, and SQLite treats
NULLs in a primary key as DISTINCT — a nullable column there would let one
recipient collect two receipts. ``INSERT OR IGNORE`` keeps the first."""

_META: Final = """
CREATE TABLE IF NOT EXISTS delegation_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_INDEXES: Final = (
    "CREATE INDEX IF NOT EXISTS ix_delegation_sender "
    "ON delegation_requests(sender_workspace, created_us)",
)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO: Final = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Delegation:
    """One recorded request and the receipts recorded for it so far.

    ``receipts`` holds only what was RECORDED, in request order; a recipient
    missing from it was never confirmed either way. Filling that gap is the
    reader's decision, because "still delivering" and "the daemon stopped"
    look identical here.
    """

    request_id: str
    kind: DelegationKind
    sender: MailboxAddress
    digest: str
    subject: str
    body: str
    recipients: tuple[MailboxAddress, ...]
    created_at: datetime
    receipts: tuple[MailboxReceipt, ...]


class DelegationJournal:
    """Durable batch requests and per-recipient receipts: one SQLite file, one lock.

    The connection discipline is ``WorkspaceHistoryStore``'s (WAL, a busy
    timeout, one connection under one lock) because every call arrives through
    ``asyncio.to_thread``, several at once.
    """

    def __init__(self, path: Path | None = None, *, busy_timeout_ms: int = 5000) -> None:
        self._path = path if path is not None else paths.delegations_path()
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def digest(payload: Mapping[str, Any]) -> str:
        """A payload's identity: canonical JSON, sha256."""
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── writes ────────────────────────────────────────────────────────────

    def record_request(
        self,
        *,
        request_id: str,
        kind: DelegationKind,
        sender: MailboxAddress,
        digest: str,
        subject: str,
        body: str,
        recipients: Sequence[MailboxAddress],
        created_at: datetime,
    ) -> None:
        """Record a request before it is dispatched. RAISES on failure.

        Raising is the point: a batch whose request cannot be recorded cannot
        promise idempotency, so the caller must not send it. A plain INSERT, so
        an id that is already recorded is an ``IntegrityError`` rather than a
        silent rewrite of someone else's attribution.
        """
        recipients_json = json.dumps([address.model_dump(mode="json") for address in recipients])
        with self._write() as conn:
            conn.execute(
                "INSERT INTO delegation_requests(request_id, kind, sender_workspace, "
                "sender_agent, digest, subject, body, recipients, created_us) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    kind,
                    sender.workspace_id,
                    sender.agent,
                    digest,
                    subject,
                    body,
                    recipients_json,
                    self._us(created_at),
                ),
            )

    def record_receipt(self, request_id: str, receipt: MailboxReceipt) -> None:
        """Append one recipient's receipt after its delivery. Never raises.

        Best-effort because the delivery already happened: failing here must
        not turn a delivered message into an error. A lost row reads as
        ``unknown`` later, which is the honest answer for it anyway.
        """
        recipient = receipt.recipient
        if recipient is None:
            return
        try:
            with self._write() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO delegation_receipts(request_id, recipient_workspace, "
                    "recipient_agent, stage, reason, detail, message_id, recorded_us) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        recipient.workspace_id,
                        recipient.agent,
                        receipt.stage,
                        receipt.reason or "",
                        receipt.detail or "",
                        receipt.message_id or "",
                        self._us(receipt.created_at),
                    ),
                )
        except sqlite3.Error as exc:
            logger.warning("delegation receipt for {} not recorded: {}", request_id, exc)

    # ── reads ─────────────────────────────────────────────────────────────

    def lookup(self, request_id: str) -> Delegation | None:
        """One recorded request with its receipts, or ``None``. Raises on an unreadable store."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM delegation_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                return None
            return self._delegation(conn, row)

    # ── internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _us(value: datetime) -> int:
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return (aware - _EPOCH) // _MICRO

    @staticmethod
    def _at(us: int) -> datetime:
        return _EPOCH + timedelta(microseconds=us)

    def _delegation(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Delegation:
        sender = MailboxAddress(workspace_id=row["sender_workspace"], agent=row["sender_agent"])
        recipients = tuple(
            MailboxAddress.model_validate(item) for item in json.loads(row["recipients"])
        )
        recorded = {
            (r["recipient_workspace"], r["recipient_agent"]): r
            for r in conn.execute(
                "SELECT * FROM delegation_receipts WHERE request_id = ?", (row["request_id"],)
            ).fetchall()
        }
        receipts = tuple(
            MailboxReceipt(
                message_id=hit["message_id"] or None,
                sender=sender,
                recipient=recipient,
                stage=hit["stage"],
                reason=hit["reason"] or None,
                detail=hit["detail"] or None,
                created_at=self._at(hit["recorded_us"]),
            )
            for recipient in recipients
            if (hit := recorded.get((recipient.workspace_id, recipient.agent))) is not None
        )
        return Delegation(
            request_id=row["request_id"],
            kind=cast(DelegationKind, row["kind"]),
            sender=sender,
            digest=row["digest"],
            subject=row["subject"],
            body=row["body"],
            recipients=recipients,
            created_at=self._at(row["created_us"]),
            receipts=receipts,
        )

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Commit on a clean exit, roll back on any raise, all under the lock."""
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            paths.ensure_dir(self._path.parent)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)}")
            for statement in (_REQUESTS, _RECEIPTS, _META, *_INDEXES):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO delegation_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
            self._conn = conn
        return self._conn


__all__ = ["Delegation", "DelegationJournal"]
