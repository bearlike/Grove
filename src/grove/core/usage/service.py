"""Synchronous public entrypoint for historical usage reads and refreshes."""

# ruff: noqa: E501, F405, UP035

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from grove.core import paths
from grove.core.config import GroveConfig
from grove.core.contracts.usage import *  # noqa: F403
from grove.core.registry import RepoRegistry
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore
from grove.core.usage.insights import BashCommandRanking, InsightEngine
from grove.core.usage.projector import UsageProjector
from grove.core.usage.query import UsageQuery
from grove.core.usage.quota import QuotaCollector
from grove.core.usage.series import UsageSeriesQuery

if TYPE_CHECKING:
    from grove.core.usage.backfill import UsageBackfillReport


class UsageService:
    """Blocking, best-effort facade consumed by daemon, web and TUI."""

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        registry: RepoRegistry,
        db_path: Path | None = None,
        quota: QuotaCollector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg, self._clock = cfg, clock or (lambda: datetime.now(UTC))
        self._store = UsageStore(
            db_path or paths.usage_db_path(), busy_timeout_ms=cfg.usage.busy_timeout_ms
        )
        self._projector = UsageProjector(
            cfg=cfg, registry=registry, store=self._store, clock=self._clock
        )
        self._quota = quota or QuotaCollector(cfg=cfg, clock=self._clock)
        self._prune_deselected_quota_snapshots()
        prices = PriceBook(cfg.usage.pricing)
        self._query = UsageQuery(store=self._store, prices=prices, cfg=cfg)
        self._series = UsageSeriesQuery(
            store=self._store, prices=prices, cfg=cfg, coverage=self._query.coverage
        )
        self._insights = InsightEngine(store=self._store, cfg=cfg, coverage=self._query.coverage)
        self._bash = BashCommandRanking(store=self._store, cfg=cfg)

    def summary(self, filters: UsageFilters) -> UsageSummaryView:
        return self._query.summary(filters)

    def activity(self, filters: UsageFilters, *, metric: UsageMetric) -> UsageActivityView:
        return self._query.activity(filters, metric=metric)

    def sessions(
        self, filters: UsageFilters, *, cursor: str | None, limit: int, sort: UsageSessionSort
    ) -> UsageSessionPageView:
        return self._query.sessions(filters, cursor=cursor, limit=limit, sort=sort)

    def breakdown(self, filters: UsageFilters, *, dimension: UsageDimension) -> UsageBreakdownView:
        return self._query.breakdown(filters, dimension=dimension)

    def series(
        self, filters: UsageFilters, *, dimension: UsageDimension, metric: UsageMetric
    ) -> UsageSeriesView:
        return self._series.series(filters, dimension=dimension, metric=metric)

    def quotas(self) -> UsageQuotasView:
        accounts = self._quota.snapshot()
        self._store_quota_snapshots(accounts)
        return self._query.quotas(accounts)

    def findings(self, filters: UsageFilters) -> UsageFindingsView:
        return self._insights.findings(filters)

    def bash_commands(self, filters: UsageFilters) -> UsageBashInsightView:
        return self._bash.view(filters)

    def refresh(self, *, force: bool) -> UsageRefreshView:
        started = time.monotonic()
        projected = self._projector.refresh(force=force)
        self._query.refresh_cost_cache()
        accounts = self._quota.refresh()
        self._store_quota_snapshots(accounts)
        coverage = self._query.coverage().model_copy(
            update={
                "quota_available": any(account.status in {"ok", "stale"} for account in accounts)
            }
        )
        return UsageRefreshView(
            indexed_sources=projected.indexed_sources,
            changed_sources=projected.changed_sources,
            quota_accounts=len(accounts),
            quota_failures=sum(a.status not in {"ok", "stale"} for a in accounts),
            duration_ms=int((time.monotonic() - started) * 1000),
            coverage=coverage,
        )

    def telemetry_backfill(
        self,
        *,
        dry_run: bool,
        limit: int,
        settled_for: timedelta = timedelta(hours=1),
    ) -> UsageBackfillReport:
        """Plan/export explicitly selected historical telemetry profiles."""
        from grove.core.usage.backfill import UsageTelemetryBackfill  # noqa: PLC0415

        return UsageTelemetryBackfill(
            cfg=self._cfg,
            store=self._store,
            clock=self._clock,
        ).run(dry_run=dry_run, limit=limit, settled_for=settled_for)

    def close(self) -> None:
        self._quota.close()
        self._store.close()

    def _store_quota_snapshots(self, accounts: tuple[BillingAccountView, ...]) -> None:
        with self._store.write() as conn:
            # This table is the latest selected quota set, not history. Clearing
            # first removes profiles deselected in config; leaving their rows
            # behind would make coverage claim quota is still available.
            conn.execute("DELETE FROM quota_snapshots")
            for account in accounts:
                conn.execute(
                    "INSERT INTO accounts(account_id, provider, label, billing_mode) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(account_id) DO UPDATE SET provider=excluded.provider, label=excluded.label, billing_mode=excluded.billing_mode",
                    (
                        account.account_id,
                        account.provider,
                        account.label,
                        account.billing_mode,
                    ),
                )
                conn.executemany(
                    "INSERT INTO quota_snapshots(account_id, scope, label, window_seconds, used_percent, remaining_percent, resets_at, limit_value, used_value, unit, observed_at, evidence, status, detail) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            account.account_id,
                            window.scope,
                            window.label,
                            window.window_seconds,
                            window.used_percent,
                            window.remaining_percent,
                            int(window.resets_at.timestamp()) if window.resets_at else None,
                            window.limit,
                            window.used,
                            window.unit,
                            int(
                                (
                                    window.observed_at or account.observed_at or self._clock()
                                ).timestamp()
                            ),
                            window.evidence,
                            account.status,
                            account.detail,
                        )
                        for window in account.windows
                    ],
                )

    def _prune_deselected_quota_snapshots(self) -> None:
        """Make persisted coverage obey current opt-in selection at startup."""
        selected = tuple(account.account_id for account in self._quota.accounts())
        with self._store.write() as conn:
            if not selected:
                conn.execute("DELETE FROM quota_snapshots")
                return
            placeholders = ",".join("?" for _ in selected)
            conn.execute(
                f"DELETE FROM quota_snapshots WHERE account_id NOT IN ({placeholders})",
                selected,
            )
