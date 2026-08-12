"""Keyboard-first historical usage screen backed by the shared engine service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from grove.core.contracts.usage import (
    UsageActivityView,
    UsageFilters,
    UsageFindingsView,
    UsageQuotasView,
    UsageSessionPageView,
    UsageSummaryView,
)
from grove.core.usage import UsageService
from grove.tui.widgets.dashboard_grid import _human_tokens


@dataclass(frozen=True, slots=True)
class _UsageData:
    summary: UsageSummaryView
    activity: UsageActivityView
    quotas: UsageQuotasView
    sessions: UsageSessionPageView
    findings: UsageFindingsView


class UsageLoaded(Message):
    def __init__(self, data: _UsageData) -> None:
        super().__init__()
        self.data = data


class UsageFailed(Message):
    def __init__(self, detail: str) -> None:
        super().__init__()
        self.detail = detail


class UsageScreen(Screen[None]):
    """One explicit-refresh screen; all blocking reads run in a thread worker."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "back", "Back"),
        Binding("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    UsageScreen #usage-body { padding: 1 2; height: 1fr; overflow-y: auto; }
    UsageScreen .usage-panel { border: round $secondary; padding: 1; margin-bottom: 1; }
    """

    def __init__(self, *, service: UsageService) -> None:
        super().__init__()
        self._service = service
        self._refreshing = False
        self._lifecycle_lock = Lock()
        self._worker_running = False
        self._unmounted = False
        self._service_closed = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("Loading usage…", id="usage-body", classes="usage-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._start_refresh()

    def action_back(self) -> None:
        if self._refreshing:
            self.notify("Usage refresh is still running; wait for it to finish.")
            return
        self.app.pop_screen()

    def action_refresh(self) -> None:
        if not self._refreshing:
            self._start_refresh()

    def action_quit(self) -> None:
        if self._refreshing:
            self.notify("Usage refresh is still running; wait for it to finish.")
            return
        self.app.exit()

    def _start_refresh(self) -> None:
        self._refreshing = True
        with self._lifecycle_lock:
            self._worker_running = True
        self.run_worker(self._read, thread=True, group="usage-refresh", exclusive=True)

    def _read(self) -> None:
        try:
            self._service.refresh(force=False)
            filters = UsageFilters(
                since=datetime.now(UTC) - timedelta(days=29),
                until=datetime.now(UTC) + timedelta(seconds=1),
                tz="UTC",
            )
            data = _UsageData(
                summary=self._service.summary(filters),
                activity=self._service.activity(filters, metric="tokens"),
                quotas=self._service.quotas(),
                sessions=self._service.sessions(filters, cursor=None, limit=20, sort="recent"),
                findings=self._service.findings(filters),
            )
        except Exception as exc:
            self.post_message(UsageFailed(type(exc).__name__))
            return
        else:
            self.post_message(UsageLoaded(data))
        finally:
            self._finish_worker()

    def _finish_worker(self) -> None:
        should_close = False
        with self._lifecycle_lock:
            self._worker_running = False
            if self._unmounted and not self._service_closed:
                self._service_closed = True
                should_close = True
        if should_close:
            self._service.close()

    def on_unmount(self) -> None:
        should_close = False
        with self._lifecycle_lock:
            self._unmounted = True
            if not self._worker_running and not self._service_closed:
                self._service_closed = True
                should_close = True
        if should_close:
            self._service.close()

    def on_usage_failed(self, message: UsageFailed) -> None:
        self._refreshing = False
        self.query_one("#usage-body", Static).update(
            f"Usage refresh failed ({message.detail}). Press r to retry."
        )

    def on_usage_loaded(self, message: UsageLoaded) -> None:
        self._refreshing = False
        summary = message.data.summary
        quotas = message.data.quotas
        sessions = message.data.sessions
        lines = Text()
        lines.append("USAGE\n", style="bold")
        lines.append(f"sessions {_number(summary.sessions)}   turns {_number(summary.turns)}   ")
        lines.append(f"tokens {_tokens(summary.tokens)}   active {_duration(summary.duration)}\n")
        lines.append(
            f"tools {summary.tools.calls} calls / {summary.tools.failures} failures   "
            f"files {summary.files_changed}\n"
        )
        lines.append("\nACTIVITY · TOKENS\n", style="bold")
        lines.append(_heatmap(message.data.activity))
        lines.append("\n")
        lines.append("\nQUOTA\n", style="bold")
        lines.append(_quota_text(quotas))
        lines.append("\nSESSIONS\n", style="bold")
        for row in sessions.rows:
            lines.append(f"{row.session_id[:12]}  {_tokens(row.tokens)}  {row.provider}\n")
        if message.data.findings.findings:
            lines.append("\nFINDINGS\n", style="bold")
            for finding in message.data.findings.findings[:5]:
                lines.append(f"{finding.count}x  {finding.title}\n")
        self.query_one("#usage-body", Static).update(lines)


def _number(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def _quota_text(quotas: UsageQuotasView) -> Text:
    """Selected quota accounts, or an actionable config-owned empty state."""
    lines = Text()
    if not quotas.accounts:
        lines.append("No subscription profiles selected in usage.quota.profiles.\n")
        return lines
    for account in quotas.accounts:
        windows = (
            "  ".join(
                f"{window.label} {window.remaining_percent:.0f}% left"
                if window.remaining_percent is not None
                else f"{window.label} unknown"
                for window in account.windows
            )
            or account.status
        )
        lines.append(f"{account.label} · {account.provider} · {windows}\n")
    return lines


def _tokens(tokens: object) -> str:
    provider_total = getattr(tokens, "provider_total", None)
    if provider_total is not None:
        return _human_tokens(provider_total)
    total = sum(
        getattr(tokens, key) or 0
        for key in ("fresh_input", "cache_read", "cache_creation", "reasoning", "output")
    )
    measured = any(
        getattr(tokens, key) is not None
        for key in ("fresh_input", "cache_read", "cache_creation", "reasoning", "output")
    )
    if measured:
        return _human_tokens(total)
    return "unknown"


def _duration(duration: object) -> str:
    active = getattr(duration, "active_ms", None)
    elapsed = getattr(duration, "elapsed_span_ms", None)
    if active is not None:
        return f"{active // 60000}m active"
    return f"{elapsed // 60000}m span" if elapsed is not None else "unknown"


def _heatmap(activity: UsageActivityView) -> str:
    """One stable cell per day; exact values stay beside the glyph."""
    levels = "·░▒▓█"
    maximum = activity.max_value
    parts: list[str] = []
    for bucket in activity.buckets:
        level = 0 if maximum <= 0 else min(4, max(1, int(bucket.value / maximum * 4)))
        parts.append(levels[level])
    tail = activity.buckets[-1] if activity.buckets else None
    detail = f"  {tail.day} {tail.value:g}" if tail is not None else "  no measured days"
    return "".join(parts) + detail
