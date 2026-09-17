"""UsageService keeps persisted quota coverage aligned with config selection."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from grove.core.config import GroveConfig, UsagePricingConfig
from grove.core.contracts.usage import BillingAccountView, SubscriptionWindowView, UsageFilters
from grove.core.registry import RepoRegistry
from grove.core.usage import UsageService
from grove.core.usage.pricing_sources import PricingCatalog
from grove.core.usage.quota import QuotaCollector


class _Quota:
    def __init__(
        self,
        snapshots: list[tuple[BillingAccountView, ...]],
        *,
        accounts: tuple[BillingAccountView, ...] = (),
    ) -> None:
        self._snapshots = iter(snapshots)
        self._accounts = accounts

    def accounts(self) -> tuple[BillingAccountView, ...]:
        return self._accounts

    def snapshot(self) -> tuple[BillingAccountView, ...]:
        return next(self._snapshots)

    def refresh(self) -> tuple[BillingAccountView, ...]:
        return self.snapshot()

    def close(self) -> None:
        pass


def test_deselecting_all_profiles_prunes_persisted_quota_coverage(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 9, tzinfo=UTC)
    account = BillingAccountView(
        account_id="claude-selected",
        provider="claude_code",
        label="Selected Claude",
        billing_mode="subscription",
        status="ok",
        observed_at=observed,
        windows=(
            SubscriptionWindowView(
                scope="weekly",
                label="weekly",
                used_percent=25,
                remaining_percent=75,
                observed_at=observed,
            ),
        ),
    )
    quota = _Quota([(account,), ()])
    service = UsageService(
        cfg=GroveConfig(),
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=tmp_path / "usage.sqlite3",
        quota=cast(QuotaCollector, cast(Any, quota)),
    )
    try:
        assert service.quotas().coverage.quota_available is True
        assert service.quotas().coverage.quota_available is False
    finally:
        service.close()


@pytest.mark.parametrize("external", [False, True])
def test_unchanged_quota_reads_do_not_write_but_foreign_changes_are_repaired(
    tmp_path: Path, external: bool
) -> None:
    observed = datetime(2026, 9, 16, tzinfo=UTC)
    account = BillingAccountView(
        account_id="selected",
        provider="claude_code",
        label="Selected",
        billing_mode="subscription",
        status="ok",
        observed_at=observed,
        windows=(
            SubscriptionWindowView(
                scope="weekly", label="Weekly", used_percent=25, observed_at=observed
            ),
        ),
    )
    db_path = tmp_path / "usage.sqlite3"
    service = UsageService(
        cfg=GroveConfig(),
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=db_path,
        quota=cast(QuotaCollector, cast(Any, _Quota([(account,)] * 22))),
    )
    try:
        assert service.quotas().coverage.quota_available is True
        conn = service._store.connect()
        changes = conn.total_changes
        for _ in range(20):
            assert service.quotas().coverage.quota_available is True
        assert conn.total_changes == changes
        writer = sqlite3.connect(db_path) if external else conn
        try:
            writer.execute("DELETE FROM quota_snapshots")
            writer.commit()
        finally:
            if external:
                writer.close()
        assert service.quotas().coverage.quota_available is True
        assert conn.total_changes > changes
    finally:
        service.close()


def test_startup_prunes_snapshots_for_profiles_no_longer_selected(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 9, tzinfo=UTC)
    account = BillingAccountView(
        account_id="claude-selected",
        provider="claude_code",
        label="Selected Claude",
        billing_mode="subscription",
        status="ok",
        observed_at=observed,
        windows=(
            SubscriptionWindowView(
                scope="weekly",
                label="weekly",
                used_percent=25,
                remaining_percent=75,
                observed_at=observed,
            ),
        ),
    )
    db_path = tmp_path / "usage.sqlite3"
    writer = UsageService(
        cfg=GroveConfig(),
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=db_path,
        quota=cast(QuotaCollector, cast(Any, _Quota([(account,)]))),
    )
    try:
        assert writer.quotas().coverage.quota_available is True
    finally:
        writer.close()

    reader = UsageService(
        cfg=GroveConfig(),
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=db_path,
        quota=cast(QuotaCollector, cast(Any, _Quota([]))),
    )
    try:
        assert reader.summary(UsageFilters()).coverage.quota_available is False
    finally:
        reader.close()


def test_configured_busy_timeout_reaches_the_live_connection(tmp_path: Path) -> None:
    """``UsageConfig.busy_timeout_ms`` must reach the store it constructs —
    the config field alone changes nothing if the PRAGMA never sees it."""
    cfg = GroveConfig.model_validate({"usage": {"busy_timeout_ms": 9_000}})
    service = UsageService(
        cfg=cfg,
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=tmp_path / "usage.sqlite3",
        quota=cast(QuotaCollector, cast(Any, _Quota([]))),
    )
    try:
        conn = service._store.connect()  # no public accessor; wiring-only check
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 9_000
    finally:
        service.close()


def test_refresh_reprices_unchanged_rows_and_all_consumers_share_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = UsagePricingConfig.model_validate({"models": {"priced": {"input": 1}}})
    refreshed = UsagePricingConfig.model_validate({"models": {"priced": {"input": 3}}})
    calls: list[str] = []

    def load(_catalog: PricingCatalog) -> UsagePricingConfig:
        calls.append("load")
        return loaded

    def refresh(_catalog: PricingCatalog) -> UsagePricingConfig:
        calls.append("refresh")
        return refreshed

    monkeypatch.setattr(PricingCatalog, "load", load)
    monkeypatch.setattr(PricingCatalog, "refresh", refresh)
    service = UsageService(
        cfg=GroveConfig.model_validate({"usage": {"enabled": False}}),
        registry=cast(RepoRegistry, cast(Any, object())),
        db_path=tmp_path / "usage.sqlite3",
        quota=cast(QuotaCollector, cast(Any, _Quota([()]))),
    )
    try:
        with service._store.write() as conn:
            conn.execute(
                "INSERT INTO sources(source_id,provider,root,label,health) "
                "VALUES('source','claude_code','profile','profile','ok')"
            )
            conn.execute(
                "INSERT INTO sessions(session_id,source_id,provider,models,fresh_input,"
                "cache_read,cache_creation,output) VALUES('session','source','claude_code',"
                "'[\"priced\"]',1000000,0,0,0)"
            )
        before = service.summary(UsageFilters())
        assert before.cost is not None and before.cost.amount == "1.000000"
        assert calls == ["load"]
        assert service._projector._prices is service._query.prices is service._series._prices
        service.refresh(force=False)
        after = service.summary(UsageFilters())
        assert after.cost is not None and after.cost.amount == "3.000000"
        assert service._store.scalar("SELECT cost_amount FROM sessions") == "3.000000"
        assert calls == ["load", "refresh"]
    finally:
        service.close()
