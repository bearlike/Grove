"""One-shot subscription quota chrome for the workspace list.

The footer owns exactly two account slots. A host can select more quota accounts
than terminal chrome can carry, so the second slot names the omitted count rather
than letting a visual cap impersonate the complete account set.
"""

from __future__ import annotations

from typing import Final

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from grove.core.contracts.usage import BillingAccountView, UsageQuotasView
from grove.tui._status import chrome_color

_MAX_ACCOUNTS: Final = 2


class QuotaFooter(Horizontal):
    """One optional row of at most two quota-account summaries."""

    DEFAULT_CSS = """
    QuotaFooter {
        height: 1;
        dock: bottom;
        background: $background;
        color: $text;
        padding: 0 2;
    }
    QuotaFooter.-hidden {
        display: none;
    }
    QuotaFooter .quota-column {
        width: 1fr;
        height: 1;
    }
    QuotaFooter #quota-divider {
        width: 3;
        height: 1;
        text-align: center;
    }
    QuotaFooter #quota-secondary.-hidden,
    QuotaFooter #quota-divider.-hidden {
        display: none;
    }
    """

    def __init__(self) -> None:
        super().__init__(classes="-hidden")
        self._accounts: tuple[BillingAccountView, ...] = ()

    def compose(self) -> ComposeResult:
        yield Static(id="quota-primary", classes="quota-column")
        yield Static(id="quota-divider")
        yield Static(id="quota-secondary", classes="quota-column")

    def set_quotas(self, quotas: UsageQuotasView) -> None:
        """Show every selected account through two named slots at most."""
        self._accounts = quotas.accounts
        self.set_class(not self._accounts, "-hidden")
        if not self._accounts:
            self.query_one("#quota-primary", Static).update("")
            return
        dark = self.app.current_theme.dark
        primary = self.query_one("#quota-primary", Static)
        secondary = self.query_one("#quota-secondary", Static)
        divider = self.query_one("#quota-divider", Static)
        primary.update(_render_account(self._accounts[0], dark=dark))
        if len(self._accounts) < _MAX_ACCOUNTS:
            secondary.add_class("-hidden")
            divider.add_class("-hidden")
            return
        secondary.remove_class("-hidden")
        divider.remove_class("-hidden")
        secondary.update(_render_account_column(self._accounts[1:], dark=dark))
        divider.update(f"[{chrome_color('muted', dark=dark)}]│[/]")


def _render_account_column(accounts: tuple[BillingAccountView, ...], *, dark: bool) -> str:
    """Render one visible account plus an explicit count for omitted accounts."""
    rendered = _render_account(accounts[0], dark=dark)
    omitted = len(accounts) - 1
    if omitted:
        muted = chrome_color("muted", dark=dark)
        rendered += f" [{muted}]· {omitted} more account{'s' if omitted != 1 else ''}[/]"
    return rendered


def _render_account(account: BillingAccountView, *, dark: bool) -> str:
    """Render one account without treating an absent percentage as zero."""
    muted = chrome_color("muted", dark=dark)
    parts = [f"[bold]{account.label}[/]"]
    plan = _plan(account)
    if plan is not None:
        parts.append(f"[{muted}]{plan}[/]")
    windows = [
        _render_window(window.label, window.used_percent, window.remaining_percent)
        for window in account.windows
    ]
    parts.extend(windows or [f"[{muted}]not measured[/]"])
    if account.status == "stale":
        parts.append(f"[{muted}]stale[/]")
    elif account.status != "ok":
        parts.append(f"[{muted}]{account.status.replace('_', ' ')}[/]")
    return f" [{muted}]·[/] ".join(parts)


def _plan(account: BillingAccountView) -> str | None:
    """Provider-owned subscription wording, absent rather than inferred."""
    subscription = account.subscription
    if subscription is None:
        return None
    plan = subscription.label or subscription.plan
    if plan is None:
        return None
    return f"{plan} {subscription.detail}" if subscription.detail else plan


def _render_window(label: str, used_percent: float | None, remaining_percent: float | None) -> str:
    """Use provider-measured percentage values only; no absent-value arithmetic."""
    if used_percent is not None:
        return f"{label} {used_percent:.0f}% used"
    if remaining_percent is not None:
        return f"{label} {remaining_percent:.0f}% left"
    return f"{label} not measured"
