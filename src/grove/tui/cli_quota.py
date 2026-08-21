"""``grove quota`` — subscription posture in a terminal-friendly grouped view.

The command calls :class:`grove.core.usage.UsageService` in-process rather than
making an HTTP request to the daemon. It is a local read over the same engine
seam as ``GET /usage/quotas`` and every workspace verb: this keeps the CLI
usable without a daemon and preserves the quota collector's shared durable
probe budget, rather than creating a second transport-only dependency.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC

import humanize
import typer
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.text import Text

from grove.core import WorkspaceStatus, load_config
from grove.core.contracts.usage import (
    BillingAccountView,
    SubscriptionWindowProjection,
    SubscriptionWindowView,
    UsageQuotasView,
)
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.tui._status import chrome_color, status_color


@dataclass(frozen=True, slots=True)
class QuotaReport:
    """One quota snapshot plus its CLI renderings.

    Keeping service construction and every output decision here leaves the
    Typer command as the thin read/emit shell. The renderer consumes only the
    frozen wire view, so it cannot invent a plan, percentage, token count, or
    burn forecast the engine did not measure.
    """

    view: UsageQuotasView

    @classmethod
    def read(cls) -> QuotaReport:
        """Read selected quota profiles through the host-wide usage service."""
        cfg = load_config(repo_root=None)
        registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
        # Deferred: this module is imported by every `grove` invocation, and the
        # usage stack pulls in `bashlex` (~98 ms measured) that only the usage
        # and quota commands need. A shell completion pays it on every TAB.
        from grove.core.usage import UsageService  # noqa: PLC0415

        service = UsageService(cfg=cfg, registry=registry)
        try:
            return cls(view=service.quotas())
        finally:
            service.close()

    def json(self) -> str:
        """The daemon's exact Pydantic payload, without a hand-maintained dict."""
        return self.view.model_dump_json(indent=2)

    def render(self) -> RenderableType:
        """Group account windows by provider without turning unknown into zero."""
        if not self.view.accounts:
            return Text("No quota profiles selected in usage.quota.profiles.")

        providers: dict[str, list[BillingAccountView]] = defaultdict(list)
        for account in self.view.accounts:
            providers[account.provider].append(account)

        sections: list[RenderableType] = []
        for provider, accounts in providers.items():
            sections.append(Text(provider, style=f"bold {chrome_color('accent')}"))
            sections.extend(self._account(account) for account in accounts)
        return Group(*sections)

    def _account(self, account: BillingAccountView) -> Panel:
        details: list[RenderableType] = [self._account_details(account)]
        status = self._account_status(account)
        if status is not None:
            details.append(status)
        if account.windows:
            details.extend(self._window(window) for window in account.windows)
        elif account.spend is not None:
            details.append(
                Text(
                    f"spend: {account.spend.amount} {account.spend.currency} "
                    f"({account.spend.provenance})",
                    style=chrome_color("muted"),
                )
            )
        else:
            details.append(Text("no quota windows measured", style=chrome_color("muted")))
        return Panel(
            Group(*details),
            title=Text(account.label, style="bold"),
            border_style=chrome_color("accent"),
            padding=(0, 1),
        )

    @staticmethod
    def _account_details(account: BillingAccountView) -> Text:
        parts: list[str] = [account.billing_mode]
        subscription = account.subscription
        if subscription is not None and subscription.plan is not None:
            # The provider's slug is display data, not a Grove tier vocabulary.
            parts.append(f"plan {subscription.plan}")
        if subscription is not None and subscription.detail is not None:
            parts.append(subscription.detail)
        return Text(" · ".join(parts), style=chrome_color("muted"))

    @staticmethod
    def _account_status(account: BillingAccountView) -> Text | None:
        if account.status == "ok":
            return None
        if account.status == "stale":
            age = (
                f" — last good reading {humanize.naturaldelta(account.stale_seconds)} old"
                if account.stale_seconds is not None
                else " — showing last good reading"
            )
            suffix = f"; last refresh: {account.last_error}" if account.last_error else ""
            return Text(f"stale{age}{suffix}", style=status_color(WorkspaceStatus.ORPHANED))
        detail = f" — {account.detail}" if account.detail else ""
        return Text(
            f"{account.status}{detail}",
            style=status_color(WorkspaceStatus.ERROR),
        )

    def _window(self, window: SubscriptionWindowView) -> RenderableType:
        reset = self._reset(window)
        projection = self._projection(window.projection)
        details = " · ".join(part for part in (reset, projection) if part)
        if window.used_percent is None:
            line = Text(f"{window.label}: not measured", style=chrome_color("muted"))
            detail_line = Text(f"  {details}", style=chrome_color("muted")) if details else Text()
            return Group(line, detail_line)

        progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(
                bar_width=28,
                complete_style=status_color(WorkspaceStatus.ACTIVE),
                finished_style=status_color(WorkspaceStatus.ACTIVE),
                style=chrome_color("muted"),
            ),
            TextColumn("{task.completed:.0f}% used"),
            expand=False,
        )
        # Rich needs a 0..total fill but the printed percentage remains the
        # provider's reading if it exceeds the nominal limit.
        progress.add_task(
            window.label,
            total=100,
            completed=int(max(0, min(window.used_percent, 100))),
        )
        detail_line = Text(f"  {details}", style=chrome_color("muted")) if details else Text()
        return Group(progress, detail_line)

    @staticmethod
    def _reset(window: SubscriptionWindowView) -> str | None:
        if window.resets_at is None:
            return None
        return f"resets {window.resets_at.astimezone(UTC):%Y-%m-%d %H:%M UTC}"

    @staticmethod
    def _projection(projection: SubscriptionWindowProjection | None) -> str | None:
        if projection is None:
            return None
        if projection.verdict == "unknown":
            return "burn unknown"
        if projection.projected_percent is None:
            return f"burn {projection.verdict}"
        return f"burn {projection.verdict} ({projection.projected_percent:.0f}% projected)"


def quota_status(
    *,
    as_json: bool = typer.Option(False, "--json", help="Emit the quota wire payload as JSON."),
) -> None:
    """Show selected subscription quota windows, grouped by provider.

    Default output is a terminal-native account view with each measured window's
    current usage bar, reset time, and measured burn verdict. ``--json`` writes
    only the same structured view returned by the daemon's ``GET /usage/quotas``
    endpoint, for scripts and other clients.
    """
    report = QuotaReport.read()
    if as_json:
        typer.echo(report.json())
        return
    Console(file=sys.stdout).print(report.render())


def register(app: typer.Typer) -> None:
    """Graft the quota read onto Grove's flat top-level CLI."""
    app.command("quota")(quota_status)


__all__ = ["QuotaReport", "register"]
