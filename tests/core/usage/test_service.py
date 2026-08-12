"""UsageService keeps persisted quota coverage aligned with config selection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from grove.core.config import GroveConfig
from grove.core.contracts.usage import BillingAccountView, SubscriptionWindowView, UsageFilters
from grove.core.registry import RepoRegistry
from grove.core.usage import UsageService
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
