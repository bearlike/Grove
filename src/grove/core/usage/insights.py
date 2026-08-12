"""Deterministic, metadata-only findings over normalized usage evidence."""

# Detector SQL is intentionally local to each question.
# ruff: noqa: E501

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Final

from loguru import logger

from grove.core.agents.registry import all_adapters
from grove.core.config import GroveConfig
from grove.core.contracts.usage import (
    FindingKind,
    UsageBashCommandView,
    UsageBashInsightView,
    UsageCoverageView,
    UsageFilters,
    UsageFindingsView,
    UsageFindingView,
)
from grove.core.usage._command import SHELL_TOOL_NAMES
from grove.core.usage._store import UsageStore
from grove.core.usage.query import session_filter_sql


class InsightDetector:
    """One provider-neutral evidence question."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        raise NotImplementedError


class RecurringToolFailureDetector(InsightDetector):
    """Find one repeatedly failing tool inside one project/account scope."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = _event_scope(filters)
        rows = store.query(
            f"WITH scoped AS (SELECT e.*, s.provider, s.project, s.account_id FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND e.kind='tool_result' AND e.tool_name IS NOT NULL), failures AS (SELECT provider, project, account_id, tool_name subject, failure_category, COUNT(*) count, MIN(ts) first_seen, MAX(ts) last_seen, GROUP_CONCAT(DISTINCT session_id) session_ids FROM scoped WHERE is_error=1 GROUP BY provider, project, account_id, tool_name, failure_category), totals AS (SELECT provider, project, account_id, tool_name, COUNT(*) total_count, SUM(is_error) failed_count FROM scoped GROUP BY provider, project, account_id, tool_name) SELECT failures.*, totals.total_count, totals.failed_count FROM failures JOIN totals ON totals.provider=failures.provider AND totals.project IS failures.project AND totals.account_id IS failures.account_id AND totals.tool_name=failures.subject WHERE failures.count >= ? ORDER BY failures.count DESC",
            [*params, cfg.usage.insights.min_occurrences],
        )
        findings: list[UsageFindingView] = []
        for row in rows:
            total = int(row["total_count"])
            failed = int(row["failed_count"])
            success_rate = (total - failed) / total
            category = row["failure_category"] or "unknown"
            findings.append(
                _finding(
                    "recurring_tool_failure",
                    f"Recurring failures in {row['subject']}",
                    row,
                    filters,
                    subject=row["subject"],
                    detail=(
                        f"{failed} of {total} observed results failed "
                        f"({success_rate:.0%} success); category: {category}."
                    ),
                    impact=failed / total,
                    evidence_filters=_coordinate_filters(filters, row),
                )
            )
        return tuple(findings)


class RetryLoopDetector(InsightDetector):
    """Find repeated failures of one operation inside a bounded session window."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = _event_scope(filters)
        rows = store.query(
            f"SELECT s.provider, s.project, s.account_id, e.tool_name subject, e.session_id, COUNT(*) count, MIN(e.ts) first_seen, MAX(e.ts) last_seen, e.session_id session_ids FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND e.is_error=1 AND e.tool_name IS NOT NULL GROUP BY e.session_id, e.source_id, e.tool_name, e.target, e.failure_category HAVING COUNT(*) >= ? AND MAX(e.ts)-MIN(e.ts) <= ? ORDER BY count DESC",
            [
                *params,
                cfg.usage.insights.min_occurrences,
                cfg.usage.insights.retry_window_seconds,
            ],
        )
        return tuple(
            _finding(
                "retry_loop",
                f"Repeated failed {row['subject']} calls",
                row,
                filters,
                subject=row["subject"],
                impact=1.0,
                evidence_filters=_coordinate_filters(filters, row),
            )
            for row in rows
        )


class EditChurnDetector(InsightDetector):
    """Find repeated edits to one metadata-visible target in one session."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = _event_scope(filters)
        rows = store.query(
            f"SELECT s.provider, s.project, s.account_id, e.target subject, e.session_id, COUNT(*) count, MIN(e.ts) first_seen, MAX(e.ts) last_seen, e.session_id session_ids FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND e.kind='file_edit' AND e.target IS NOT NULL GROUP BY e.session_id, e.source_id, e.target HAVING COUNT(*) >= ? ORDER BY count DESC",
            [*params, cfg.usage.insights.edit_churn_edits],
        )
        return tuple(
            _finding(
                "edit_churn",
                f"Repeated edits to {row['subject']}",
                row,
                filters,
                subject=row["subject"],
                impact=1.0,
                evidence_filters=_coordinate_filters(filters, row),
            )
            for row in rows
        )


