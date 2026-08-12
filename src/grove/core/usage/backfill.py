"""Explicit local projection plus optional historical telemetry export."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from loguru import logger

from grove.core import paths
from grove.core.agents import get_adapter
from grove.core.config import AgentKind, GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.telemetry_backfill import (
    LangfuseTraceProbe,
    TelemetryBackfillExporter,
    TelemetryBackfillResult,
    TelemetryCheckpointLedger,
)
from grove.core.trace import TraceInstrumentor, TraceManifest, build_span_sink, price_book_estimator
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore


@dataclass(slots=True, frozen=True)
class HistoricalSession:
    """A cache-backed address for one adapter-owned transcript session."""

    source_id: str
    provider: AgentKind
    profile_root: Path
    session_id: str
    cwd: Path
    last_event_at: datetime | None


@dataclass(slots=True, frozen=True)
class UsageBackfillReport:
    """Operator-facing totals; ordinary per-session degradation stays data."""

    selected_sessions: int = 0
    ownership_skips: int = 0
    planned_traces: int = 0
    planned_spans: int = 0
    completed_traces: int = 0
    submitted_traces: int = 0
    checkpoint_skips: int = 0
    degraded_traces: int = 0
    emitted_spans: int = 0
    deferred_traces: int = 0
    details: tuple[str, ...] = ()


@dataclass(slots=True)
class _Totals:
    selected_sessions: int = 0
    ownership_skips: int = 0
    planned_traces: int = 0
    planned_spans: int = 0
    completed_traces: int = 0
    submitted_traces: int = 0
    checkpoint_skips: int = 0
    degraded_traces: int = 0
    emitted_spans: int = 0
    deferred_traces: int = 0
    details: list[str] = field(default_factory=list)

    def report(self) -> UsageBackfillReport:
        return UsageBackfillReport(
            selected_sessions=self.selected_sessions,
            ownership_skips=self.ownership_skips,
            planned_traces=self.planned_traces,
            planned_spans=self.planned_spans,
            completed_traces=self.completed_traces,
            submitted_traces=self.submitted_traces,
            checkpoint_skips=self.checkpoint_skips,
            degraded_traces=self.degraded_traces,
            emitted_spans=self.emitted_spans,
            deferred_traces=self.deferred_traces,
            details=tuple(self.details),
        )


class UsageTelemetryBackfill:
    """Plan selected historical sessions and optionally export them safely."""

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        store: UsageStore,
        ledger_path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._ledger_path = ledger_path or paths.telemetry_ledger_path()
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self,
        *,
        dry_run: bool,
        limit: int,
        settled_for: timedelta = timedelta(hours=1),
    ) -> UsageBackfillReport:
        """Run a bounded explicit export; never raises for ordinary degradation."""
        totals = _Totals()
        sessions = self._sessions(settled_before=self._clock() - settled_for)
        totals.selected_sessions = len(sessions)
        if limit < 1:
            totals.details.append("trace limit must be at least one")
            return totals.report()

        sink = None if dry_run else build_span_sink(self._cfg.telemetry)
        if not dry_run and sink is None:
            totals.details.append("telemetry exporter is unavailable")
            return totals.report()
        instrumentor = TraceInstrumentor(
            self._cfg.telemetry,
            sink=sink,
            cost_estimator=price_book_estimator(PriceBook(self._cfg.usage.pricing)),
        )
        ledger = None
        probe = None
        exporter = None
        if not dry_run and sink is not None:
            probe = LangfuseTraceProbe(self._cfg.telemetry)
            if probe.destination_id is None:
                totals.details.append("Langfuse reconciliation credentials are unavailable")
                self._close_resources(probe=probe, ledger=None, sink=sink)
                return totals.report()
            ledger = TelemetryCheckpointLedger(
                self._ledger_path, destination_id=probe.destination_id, clock=self._clock
            )
            exporter = TelemetryBackfillExporter(
                ledger=ledger, probe=probe, sink=sink, clock=self._clock
            )

        try:
            self._run_sessions(
                sessions,
                instrumentor=instrumentor,
                exporter=exporter,
                dry_run=dry_run,
                limit=limit,
                totals=totals,
            )
        finally:
            self._close_resources(probe=probe, ledger=ledger, sink=sink)
        return totals.report()

    def _run_sessions(
        self,
        sessions: tuple[HistoricalSession, ...],
        *,
        instrumentor: TraceInstrumentor,
        exporter: TelemetryBackfillExporter | None,
        dry_run: bool,
        limit: int,
        totals: _Totals,
    ) -> int:
        remaining = limit
        for session in sessions:
            if self._cfg.telemetry.content_owner_for(session.provider) != "grove":
                totals.ownership_skips += 1
                continue
            manifests = self._plan(instrumentor, session)
            if manifests is None:
                totals.details.append(f"{session.provider} session planning failed")
                continue
            selected = manifests[:remaining]
            totals.deferred_traces += len(manifests) - len(selected)
            totals.planned_traces += len(manifests)
            totals.planned_spans += sum(len(manifest.spans) for manifest in manifests)
            remaining -= len(selected)
            if dry_run or exporter is None or not selected:
                continue
            self._accumulate(totals, exporter.export(selected, limit=len(selected)))
        return remaining

    @staticmethod
    def _close_resources(*, probe: object, ledger: object, sink: object) -> None:
        for resource in (probe, ledger):
            close = getattr(resource, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
        flush = getattr(sink, "flush", None)
        if flush is not None:
            with contextlib.suppress(Exception):
                flush()

    def _plan(
        self, instrumentor: TraceInstrumentor, session: HistoricalSession
    ) -> tuple[TraceManifest, ...] | None:
        try:
            adapter = get_adapter(session.provider)
            with WorkspaceManager.transcript_config_dir_scope(
                session.provider, str(session.profile_root)
            ):
                return instrumentor.plan(
                    session.cwd,
                    session.session_id,
                    adapter,
                    session_group_id=session.session_id,
                    content=self._cfg.telemetry.backfill.content,
                    source_id=session.source_id,
                )
        except Exception as exc:
            logger.debug(
                "telemetry backfill planning degraded for {}: {}",
                session.session_id,
                type(exc).__name__,
            )
            return None

    @staticmethod
    def _accumulate(totals: _Totals, result: TelemetryBackfillResult) -> None:
        totals.completed_traces += result.completed
        totals.submitted_traces += result.submitted
        totals.checkpoint_skips += result.skipped
        totals.degraded_traces += result.degraded
        totals.emitted_spans += result.emitted_spans
        totals.deferred_traces += result.deferred_manifests
        totals.details.extend(
            item.detail for item in result.manifests if item.detail and item.status == "degraded"
        )

    def _sessions(self, *, settled_before: datetime) -> tuple[HistoricalSession, ...]:
        selected = {
            (provider, str(Path(raw).expanduser().resolve()))
            for provider, roots in self._cfg.telemetry.backfill.profiles.items()
            for raw in roots
        }
        if not self._cfg.telemetry.backfill.enabled or not selected:
            return ()
        rows = self._store.query(
            "SELECT s.source_id, s.provider, s.session_id, s.cwd, s.last_event_at, src.root "
            "FROM sessions s JOIN sources src ON src.source_id=s.source_id "
            "WHERE s.cwd IS NOT NULL ORDER BY s.last_event_at, s.session_id"
        )
        cutoff = int(settled_before.timestamp())
        sessions: list[HistoricalSession] = []
        for row in rows:
            root = Path(row["root"]).expanduser().resolve()
            if (row["provider"], str(root)) not in selected:
                continue
            last = row["last_event_at"]
            if last is not None and int(last) > cutoff:
                continue
            sessions.append(
                HistoricalSession(
                    source_id=row["source_id"],
                    provider=cast("AgentKind", row["provider"]),
                    profile_root=root,
                    session_id=row["session_id"],
                    cwd=Path(row["cwd"]),
                    last_event_at=datetime.fromtimestamp(last, UTC) if last is not None else None,
                )
            )
        return tuple(sessions)


__all__ = ["HistoricalSession", "UsageBackfillReport", "UsageTelemetryBackfill"]
