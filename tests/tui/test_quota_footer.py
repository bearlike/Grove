"""QuotaFooter chrome is visible only for real quota accounts."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from grove.core.contracts.usage import (
    BillingAccountView,
    QuotaStatus,
    SubscriptionTier,
    SubscriptionWindowView,
    UsageQuotasView,
)
from grove.tui.widgets.quota_footer import QuotaFooter


class _QuotaHost(App[None]):
    def __init__(self, quotas: UsageQuotasView) -> None:
        super().__init__()
        self._quotas = quotas

    def compose(self) -> ComposeResult:
        yield QuotaFooter()

    def on_mount(self) -> None:
        self.query_one(QuotaFooter).set_quotas(self._quotas)


def _account(
    label: str,
    *,
    used_percent: float | None = 25,
    status: QuotaStatus = "ok",
) -> BillingAccountView:
    return BillingAccountView(
        account_id=label,
        provider="claude_code",
        label=label,
        status=status,
        subscription=SubscriptionTier(label="Max", detail="20x"),
        windows=(
            SubscriptionWindowView(
                scope="session",
                label="session",
                used_percent=used_percent,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_empty_quota_footer_is_byte_identical_to_absence() -> None:
    app = _QuotaHost(UsageQuotasView())
    async with app.run_test(size=(140, 40)):
        footer = app.query_one(QuotaFooter)
        assert footer.display is False
        assert footer.content_size.height == 0
        assert str(footer.query_one("#quota-primary").render()) == ""
        assert str(footer.query_one("#quota-secondary").render()) == ""
        assert str(footer.query_one("#quota-divider").render()) == ""


@pytest.mark.asyncio
async def test_quota_footer_renders_two_columns_and_omitted_count() -> None:
    quotas = UsageQuotasView(
        accounts=(_account("personal"), _account("work", status="stale"), _account("backup"))
    )
    app = _QuotaHost(quotas)
    async with app.run_test(size=(140, 40)):
        footer = app.query_one(QuotaFooter)
        primary = str(footer.query_one("#quota-primary").render())
        secondary = str(footer.query_one("#quota-secondary").render())
        assert footer.display is True
        assert footer.content_size.height == 1
        assert "personal" in primary
        assert "Max 20x" in primary
        assert "session 25% used" in primary
        assert "work" in secondary
        assert "stale" in secondary
        assert "1 more account" in secondary
        assert "backup" not in primary + secondary


@pytest.mark.asyncio
async def test_quota_footer_never_turns_unmeasured_percentage_into_zero() -> None:
    app = _QuotaHost(UsageQuotasView(accounts=(_account("personal", used_percent=None),)))
    async with app.run_test(size=(140, 40)):
        rendered = str(app.query_one("#quota-primary").render())
        assert "not measured" in rendered
        assert "0%" not in rendered