class SlowOperationDetector(InsightDetector):
    """Find operation cohorts whose measured high p95 crosses the configured limit."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = _event_scope(filters)
        rows = store.query(
            f"SELECT s.provider, s.project, s.account_id, e.kind, e.model, e.tool_name, e.duration_ms, e.ts, e.session_id FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id WHERE {where} AND e.kind IN ('generation','tool_result') AND e.duration_ms IS NOT NULL",
            params,
        )
        groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
        for row in rows:
            key = (
                row["provider"],
                row["project"],
                row["account_id"],
                row["kind"],
                row["tool_name"],
                row["model"],
            )
            groups[key].append(row)

        findings: list[UsageFindingView] = []
        for group in groups.values():
            if len(group) < cfg.usage.insights.min_occurrences:
                continue
            durations = [int(row["duration_ms"]) for row in group]
            p95 = _nearest_rank_percentile(durations, 95)
            threshold = cfg.usage.insights.slow_operation_ms
            if p95 < threshold:
                continue
            slow_rows = [row for row in group if int(row["duration_ms"]) >= threshold]
            first = group[0]
            tool_name = first["tool_name"]
            model = first["model"] if first["kind"] == "generation" else None
            subject = tool_name or model or first["kind"]
            operation = f"generation in {model}" if model else str(subject)
            findings.append(
                _finding_from_values(
                    "slow_operation",
                    f"Slow operation in {operation}",
                    count=len(slow_rows),
                    first_seen=_minimum(row["ts"] for row in slow_rows),
                    last_seen=_maximum(row["ts"] for row in slow_rows),
                    session_ids=(str(row["session_id"]) for row in slow_rows),
                    filters=filters,
                    subject=str(subject),
                    detail=f"Measured p95 was {p95} ms across {len(group)} operations.",
                    impact=min(1.0, p95 / threshold),
                    confidence=1.0,
                    evidence_filters=_coordinate_filters(filters, first, model=model),
                )
            )
        return tuple(findings)


class TokenStructureDetector(InsightDetector):
    """Find scopes where measured fresh/cache-write input exceeds cache reads."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = session_filter_sql(filters)
        rows = store.query(
            f"SELECT provider, project, account_id, project subject, COUNT(*) count, MIN(started_at) first_seen, MAX(last_event_at) last_seen, GROUP_CONCAT(session_id) session_ids, SUM(COALESCE(fresh_input,0)+COALESCE(cache_creation,0)) structured_input, SUM(cache_read) cache_reads FROM sessions WHERE {where} AND cache_read IS NOT NULL AND (fresh_input IS NOT NULL OR cache_creation IS NOT NULL) AND COALESCE(fresh_input,0)+COALESCE(cache_creation,0) > cache_read GROUP BY provider, project, account_id HAVING COUNT(*) >= ? ORDER BY count DESC",
            [*params, cfg.usage.insights.min_occurrences],
        )
        findings: list[UsageFindingView] = []
        for row in rows:
            structured_input = int(row["structured_input"])
            cache_reads = int(row["cache_reads"])
            share = structured_input / (structured_input + cache_reads)
            findings.append(
                _finding(
                    "token_structure",
                    f"Fresh or cache-write input dominates in {row['subject'] or 'unknown project'}",
                    row,
                    filters,
                    subject=row["subject"],
                    detail=(
                        f"Measured fresh/cache-write input was {share:.0%} of "
                        "the compared input tokens."
                    ),
                    impact=share,
                    confidence=1.0,
                    evidence_filters=_coordinate_filters(filters, row),
                )
            )
        return tuple(findings)


