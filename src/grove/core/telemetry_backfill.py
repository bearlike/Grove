"""Durable, remote-reconciled checkpoints for historical telemetry export.

This module consumes immutable trace manifests. Transcript discovery and parsing
belong to the adapter-backed trace projector; the export boundary only decides
which already-built observations still need to be delivered.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

import httpx
from loguru import logger

from grove.core import paths
from grove.core.config import TelemetryConfig
from grove.core.trace import SpanRecord, SpanSink, TraceInstrumentor, TraceManifest

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS trace_exports_v2 (
    destination_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('submitted', 'completed')),
    submitted_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (destination_id, trace_id)
)
"""
_MAX_TRACE_ID = (1 << 128) - 1
_MAX_SPAN_ID = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class LedgerCheck:
    """Whether a trace is new, submitted, completed, or conflicts with history."""

    status: Literal["pending", "submitted", "completed", "mismatch"]
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


class TelemetryCheckpointLedger:
    """A durable record of immutable traces verified at one remote destination."""

    def __init__(
        self,
        path: Path,
        *,
        destination_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not destination_id:
            raise ValueError("destination_id must not be empty")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._destination_id = destination_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        with self._connection:
            self._connection.execute(_LEDGER_DDL)

    def check(self, trace_id: int, manifest_hash: str) -> LedgerCheck:
        """Read a checkpoint without changing a conflicting completed trace."""
        trace_hex = _trace_hex(trace_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT manifest_hash, state, submitted_at, completed_at FROM trace_exports_v2 "
                "WHERE destination_id = ? AND trace_id = ?",
                (self._destination_id, trace_hex),
            ).fetchone()
        return self._decode(row, manifest_hash)

    def recorded(self, trace_ids: frozenset[int]) -> frozenset[int]:
        """The subset of ``trace_ids`` with any checkpoint at this destination."""
        if not trace_ids:
            return frozenset()
        by_hex = {_trace_hex(trace_id): trace_id for trace_id in trace_ids}
        keys = list(by_hex)
        found: set[int] = set()
        with self._lock:
            # Chunked under SQLite's default bound-parameter limit.
            for start in range(0, len(keys), 500):
                chunk = keys[start : start + 500]
                rows = self._connection.execute(
                    "SELECT trace_id FROM trace_exports_v2 WHERE destination_id = ? "
                    f"AND trace_id IN ({','.join('?' * len(chunk))})",
                    (self._destination_id, *chunk),
                ).fetchall()
                found.update(by_hex[str(row[0])] for row in rows)
        return frozenset(found)

    def submit(self, trace_id: int, manifest_hash: str) -> LedgerCheck:
        """Record collector acceptance before remote indexing becomes visible."""
        return self._record(trace_id, manifest_hash, complete=False)

    def complete(self, trace_id: int, manifest_hash: str) -> LedgerCheck:
        """Record one verified trace, preserving any prior immutable hash."""
        return self._record(trace_id, manifest_hash, complete=True)

    def _record(self, trace_id: int, manifest_hash: str, *, complete: bool) -> LedgerCheck:
        trace_hex = _trace_hex(trace_id)
        now = self._clock().astimezone(UTC).isoformat()
        state = "completed" if complete else "submitted"
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO trace_exports_v2
                    (destination_id, trace_id, manifest_hash, state, submitted_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._destination_id,
                    trace_hex,
                    manifest_hash,
                    state,
                    now,
                    now if complete else None,
                ),
            )
            if complete:
                self._connection.execute(
                    "UPDATE trace_exports_v2 SET state='completed', completed_at=? "
                    "WHERE destination_id=? AND trace_id=? AND manifest_hash=?",
                    (now, self._destination_id, trace_hex, manifest_hash),
                )
            row = self._connection.execute(
                "SELECT manifest_hash, state, submitted_at, completed_at FROM trace_exports_v2 "
                "WHERE destination_id = ? AND trace_id = ?",
                (self._destination_id, trace_hex),
            ).fetchone()
        return self._decode(row, manifest_hash)

    @staticmethod
    def _decode(row: tuple[object, ...] | None, manifest_hash: str) -> LedgerCheck:
        if row is None:
            return LedgerCheck(status="pending")
        submitted_at = datetime.fromisoformat(str(row[2]))
        completed_at = datetime.fromisoformat(str(row[3])) if row[3] is not None else None
        if str(row[0]) != manifest_hash:
            return LedgerCheck(
                status="mismatch", submitted_at=submitted_at, completed_at=completed_at
            )
        state: Literal["submitted", "completed"] = (
            "completed" if str(row[1]) == "completed" else "submitted"
        )
        return LedgerCheck(status=state, submitted_at=submitted_at, completed_at=completed_at)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class LiveAcknowledgements:
    """The live replay's once-only gate, persisted in the backfill's own ledger.

    A live acknowledgement is recorded as ``submitted``: the collector accepted
    the turn, nothing has read it back. That is the same claim the backfill
    makes before verification, so the two paths share one row per trace and the
    backfill never re-sends a turn the live path already delivered. Never
    raises: a ledger that cannot be written costs a re-send after the next
    restart, which is the behaviour without it.
    """

    def __init__(self, ledger: TelemetryCheckpointLedger) -> None:
        self._ledger = ledger

    @classmethod
    def from_config(
        cls, cfg: TelemetryConfig, *, env: Mapping[str, str] | None = None
    ) -> LiveAcknowledgements | None:
        """``None`` when the destination cannot be named, so the gate stays in-memory."""
        destination_id = _destination_id(cfg.derive_env(env if env is not None else os.environ))
        if destination_id is None:
            return None
        try:
            ledger = TelemetryCheckpointLedger(
                paths.telemetry_ledger_path(), destination_id=destination_id
            )
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "telemetry ledger unavailable; live replay gate stays in-memory: {}", exc
            )
            return None
        return cls(ledger)

    def accepted(self, trace_ids: frozenset[int]) -> frozenset[int]:
        try:
            return self._ledger.recorded(trace_ids)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("telemetry ledger read failed: {}", exc)
            return frozenset()

    def accept(self, manifest: TraceManifest) -> None:
        try:
            self._ledger.submit(manifest.trace_id, manifest.fingerprint)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("telemetry ledger write failed: {}", exc)

    def close(self) -> None:
        self._ledger.close()


