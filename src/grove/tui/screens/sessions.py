"""SessionsScreen — browse one workspace's agent-session history (issue #33).

Pushed from the list screen on ``s`` for the selected workspace. Layout
mirrors the list screen's "panels on canvas" split: a `SessionList` of
session rows on the left (one `SessionRow` card per on-disk session,
newest first) and a scrollable `history` panel on the right showing the
highlighted session's normalized turns. Rendering reuses the `grove
sessions` CLI's glyph conventions (the prompt chevron, ⚒ tool, ⏺
assistant) and the agent-state accessors — no new visual language.

Data comes from the engine's bounded per-workspace seams:
``SessionExplorer.for_workspace(id)`` (one-cwd scan) on mount/refresh and
``turns_for(listing)`` per highlighted session (cached per session id —
history is static, so one parse per session per visit is enough). No
timers: this surface reads recorded history, it doesn't stream.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Final

import humanize
from loguru import logger
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Header, ListItem, ListView, Static

from grove.core import GroveError, SessionExplorer, SessionListing
from grove.core.agents import SessionTurn
from grove.tui._status import (
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    chrome_color,
    ref_color,
)
from grove.tui._turns import ENTRY_TEXT_CAP, append_turn_body, truncate
from grove.tui.widgets.footer import ContextualFooter, FooterKey

# Same trim the workspace card / `grove sessions` CLI use: row labels stay
# one line. The per-entry cap lives in `_turns.py` with the shared renderer.
_LABEL_TRIM: Final = 48
# Bound the rendered conversation — the panel shows the most recent turns;
# `grove sessions show` / the webapp are the full-transcript surfaces.
_MAX_TURNS: Final = 50

_FOOTER_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("s,escape", "Back"),
    ("t", "Tools"),
    ("r", "Refresh"),
)


class SessionRow(ListItem):
    """One session as a ListItem; body is a single Static rendered via Rich Text.

    Focus chrome is TCSS-only, same contract as `WorkspaceCard`: a
    transparent `round $surface` border by default, gray outline on hover,
    and the parent-list-scoped highlight rule swaps it to clay. Fixed
    `height: 4` keeps highlighting layout-stable.
    """

    DEFAULT_CSS = """
    SessionRow {
        height: 4;
        padding: 0 1;
        border: round $surface;
    }
    SessionRow:hover {
        border: round $secondary;
    }
    """

    def __init__(self, listing: SessionListing, *, dark: bool) -> None:
        super().__init__()
        self.listing = listing
        self._dark = dark

    def compose(self) -> ComposeResult:
        yield Static(_render_session_row(self.listing, dark=self._dark))

    @property
    def body_text(self) -> str:
        """Plain rendered body — the test seam."""
        content = self.query_one(Static).content
        return content.plain if isinstance(content, Text) else str(content)


class SessionList(ListView):
    """Read-only list of one workspace's sessions, newest first."""

    DEFAULT_CSS = """
    SessionList {
        width: 40%;
        min-width: 30;
        height: 1fr;
        background: $surface;
        border: round $secondary;
        border-title-color: $primary;
        border-title-align: left;
        padding: 0 1;
        margin-right: 1;
    }
    SessionList:focus {
        border: round $primary;
    }
    SessionList:focus > SessionRow.-highlight {
        background: $panel;
        border: round $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._listings: tuple[SessionListing, ...] = ()

    def on_mount(self) -> None:
        self.border_title = "sessions"

    def populate(self, listings: tuple[SessionListing, ...], *, dark: bool) -> None:
        """Rebuild one SessionRow per listing; cursor lands on the newest."""
        self._listings = tuple(listings)
        self.clear()
        for listing in self._listings:
            self.append(SessionRow(listing, dark=dark))
        if self._listings:
            self.index = 0

    @property
    def selected_listing(self) -> SessionListing | None:
        idx = self.index
        if idx is None or not self._listings:
            return None
        if idx < 0 or idx >= len(self._listings):
            return None
        return self._listings[idx]


class SessionsScreen(Screen[None]):
    """Per-workspace session-history browser: session list + turns panel."""

    DEFAULT_CSS = """
    SessionsScreen #main {
        height: 1fr;
        padding: 0 1;
    }
    SessionsScreen #history-panel {
        height: 1fr;
    }
    SessionsScreen #history-panel:focus {
        border: round $primary;
    }
    SessionsScreen #empty-wrap {
        display: none;
        height: 1fr;
        align: center middle;
    }
    SessionsScreen #sessions-empty {
        width: auto;
        height: auto;
        padding: 1 3;
        color: $text-muted;
        text-style: italic;
        text-align: center;
    }
    SessionsScreen.-empty #main {
        display: none;
    }
    SessionsScreen.-empty #empty-wrap {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "back", "Back", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Back", show=False),
        Binding("t", "toggle_tools", "Tools", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(
        self,
        explorer: SessionExplorer,
        *,
        workspace_id: str,
        workspace_title: str,
    ) -> None:
        super().__init__()
        self._explorer = explorer
        self._workspace_id = workspace_id
        self._workspace_title = workspace_title
        # Turns are recorded history — static once read — so one parse per
        # session per visit is enough. Keyed by (adapter, id), cleared on `r`.
        self._turns_cache: dict[tuple[str, str], tuple[SessionTurn, ...]] = {}
        # Tool detail starts collapsed: consecutive tool calls fold into one
        # "N tool calls" row; `t` expands them. Screen-wide, not per-session
        # — the user's "show me the plumbing" intent outlives one highlight.
        self._expand_tools = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            yield SessionList()
            with VerticalScroll(id="history-panel", classes="grove-card"):
                yield Static(id="turns-body")
        with Vertical(id="empty-wrap"):
            yield Static(
                "no agent sessions recorded for this workspace — press [bold]s[/] to go back",
                id="sessions-empty",
                classes="grove-card",
            )
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.title = "Grove — Sessions"
        self.sub_title = self._workspace_title
        self.query_one("#history-panel", VerticalScroll).border_title = "history"
        self.query_one(ContextualFooter).set_keys(
            [FooterKey(key, label) for key, label in _FOOTER_KEYS]
        )
        self._reload()
        self.query_one(SessionList).focus()

    # ─── actions ──────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._turns_cache.clear()
        self._reload()

    def action_toggle_tools(self) -> None:
        """Toggle tool-call detail in the turns panel (grouped ↔ expanded)."""
        self._expand_tools = not self._expand_tools
        self._show_turns()

    # ─── selection → turns ────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        del event
        self._show_turns()

    def _show_turns(self) -> None:
        listing = self.query_one(SessionList).selected_listing
        if listing is None:
            return
        dark = self.app.current_theme.dark
        self.query_one("#turns-body", Static).update(
            _render_turns(
                listing,
                self._turns(listing),
                dark=dark,
                expand_tools=self._expand_tools,
            )
        )
        # The newest exchange is what the user came for — land at the tail
        # (the CLI's oldest-first order is preserved; we just scroll past it).
        panel = self.query_one("#history-panel", VerticalScroll)
        self.call_after_refresh(lambda: panel.scroll_end(animate=False))

    def _turns(self, listing: SessionListing) -> tuple[SessionTurn, ...]:
        key = (listing.summary.adapter_kind, listing.summary.session_id)
        cached = self._turns_cache.get(key)
        if cached is not None:
            return cached
        try:
            turns = tuple(self._explorer.turns_for(listing, last=_MAX_TURNS))
        except Exception as exc:  # best-effort, peek contract — never break the screen
            logger.debug("turns for session {} failed: {}", listing.summary.session_id, exc)
            turns = ()
        self._turns_cache[key] = turns
        return turns

    # ─── internal ─────────────────────────────────────────────────────────

    def _reload(self) -> None:
        try:
            listings = tuple(self._explorer.for_workspace(self._workspace_id))
        except GroveError as exc:
            logger.debug("session scan for workspace {} failed: {}", self._workspace_id, exc)
            listings = ()
        self.query_one(SessionList).populate(listings, dark=self.app.current_theme.dark)
        self.set_class(not listings, "-empty")
        if not listings:
            self.query_one("#turns-body", Static).update("")

    @property
    def turns_text(self) -> str:
        """Plain rendered turns panel — the test seam."""
        content = self.query_one("#turns-body", Static).content
        return content.plain if isinstance(content, Text) else str(content)


# ─── pure render helpers (the test seams) ────────────────────────────────────


def _ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    return humanize.naturaldelta(datetime.now(UTC) - when) + " ago"


def _render_session_row(listing: SessionListing, *, dark: bool) -> Text:
    """Two-line session row: state glyph + id + label, then the fact line.

    Same typographic tiers as the workspace card: bold + semantic color for
    values (state glyph/label, adapter), bold default-fg counters (turn
    count), muted labels and connectives. ``grove`` is a quiet provenance
    qualifier (absence is the default), same convention as the root tag.
    """
    summary = listing.summary
    state = summary.activity.state
    state_style = f"bold {agent_state_color(state, dark=dark)}"
    muted = chrome_color("muted", dark=dark)
    text = Text()
    text.append(agent_state_glyph(state), style=state_style)
    text.append(" ")
    text.append(summary.session_id[:8], style="bold")
    label = summary.title or summary.last_prompt or summary.first_prompt
    if label:
        text.append("  ")
        text.append(truncate(label, _LABEL_TRIM))
    text.append("\n")
    text.append(summary.adapter_kind, style=f"bold {ref_color('info', dark=dark)}")
    text.append(" · ", style=muted)
    text.append(str(summary.activity.human_turns), style="bold")
    text.append(" turns", style=muted)
    text.append(" · ", style=muted)
    text.append(agent_state_label(state), style=state_style)
    text.append(" · ", style=muted)
    text.append(_ago(summary.modified_at), style=muted)
    if listing.provenance == "grove_launched":
        text.append(" · grove", style=muted)
    return text


def _render_turns(
    listing: SessionListing,
    turns: tuple[SessionTurn, ...],
    *,
    dark: bool,
    expand_tools: bool = False,
) -> Text:
    """The highlighted session's conversation, oldest first.

    Mirrors the `grove sessions show` shape: a header line, then per turn a
    muted divider and the shared turn body (`_turns.append_turn_body` — the
    single implementation the peek rail's transcript tab also renders
    through). ``expand_tools`` toggles between the grouped "N tool calls"
    rows (default) and the individual ⚒ lines.
    """
    summary = listing.summary
    muted = chrome_color("muted", dark=dark)
    text = Text()
    text.append(summary.session_id[:8], style="bold")
    text.append(" · ", style=muted)
    text.append(summary.adapter_kind, style=f"bold {ref_color('info', dark=dark)}")
    if summary.git_branch:
        text.append(" · ", style=muted)
        text.append(summary.git_branch, style=f"bold {ref_color('branch', dark=dark)}")
    if summary.title:
        text.append("\n")
        text.append(truncate(summary.title, ENTRY_TEXT_CAP))
    if not turns:
        text.append("\n\n")
        text.append("(no turns recorded)", style=muted)
        return text
    if len(turns) == _MAX_TURNS:
        text.append("\n")
        text.append(f"showing the most recent {_MAX_TURNS} turns", style=muted)
    for index, turn in enumerate(turns, start=1):
        when = turn.started_at.isoformat(timespec="seconds") if turn.started_at else ""
        text.append("\n\n")
        text.append(f"── turn {index} {when}".rstrip(), style=muted)
        text.append("\n")
        append_turn_body(text, turn, dark=dark, expand_tools=expand_tools)
    return text