class ConcentrationDetector(InsightDetector):
    """Find dominant project, account, or single-model cohorts."""

    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        where, params = session_filter_sql(filters, alias="s")
        rows = store.query(
            f"SELECT s.provider, s.project, s.account_id, s.models, s.provider_total, s.active_ms, s.started_at, s.last_event_at, s.session_id FROM sessions s WHERE {where}",
            params,
        )
        findings: list[UsageFindingView] = []
        for dimension in ("project", "account", "model"):
            cohorts = self._cohorts(rows, dimension)
            metric = _complete_metric(cohorts)
            if metric is None:
                continue
            metric_name, amounts = metric
            total = sum(amounts)
            grouped: dict[str | None, list[tuple[Any, int]]] = defaultdict(list)
            for (subject, row), amount in zip(cohorts, amounts, strict=True):
                grouped[subject].append((row, amount))
            for subject, members in grouped.items():
                amount = sum(value for _, value in members)
                share = amount / total
                if share < cfg.usage.insights.concentration_share:
                    continue
                evidence = _coordinate_filters(
                    filters,
                    members[0][0],
                    model=subject if dimension == "model" else None,
                    project=subject if dimension == "project" else _UNSET,
                    account=subject if dimension == "account" else _UNSET,
                )
                findings.append(
                    _finding_from_values(
                        "concentration",
                        f"Usage is concentrated in {dimension} {subject or 'unknown'}",
                        count=len(members),
                        first_seen=_minimum(row["started_at"] for row, _ in members),
                        last_seen=_maximum(row["last_event_at"] for row, _ in members),
                        session_ids=(str(row["session_id"]) for row, _ in members),
                        filters=filters,
                        subject=subject,
                        detail=f"{share:.0%} of measured {metric_name} is in this {dimension}.",
                        impact=share,
                        confidence=1.0,
                        evidence_filters=evidence,
                    )
                )
        return tuple(findings)

    @staticmethod
    def _cohorts(rows: list[Any], dimension: str) -> list[tuple[str | None, Any]]:
        cohorts: list[tuple[str | None, Any]] = []
        for row in rows:
            if dimension == "project":
                cohorts.append((row["project"], row))
            elif dimension == "account":
                cohorts.append((row["account_id"], row))
            else:
                models = _models(row["models"])
                if len(models) == 1:
                    cohorts.append((models[0], row))
        return cohorts


_SHELL_NAMES: Final = tuple(sorted(SHELL_TOOL_NAMES))
"""Sorted so the SQL text and its bound parameters pair deterministically —
a frozenset's iteration order is not stable across processes."""


ERROR_REPORTING_PROVIDERS: Final = tuple(
    sorted(adapter.kind for adapter in all_adapters() if adapter.reports_tool_errors)
)
"""Which ``sessions.provider`` values can carry a tool error at all — ASKED of
the adapters rather than listed here.

Public because it is a claim about what this audit can measure, not an
implementation detail: it is what a reader (and a test) checks to see that the
set is DERIVED rather than hard-coded, and pinning that through a private name
would make the derivation an implicit contract a rename could silently void.

Whether a harness records a structural failure flag is a fact about the harness,
so the adapter owns it (``AgentAdapter.reports_tool_errors``) and this layer only
projects it onto its own provider vocabulary. A literal ``('claude_code',)``
would be exactly the policy-in-code the root guide forbids: correct on the day it
was written and silently wrong the day an adapter's capability changes or a new
one ships, with nothing to fail.

Adapter kind IS the provider string for every adapter that can qualify — the one
place ``projector._provider`` collapses a kind (mewbo → ``generic``) is an
adapter with no message spine, hence no tool result, hence never capable.
Computed once at import: these are class constants, fixed for the process."""


