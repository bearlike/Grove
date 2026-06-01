"""StatusBar — VS Code-style workbench bar at the bottom of the list screen.

Mirrors VS Code's ``StatusbarPart``: a single full-width row whose
background asserts the application's brand and current state. The row
itself is the unifier — no `·` separators between segments, the bg
colour does that work; padding alone separates them. A muted vertical
bar (`│`) splits sub-groups within an alignment zone (e.g. count chips
vs. selection summary on the left) — same idiom claude-squad uses.

Background tiers (analogue of VS Code's blue/orange/purple):
    normal     → ``$primary``  (clay)        — fleet under management
    -attention → ``$warning``  (amber)       — any ORPHANED or ERROR workspace
    -empty     → ``$panel``    (neutral)     — no workspaces yet

Layout zones (VS Code's two alignment groups):
    left       → brand identity, count chips, selection summary
    right      → filter chip (when active), theme indicator

Responsive collapse drops low-priority segments first as the terminal
narrows; the brand chip and count chips never drop. Three width tiers,
chosen by reading ``self.size.width`` once per render — no reactive
plumbing for the tier itself.

Flash messages (info / success / error) take over the selection-summary
slot for 3 seconds, after which a single shared timer restores the
selection view. Mirrors claude-squad's ErrBox auto-clear.

Exactly one widget per frame; render returns a Rich ``Text``. All hex
flows from ``grove.tui.theme`` via the ``_status`` accessors — no literal
hex anywhere in this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget

from grove.core import WorkspaceState, WorkspaceStatus
from grove.tui._status import (
    STATUS_GLYPH,
    active_pulse,
    chrome_color,
    ref_color,
    status_color,
    status_label,
)

# ─── chrome glyphs (widget-local; not theme-keyed) ──────────────────────────

_GLYPH_REPO: Final = "⌂"
_GLYPH_BRANCH: Final = "⎇"
_GLYPH_FILTER: Final = "⌕"
_GLYPH_SELECT: Final = "›"  # noqa: RUF001
_GLYPH_DIVIDER: Final = "│"

# Order in which count chips appear. Computed/persisted statuses both
# in the list — RUNNING is rare post-reconciliation but mapped so a
# debug renderer stays coherent (status_color handles it).
_COUNT_ORDER: Final[tuple[WorkspaceStatus, ...]] = (
    WorkspaceStatus.ACTIVE,
    WorkspaceStatus.IDLE,
    WorkspaceStatus.PAUSED,
    WorkspaceStatus.OFFLINE,
    WorkspaceStatus.ORPHANED,
    WorkspaceStatus.ERROR,
)

# Statuses that flip the bar into the amber `-attention` tier.
_ATTENTION_STATUSES: Final[frozenset[WorkspaceStatus]] = frozenset(
    {WorkspaceStatus.ORPHANED, WorkspaceStatus.ERROR}
)

# Truncation budgets — keep segments compact at any width.
_REPO_TRIM: Final = 24
_TITLE_TRIM: Final = 28
_BRANCH_TRIM: Final = 32
_FILTER_TRIM: Final = 20

# Width tiers (column counts). `medium` drops the theme chip; `narrow`
# also drops the selection summary and filter chip.
_WIDE_THRESHOLD: Final = 110
_MEDIUM_THRESHOLD: Final = 80

_FLASH_SECONDS: Final = 3.0

FlashLevel = Literal["info", "success", "error"]
_Tier = Literal["wide", "medium", "narrow"]


class StatusBar(Widget):
    """One-line workbench bar: identity, fleet counts, selection, filter, theme."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: bottom;
        background: $primary;
        color: $foreground;
        padding: 0 1;
    }
    StatusBar.-attention {
        background: $warning;
    }
    StatusBar.-empty {
        background: $panel;
    }
    """

    repo: reactive[str] = reactive("")
    breakdown: reactive[dict[WorkspaceStatus, int]] = reactive[dict[WorkspaceStatus, int]](
        dict, always_update=True
    )
    selection: reactive[WorkspaceState | None] = reactive[WorkspaceState | None](None)
    filter_query: reactive[str] = reactive("")
    flash_message: reactive[str] = reactive("")
    flash_level: reactive[FlashLevel] = reactive[FlashLevel]("info")
    # Pulse clock pushed by the parent screen at 4 Hz. Drives the swell on
    # the selection-summary glyph when the focused row is ACTIVE; ignored
    # by the count chips (counts read as steady reference data — pulsing
    # `● 3` would suggest the count itself is changing). 0 = resting frame.
    pulse_frame: reactive[int] = reactive(0)

    def __init__(self, repo_root: Path) -> None:
        super().__init__()
        self.set_reactive(StatusBar.repo, repo_root.name or str(repo_root))
        self._flash_timer: Timer | None = None

    # ─── public API ────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Total workspace count. Convenience for callers/tests."""
        return sum(self.breakdown.values())

    def flash(self, message: str, *, level: FlashLevel = "info") -> None:
        """Show a transient message in the selection slot for 3 seconds.

        Empty ``message`` clears immediately. Replacing an active flash
        cancels the previous timer so the new message gets a full window.
        """
        self._cancel_flash_timer()
        self.flash_message = message
        self.flash_level = level
        if message:
            self._flash_timer = self.set_timer(_FLASH_SECONDS, self._clear_flash)

    # ─── reactives → state classes & repaint ───────────────────────────────

    def watch_breakdown(self, breakdown: dict[WorkspaceStatus, int]) -> None:
        total = sum(breakdown.values())
        has_attention = any(breakdown.get(s, 0) > 0 for s in _ATTENTION_STATUSES)
        self.set_class(total == 0, "-empty")
        self.set_class(has_attention, "-attention")
        self.refresh()

    def watch_selection(self, _value: WorkspaceState | None) -> None:
        self.refresh()

    def watch_filter_query(self, _value: str) -> None:
        self.refresh()

    def watch_flash_message(self, _value: str) -> None:
        self.refresh()

    def watch_repo(self, _value: str) -> None:
        self.refresh()

    def watch_pulse_frame(self, _value: int) -> None:
        # Selection-summary glyph is the only consumer of the pulse on this
        # bar; counts stay steady. Skip the repaint when the focused row
        # isn't ACTIVE — a pulsing IDLE/PAUSED selection would contradict
        # the semantic. Empty selection also short-circuits.
        sel = self.selection
        if sel is None or sel.status != WorkspaceStatus.ACTIVE:
            return
        self.refresh()

    # ─── lifecycle ─────────────────────────────────────────────────────────

    def on_unmount(self) -> None:
        self._cancel_flash_timer()

    # ─── rendering ─────────────────────────────────────────────────────────

    def render(self) -> Text:
        # `self.size.width` is the OUTER width including padding. TCSS sets
        # `padding: 0 1`, so 2 columns are eaten by the gutters.
        outer = self.size.width
        content_width = max(outer - 2, 0) if outer else 0
        tier = _width_tier(content_width)
        dark = self.app.current_theme.dark

        left = self._render_left(dark=dark, tier=tier)
        right = self._render_right(dark=dark, tier=tier)

        if content_width <= 0:
            # Pre-layout render: no width yet. Emit left + right joined by
            # a single space; subsequent renders right-justify.
            joined = Text()
            joined.append_text(left)
            if right.cell_len:
                joined.append("  ")
                joined.append_text(right)
            return joined

        gap = max(content_width - left.cell_len - right.cell_len, 1)
        out = Text()
        out.append_text(left)
        out.append(" " * gap)
        out.append_text(right)
        return out

    # ─── private: zone composers ───────────────────────────────────────────

    def _render_left(self, *, dark: bool, tier: _Tier) -> Text:
        text = Text()
        repo = _truncate(self.repo, _REPO_TRIM) or "(no repo)"
        text.append(f"{_GLYPH_REPO} {repo}", style="bold")

        # Count chips — non-zero only.
        for status in _COUNT_ORDER:
            n = self.breakdown.get(status, 0)
            if n <= 0:
                continue
            text.append("  ")
            text.append(
                STATUS_GLYPH.get(status, "?"),
                style=f"bold {status_color(status, dark=dark)}",
            )
            text.append(f" {n}", style="bold")

        # Selection summary or flash. Narrow tier drops both — counts and
        # brand alone fit and stay legible.
        if tier == "narrow":
            return text
        summary = self._render_summary(dark=dark)
        if summary.cell_len:
            text.append("   ")
            text.append(_GLYPH_DIVIDER, style=chrome_color("muted", dark=dark))
            text.append("   ")
            text.append_text(summary)
        return text

    def _render_right(self, *, dark: bool, tier: _Tier) -> Text:
        text = Text()
        if tier == "narrow":
            return text
        if self.filter_query:
            query = _truncate(self.filter_query, _FILTER_TRIM)
            text.append(f'{_GLYPH_FILTER} "{query}"', style="bold underline")
        if tier == "wide":
            if text.cell_len:
                text.append("   ")
            text.append("dark" if dark else "light", style=chrome_color("muted", dark=dark))
        return text

    def _render_summary(self, *, dark: bool) -> Text:
        """Selection summary OR flash — the two are mutually exclusive."""
        if self.flash_message:
            return self._render_flash(dark=dark)
        sel = self.selection
        if sel is None:
            return Text()
        title = _truncate(sel.title, _TITLE_TRIM)
        branch = _truncate(sel.branch, _BRANCH_TRIM)
        muted = chrome_color("muted", dark=dark)
        branch_hex = ref_color("branch", dark=dark)
        # ACTIVE selection pulses in lockstep with the card list (same
        # `pulse_frame` set by the screen). Other statuses use the canonical
        # static lookup. Glyph and label share the resolved color so the
        # swell reads as one chunk, same rule as the card body.
        if sel.status == WorkspaceStatus.ACTIVE:
            status_glyph_char, status_hex = active_pulse(self.pulse_frame, dark=dark)
        else:
            status_glyph_char = STATUS_GLYPH.get(sel.status, "?")
            status_hex = status_color(sel.status, dark=dark)
        text = Text()
        text.append(_GLYPH_SELECT, style=muted)
        text.append(" ")
        text.append(title, style="bold")
        text.append("  ")
        text.append(_GLYPH_BRANCH, style=muted)
        text.append(" ")
        text.append(branch, style=f"bold {branch_hex}")
        text.append("  ")
        text.append(status_glyph_char, style=f"bold {status_hex}")
        text.append(" ")
        text.append(status_label(sel.status), style=f"bold {status_hex}")
        return text

    def _render_flash(self, *, dark: bool) -> Text:
        msg = self.flash_message
        level = self.flash_level
        if level == "success":
            style = f"bold {ref_color('diff_add', dark=dark)}"
        elif level == "error":
            style = f"bold {ref_color('diff_remove', dark=dark)}"
        else:
            style = "bold"
        return Text(msg, style=style)

    # ─── internal: flash timer ─────────────────────────────────────────────

    def _clear_flash(self) -> None:
        self.flash_message = ""
        self._flash_timer = None

    def _cancel_flash_timer(self) -> None:
        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer = None


# ─── helpers ────────────────────────────────────────────────────────────────


def _width_tier(width: int) -> _Tier:
    if width >= _WIDE_THRESHOLD:
        return "wide"
    if width >= _MEDIUM_THRESHOLD:
        return "medium"
    return "narrow"


def _truncate(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return value[: limit - 1] + "…"
