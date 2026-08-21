"""``grove quota`` renders the existing quota wire view without a daemon.

The command is deliberately tested through a frozen ``UsageQuotasView`` rather
than a quota provider: collection, durable fallback and burn arithmetic belong
to the engine; this shell owns only JSON identity and the honest terminal
projection.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from grove.core.contracts.usage import (
    BillingAccountView,
    SubscriptionTier,
    SubscriptionWindowProjection,
    SubscriptionWindowView,
    UsageQuotasView,
)
from grove.tui.cli import app
from grove.tui.cli_quota import QuotaReport


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def quotas() -> UsageQuotasView:
    return UsageQuotasView(
        accounts=(
            BillingAccountView(
                account_id="claude-1",
                provider="claude_code",
                label="Primary",
                billing_mode="subscription",
                subscription=SubscriptionTier(plan="max", detail="20x"),
                windows=(
                    SubscriptionWindowView(
                        scope="session",
                        label="5h",
                        used_percent=42,
                        resets_at=datetime(2026, 8, 15, 4, 0, tzinfo=UTC),
                        projection=SubscriptionWindowProjection(verdict="unknown"),
                    ),
                    SubscriptionWindowView(
                        scope="weekly",
                        label="7d",
                        used_percent=None,
                        resets_at=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
                    ),
                ),
            ),
            BillingAccountView(
                account_id="codex-1",
                provider="codex",
                label="Secondary",
                billing_mode="subscription",
                subscription=SubscriptionTier(plan="prolite"),
                status="stale",
                stale_seconds=3600,
                last_error="rate_limited",
                windows=(
                    SubscriptionWindowView(
                        scope="weekly",
                        label="weekly",
                        used_percent=80,
                        resets_at=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
                        projection=SubscriptionWindowProjection(
                            verdict="tight", projected_percent=110
                        ),
                    ),
                ),
            ),
        )
    )


def _stub_read(monkeypatch: pytest.MonkeyPatch, view: UsageQuotasView) -> None:
    monkeypatch.setattr(QuotaReport, "read", classmethod(lambda cls: cls(view=view)))


def test_quota_json_is_the_exact_quota_wire_view(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, quotas: UsageQuotasView
) -> None:
    """No hand-built dict can silently drift away from ``GET /usage/quotas``."""
    _stub_read(monkeypatch, quotas)

    result = runner.invoke(app, ["quota", "--json"])

    assert result.exit_code == 0, result.output
    assert UsageQuotasView.model_validate_json(result.output) == quotas


def test_quota_groups_accounts_and_keeps_unknowns_honest(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, quotas: UsageQuotasView
) -> None:
    """A stale last-good reading remains visible; absent usage and an unknown
    burn forecast get their named states rather than bars at a fabricated zero."""
    _stub_read(monkeypatch, quotas)

    result = runner.invoke(app, ["quota"])

    assert result.exit_code == 0, result.output
    assert "claude_code" in result.output
    assert "codex" in result.output
    assert "Primary" in result.output
    assert "Secondary" in result.output
    assert "42% used" in result.output
    assert "not measured" in result.output
    assert "burn unknown" in result.output
    assert "stale" in result.output
    assert "last refresh: rate_limited" in result.output
    assert "tight (110% projected)" in result.output


def test_quota_omits_an_absent_plan(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    view = UsageQuotasView(
        accounts=(
            BillingAccountView(
                account_id="account-1",
                provider="codex",
                label="Codex",
                billing_mode="subscription",
                subscription=SubscriptionTier(),
            ),
        )
    )
    _stub_read(monkeypatch, view)

    result = runner.invoke(app, ["quota"])

    assert result.exit_code == 0, result.output
    assert "plan" not in result.output
    assert "no quota windows measured" in result.output