class BashCommandRanking:
    """Where the agent's shell time went, by the command that LED each call.

    Not a detector: it reports measured evidence rather than a judgement, so it
    is not gated by ``usage.insights.enabled`` and produces no
    ``UsageFindingView``. It shares this module because it answers the same
    question shape — *what does the evidence say about how time was spent* —
    over the same filtered scope.

    Two rules keep the ranking honest, and both are visible on the wire:

    * The population is calls with a MEASURED duration. A `tool_result` with no
      correlated call (or no timestamps) has no time to attribute, so counting
      it in ``calls`` while it contributes nothing to ``total_ms`` would make
      ``avg_ms`` disagree with ``total_ms / calls`` for no reader's benefit.
    * ``censored_calls`` and ``background_calls`` ride every row. A row that is
      mostly either is not a measurement of cost, and nothing else on the wire
      could tell a reader that.
    * ``error_reportable_calls`` is the same disclosure applied to the ERROR
      column's denominator. Only some harnesses record a structural failure flag
      (``AgentAdapter.reports_tool_errors``), so a row's calls and its
      *measurable* calls are different numbers whenever the scope mixes
      providers — and the mix varies per command, so dividing by ``calls``
      deflates each row by a different, invisible amount.
    """

    def __init__(self, *, store: UsageStore, cfg: GroveConfig) -> None:
        self._store, self._cfg = store, cfg

    def view(self, filters: UsageFilters) -> UsageBashInsightView:
        where, params = _event_scope(filters)
        ceiling = self._cfg.usage.commands.censored_at_ms
        placeholders = ",".join("?" for _ in _SHELL_NAMES)
        # A world where no adapter reports tool errors is expressible, and
        # `IN ()` is not portable SQL — so the whole predicate collapses to a
        # constant 0 rather than being papered over with a sentinel provider.
        reportable = (
            f"s.provider IN ({','.join('?' for _ in ERROR_REPORTING_PROVIDERS)})"
            if ERROR_REPORTING_PROVIDERS
            else "0"
        )
        rows = self._store.query(
            f"SELECT e.target executable, COUNT(*) calls, SUM(e.duration_ms) total_ms, "
            f"SUM(CASE WHEN ? > 0 AND e.duration_ms >= ? THEN 1 ELSE 0 END) censored_calls, "
            f"SUM(CASE WHEN json_extract(e.attrs_json, '$.background') = 1 THEN 1 ELSE 0 END) background_calls, "
            f"SUM(e.is_error) error_calls, "
            f"SUM(CASE WHEN {reportable} THEN 1 ELSE 0 END) error_reportable_calls "
            f"FROM usage_events e JOIN sessions s ON s.session_id=e.session_id AND s.source_id=e.source_id "
            f"WHERE {where} AND e.kind='tool_result' AND e.duration_ms IS NOT NULL "
            f"AND e.tool_name IN ({placeholders}) GROUP BY e.target",
            # Bound in SQL-TEXT order: the two `censored` placeholders, then the
            # reportable-provider list — both in the SELECT list — then the
            # scope's own parameters, then the WHERE clause's tool names.
            [ceiling, ceiling, *ERROR_REPORTING_PROVIDERS, *params, *_SHELL_NAMES],
        )
        commands: list[UsageBashCommandView] = []
        unattributed_calls = unattributed_ms = 0
        total_calls = total_ms = 0
        for row in rows:
            calls, duration = int(row["calls"]), int(row["total_ms"] or 0)
            total_calls += calls
            total_ms += duration
            executable = row["executable"]
            if not executable:
                unattributed_calls += calls
                unattributed_ms += duration
                continue
            commands.append(
                UsageBashCommandView(
                    executable=str(executable),
                    calls=calls,
                    total_ms=duration,
                    avg_ms=duration // calls if calls else 0,
                    censored_calls=int(row["censored_calls"] or 0),
                    background_calls=int(row["background_calls"] or 0),
                    error_calls=int(row["error_calls"] or 0),
                    error_reportable_calls=int(row["error_reportable_calls"] or 0),
                )
            )
        commands.sort(key=lambda command: (command.total_ms, command.calls), reverse=True)
        return UsageBashInsightView(
            commands=tuple(commands[: self._cfg.usage.max_breakdown_rows]),
            unattributed_calls=unattributed_calls,
            unattributed_ms=unattributed_ms,
            total_calls=total_calls,
            total_ms=total_ms,
        )


class InsightEngine:
    """Run independent detectors, isolating one malformed evidence query."""

    def __init__(
        self,
        *,
        store: UsageStore,
        cfg: GroveConfig,
        coverage: Callable[[], UsageCoverageView] | None = None,
        detectors: tuple[InsightDetector, ...] | None = None,
    ) -> None:
        self._store, self._cfg = store, cfg
        self._coverage = coverage or UsageCoverageView
        self._detectors = detectors or (
            RecurringToolFailureDetector(),
            RetryLoopDetector(),
            EditChurnDetector(),
            SlowOperationDetector(),
            TokenStructureDetector(),
            ConcentrationDetector(),
        )

    def findings(self, filters: UsageFilters) -> UsageFindingsView:
        if not self._cfg.usage.insights.enabled:
            return UsageFindingsView(coverage=self._coverage())
        findings: list[UsageFindingView] = []
        for detector in self._detectors:
            try:
                findings.extend(detector.detect(self._store, filters, self._cfg))
            except Exception as exc:
                logger.warning(
                    "usage insight {} degraded: {}",
                    type(detector).__name__,
                    type(exc).__name__,
                )
        findings.sort(key=_rank, reverse=True)
        # `total` is filled HERE, at the one site that holds the complete ranked
        # list, so an in-process reader (the TUI renders the top 5) and a page
        # sliced by the daemon report the same count rather than one of them
        # defaulting to a plausible 0.
        return UsageFindingsView(
            findings=tuple(findings), total=len(findings), coverage=self._coverage()
        )