def _destination_id(derived: Mapping[str, str]) -> str | None:
    """The ledger key for this Langfuse host and project, or ``None`` without credentials.

    Shared by the probe and the live gate so both name one row per trace."""
    host = derived.get("LANGFUSE_HOST")
    public_key = derived.get("LANGFUSE_PUBLIC_KEY")
    if not (host and public_key and derived.get("LANGFUSE_SECRET_KEY")):
        return None
    return hashlib.sha256(f"langfuse-v4\0{host.rstrip('/')}\0{public_key}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TraceProbeResult:
    """A remote observation snapshot, or an explicit unavailable outcome."""

    status: Literal["available", "unavailable"]
    observation_ids: frozenset[str] = frozenset()
    detail: str | None = None


class TraceProbe(Protocol):
    def get(self, trace_id: int) -> TraceProbeResult:
        """Return the remote observation IDs for one deterministic trace."""


class LangfuseTraceProbe:
    """Read a trace's observation ids from Langfuse's v2 observations endpoint.

    The v1 ``/api/public/traces/{id}`` read answers 404 for EVERY trace on a v4
    deployment in events_only mode (measured on 4.48.0, 2026-10-02), and a 404
    reads as "nothing remote", so every reconciliation re-sent its whole trace.
    The v2 list is the read that works there; it pages by cursor.
    """

    PAGE_SIZE = 1000
    MAX_PAGES = 50

    def __init__(
        self,
        cfg: TelemetryConfig,
        *,
        env: Mapping[str, str] | None = None,
        repo_root: Path | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        derived = cfg.derive_env(env if env is not None else os.environ, repo_root=repo_root)
        host = derived.get("LANGFUSE_HOST")
        public_key = derived.get("LANGFUSE_PUBLIC_KEY")
        secret_key = derived.get("LANGFUSE_SECRET_KEY")
        self.destination_id = _destination_id(derived)
        self._http = (
            httpx.Client(
                base_url=host.rstrip("/"),
                auth=httpx.BasicAuth(public_key, secret_key),
                timeout=timeout_seconds,
                transport=transport,
            )
            if host and public_key and secret_key
            else None
        )

    def get(self, trace_id: int) -> TraceProbeResult:
        trace_hex = _trace_hex(trace_id)
        if self._http is None:
            return TraceProbeResult(
                status="unavailable", detail="Langfuse credentials are unavailable"
            )
        observation_ids: set[str] = set()
        params: dict[str, str | int] = {
            "traceId": trace_hex,
            "fields": "core",
            "limit": self.PAGE_SIZE,
        }
        for _ in range(self.MAX_PAGES):
            try:
                response = self._http.get("/api/public/v2/observations", params=params)
            except httpx.HTTPError:
                return TraceProbeResult(status="unavailable", detail="Langfuse trace probe failed")
            page = self._decode(response)
            if isinstance(page, TraceProbeResult):
                return page
            ids, cursor = page
            observation_ids.update(ids)
            if not cursor:
                return TraceProbeResult(
                    status="available", observation_ids=frozenset(observation_ids)
                )
            params["cursor"] = cursor
        # A partial read would understate what is remote and re-send the rest.
        return TraceProbeResult(
            status="unavailable", detail="Langfuse trace exceeds the page bound"
        )

    @staticmethod
    def _decode(response: httpx.Response) -> tuple[set[str], str | None] | TraceProbeResult:
        """One page's ids and next cursor, or the reason the remote state is unknown.

        Any non-200 is UNAVAILABLE, never "absent": an empty trace is a 200 with
        no rows, so a 404 here is a missing endpoint, and treating it as absence
        is the bug this probe used to have.
        """
        if response.status_code != 200:
            return TraceProbeResult(
                status="unavailable",
                detail=f"Langfuse trace probe returned HTTP {response.status_code}",
            )
        try:
            body = response.json()
        except ValueError:
            return TraceProbeResult(
                status="unavailable", detail="Langfuse trace response was not JSON"
            )
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            return TraceProbeResult(
                status="unavailable", detail="Langfuse trace response had an unexpected shape"
            )
        ids: set[str] = set()
        for observation in body["data"]:
            if not isinstance(observation, dict) or not isinstance(observation.get("id"), str):
                return TraceProbeResult(
                    status="unavailable", detail="Langfuse observations had an unexpected shape"
                )
            ids.add(observation["id"].lower())
        meta = body.get("meta")
        cursor = meta.get("cursor") if isinstance(meta, dict) else None
        return ids, cursor if isinstance(cursor, str) and cursor else None

    def close(self) -> None:
        if self._http is not None:
            self._http.close()


ExportStatus = Literal["completed", "submitted", "skipped", "degraded"]


@dataclass(frozen=True, slots=True)
class ManifestExportResult:
    """One manifest's isolated export outcome and reconciliation counters."""

    trace_id: str
    status: ExportStatus
    expected_spans: int
    remote_spans: int
    emitted_spans: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TelemetryBackfillResult:
    """A bounded batch result; details retain every per-manifest degradation."""

    manifests: tuple[ManifestExportResult, ...]
    deferred_manifests: int

    @property
    def completed(self) -> int:
        return sum(result.status == "completed" for result in self.manifests)

    @property
    def skipped(self) -> int:
        return sum(result.status == "skipped" for result in self.manifests)

    @property
    def submitted(self) -> int:
        return sum(result.status == "submitted" for result in self.manifests)

    @property
    def degraded(self) -> int:
        return sum(result.status == "degraded" for result in self.manifests)

    @property
    def emitted_spans(self) -> int:
        return sum(result.emitted_spans for result in self.manifests)


class TelemetryBackfillExporter:
    """Reconcile immutable manifests against checkpoints and remote state."""

    def __init__(
        self,
        *,
        ledger: TelemetryCheckpointLedger,
        probe: TraceProbe,
        sink: SpanSink,
        clock: Callable[[], datetime] | None = None,
        submission_grace: timedelta = timedelta(hours=24),
    ) -> None:
        self._ledger = ledger
        self._probe = probe
        self._sink = sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._submission_grace = submission_grace

    def export(self, manifests: Sequence[TraceManifest], *, limit: int) -> TelemetryBackfillResult:
        """Export at most ``limit`` manifests without one failure stopping the batch."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        selected = manifests[:limit]
        results = tuple(self._export_one(manifest) for manifest in selected)
        return TelemetryBackfillResult(
            manifests=results,
            deferred_manifests=max(0, len(manifests) - len(selected)),
        )

    def _export_one(self, manifest: TraceManifest) -> ManifestExportResult:
        try:
            trace_hex, expected = self._validate(manifest)
        except (AttributeError, TypeError, ValueError):
            return ManifestExportResult(
                trace_id=_display_trace_id(manifest),
                status="degraded",
                expected_spans=0,
                remote_spans=0,
                emitted_spans=0,
                detail="trace manifest was invalid",
            )

        try:
            checkpoint = self._ledger.check(manifest.trace_id, manifest.fingerprint)
        except (OSError, sqlite3.Error, ValueError):
            return self._degraded(trace_hex, len(expected), detail="checkpoint ledger unavailable")
        if checkpoint.status == "completed":
            return ManifestExportResult(
                trace_id=trace_hex,
                status="skipped",
                expected_spans=len(expected),
                remote_spans=len(expected),
                emitted_spans=0,
                detail="trace already has a completed checkpoint",
            )
        if checkpoint.status == "mismatch":
            return self._degraded(
                trace_hex,
                len(expected),
                detail="manifest hash differs from the completed checkpoint",
            )

        return self._export_pending(
            manifest, checkpoint=checkpoint, trace_hex=trace_hex, expected=expected
        )

    def _export_pending(
        self,
        manifest: TraceManifest,
        *,
        checkpoint: LedgerCheck,
        trace_hex: str,
        expected: frozenset[str],
    ) -> ManifestExportResult:
        before = self._probe_remote(manifest.trace_id)
        if before.status == "unavailable":
            return self._degraded(
                trace_hex,
                len(expected),
                detail=before.detail or "remote trace state unavailable",
            )

        missing = tuple(
            span for span in manifest.spans if _span_hex(span.span_id) not in before.observation_ids
        )
        if not missing:
            return self._record_complete(
                manifest,
                trace_hex=trace_hex,
                expected=len(expected),
                remote=len(before.observation_ids & expected),
                emitted=0,
            )
        if (
            checkpoint.status == "submitted"
            and checkpoint.submitted_at is not None
            and self._clock() - checkpoint.submitted_at < self._submission_grace
        ):
            return self._submitted(
                trace_hex,
                len(expected),
                remote=len(before.observation_ids & expected),
                detail="collector accepted trace; awaiting remote visibility",
            )

        return self._submit_missing(
            manifest,
            missing=missing,
            before=before,
            trace_hex=trace_hex,
            expected=expected,
        )

    def _submit_missing(
        self,
        manifest: TraceManifest,
        *,
        missing: Sequence[SpanRecord],
        before: TraceProbeResult,
        trace_hex: str,
        expected: frozenset[str],
    ) -> ManifestExportResult:
        """Send one reconciled remainder and durably record collector acceptance."""

        emitted = 0
        try:
            # One flush per chunk, for the reason `TraceInstrumentor.REPLAY_CHUNK`
            # gives: a remainder over the processor's bounds is otherwise refused
            # in part on every run and can never complete.
            for start in range(0, len(missing), TraceInstrumentor.REPLAY_CHUNK):
                for span in missing[start : start + TraceInstrumentor.REPLAY_CHUNK]:
                    self._sink(span)
                    emitted += 1
                self._sink.flush()
        except Exception:
            return self._degraded(
                trace_hex,
                len(expected),
                remote=len(before.observation_ids & expected),
                emitted=emitted,
                detail="telemetry export or flush failed",
            )

        try:
            submitted = self._ledger.submit(manifest.trace_id, manifest.fingerprint)
        except (OSError, sqlite3.Error, ValueError):
            return self._degraded(
                trace_hex,
                len(expected),
                remote=len(before.observation_ids & expected),
                emitted=emitted,
                detail="collector accepted trace but submission could not be recorded",
            )
        if submitted.status == "mismatch":
            return self._degraded(
                trace_hex,
                len(expected),
                remote=len(before.observation_ids & expected),
                emitted=emitted,
                detail="manifest hash conflicts with a concurrent submission",
            )

        return self._verify_submission(
            manifest,
            before=before,
            trace_hex=trace_hex,
            expected=expected,
            emitted=emitted,
        )

    def _verify_submission(
        self,
        manifest: TraceManifest,
        *,
        before: TraceProbeResult,
        trace_hex: str,
        expected: frozenset[str],
        emitted: int,
    ) -> ManifestExportResult:
        """Promote a submitted trace only after every observation is visible."""

        after = self._probe_remote(manifest.trace_id)
        if after.status == "unavailable":
            return self._submitted(
                trace_hex,
                len(expected),
                remote=len(before.observation_ids & expected),
                emitted=emitted,
                detail=after.detail or "collector accepted trace; remote verification unavailable",
            )
        absent = expected - after.observation_ids
        if absent:
            return self._submitted(
                trace_hex,
                len(expected),
                remote=len(after.observation_ids & expected),
                emitted=emitted,
                detail=f"collector accepted trace; awaiting {len(absent)} observations",
            )
        return self._record_complete(
            manifest,
            trace_hex=trace_hex,
            expected=len(expected),
            remote=len(after.observation_ids & expected),
            emitted=emitted,
        )

    def _probe_remote(self, trace_id: int) -> TraceProbeResult:
        try:
            return self._probe.get(trace_id)
        except Exception:
            return TraceProbeResult(
                status="unavailable", detail="remote trace probe failed unexpectedly"
            )

    @staticmethod
    def _validate(manifest: TraceManifest) -> tuple[str, frozenset[str]]:
        trace_hex = _trace_hex(manifest.trace_id)
        if not manifest.fingerprint:
            raise ValueError("manifest hash is empty")
        ids: list[str] = []
        for span in manifest.spans:
            if span.trace_id != manifest.trace_id:
                raise ValueError("span belongs to another trace")
            ids.append(_span_hex(span.span_id))
        expected = frozenset(ids)
        if len(expected) != len(ids):
            raise ValueError("manifest contains duplicate observation ids")
        return trace_hex, expected

    def _record_complete(
        self,
        manifest: TraceManifest,
        *,
        trace_hex: str,
        expected: int,
        remote: int,
        emitted: int,
    ) -> ManifestExportResult:
        try:
            recorded = self._ledger.complete(manifest.trace_id, manifest.fingerprint)
        except (OSError, sqlite3.Error, ValueError):
            return self._degraded(
                trace_hex,
                expected,
                remote=remote,
                emitted=emitted,
                detail="verified trace could not be checkpointed",
            )
        if recorded.status != "completed":
            return self._degraded(
                trace_hex,
                expected,
                remote=remote,
                emitted=emitted,
                detail="manifest hash conflicts with a concurrent checkpoint",
            )
        return ManifestExportResult(
            trace_id=trace_hex,
            status="completed",
            expected_spans=expected,
            remote_spans=remote,
            emitted_spans=emitted,
        )

    @staticmethod
    def _submitted(
        trace_hex: str,
        expected: int,
        *,
        remote: int = 0,
        emitted: int = 0,
        detail: str,
    ) -> ManifestExportResult:
        return ManifestExportResult(
            trace_id=trace_hex,
            status="submitted",
            expected_spans=expected,
            remote_spans=remote,
            emitted_spans=emitted,
            detail=detail,
        )

    @staticmethod
    def _degraded(
        trace_hex: str,
        expected: int,
        *,
        remote: int = 0,
        emitted: int = 0,
        detail: str,
    ) -> ManifestExportResult:
        return ManifestExportResult(
            trace_id=trace_hex,
            status="degraded",
            expected_spans=expected,
            remote_spans=remote,
            emitted_spans=emitted,
            detail=detail,
        )


def _trace_hex(trace_id: int) -> str:
    if (
        not isinstance(trace_id, int)
        or isinstance(trace_id, bool)
        or not 0 < trace_id <= _MAX_TRACE_ID
    ):
        raise ValueError("trace_id must be a non-zero 128-bit integer")
    return f"{trace_id:032x}"


def _span_hex(span_id: int) -> str:
    if not isinstance(span_id, int) or isinstance(span_id, bool) or not 0 < span_id <= _MAX_SPAN_ID:
        raise ValueError("span_id must be a non-zero 64-bit integer")
    return f"{span_id:016x}"


def _display_trace_id(manifest: TraceManifest) -> str:
    try:
        return _trace_hex(manifest.trace_id)
    except (AttributeError, TypeError, ValueError):
        return "invalid"


__all__ = [
    "LangfuseTraceProbe",
    "LedgerCheck",
    "LiveAcknowledgements",
    "ManifestExportResult",
    "TelemetryBackfillExporter",
    "TelemetryBackfillResult",
    "TelemetryCheckpointLedger",
    "TraceProbe",
    "TraceProbeResult",
]