class _Unset:
    pass


_UNSET = _Unset()


def _event_scope(filters: UsageFilters) -> tuple[str, list[Any]]:
    return session_filter_sql(filters, alias="s", timestamp_column="e.ts")


def _coordinate_filters(
    filters: UsageFilters,
    row: Any,
    *,
    model: str | None = None,
    project: str | None | _Unset = _UNSET,
    account: str | None | _Unset = _UNSET,
) -> UsageFilters:
    updates: dict[str, Any] = {}
    provider = _row_value(row, "provider")
    project_value = _row_value(row, "project") if isinstance(project, _Unset) else project
    account_value = _row_value(row, "account_id") if isinstance(account, _Unset) else account
    if provider is not None:
        updates["provider"] = provider
    if project_value is not None:
        updates["project"] = project_value
    if account_value is not None:
        updates["account"] = account_value
    if model is not None:
        updates["model"] = model
    return filters.model_copy(update=updates)


def _finding(
    kind: FindingKind,
    title: str,
    row: Any,
    filters: UsageFilters,
    *,
    subject: str | None = None,
    detail: str | None = None,
    impact: float = 1.0,
    confidence: float = 1.0,
    evidence_filters: UsageFilters | None = None,
) -> UsageFindingView:
    return _finding_from_values(
        kind,
        title,
        count=int(row["count"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        session_ids=_session_ids(row),
        filters=filters,
        subject=subject,
        detail=detail,
        impact=impact,
        confidence=confidence,
        evidence_filters=evidence_filters,
    )


def _finding_from_values(
    kind: FindingKind,
    title: str,
    *,
    count: int,
    first_seen: int | None,
    last_seen: int | None,
    session_ids: Iterable[str],
    filters: UsageFilters,
    subject: str | None,
    detail: str | None,
    impact: float,
    confidence: float,
    evidence_filters: UsageFilters | None = None,
) -> UsageFindingView:
    return UsageFindingView(
        kind=kind,
        title=title,
        detail=detail,
        count=count,
        impact=min(1.0, max(0.0, impact)),
        confidence=min(1.0, max(0.0, confidence)),
        first_seen_at=_dt(first_seen),
        last_seen_at=_dt(last_seen),
        evidence_filters=evidence_filters or filters,
        session_ids=tuple(dict.fromkeys(session_ids))[:10],
        subject=subject,
    )


def _complete_metric(
    cohorts: list[tuple[str | None, Any]],
) -> tuple[str, list[int]] | None:
    if not cohorts:
        return None
    tokens = [row["provider_total"] for _, row in cohorts]
    if all(value is not None for value in tokens) and sum(tokens) > 0:
        return "tokens", [int(value) for value in tokens]
    active = [row["active_ms"] for _, row in cohorts]
    if all(value is not None for value in active) and sum(active) > 0:
        return "active time", [int(value) for value in active]
    return None


def _nearest_rank_percentile(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[max(0, rank - 1)]


def _rank(finding: UsageFindingView) -> tuple[float, int]:
    return finding.impact * finding.count * finding.confidence, finding.count


def _models(raw: str | None) -> tuple[str, ...]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _session_ids(row: Any) -> tuple[str, ...]:
    raw_ids = _row_value(row, "session_ids")
    return tuple(value for value in str(raw_ids or "").split(",") if value)


def _row_value(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _minimum(values: Iterable[int | None]) -> int | None:
    measured = [value for value in values if value is not None]
    return min(measured) if measured else None


def _maximum(values: Iterable[int | None]) -> int | None:
    measured = [value for value in values if value is not None]
    return max(measured) if measured else None


def _dt(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None
