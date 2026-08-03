"""SessionsScreen — browse one workspace's agent-session history.

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

from grove.core import GroveError, RepoRegistry, SessionExplorer, SessionListing, WorkspaceStatus
from grove.core.agents import SessionSummary, SessionTurn
from grove.core.sessions import CatalogEntry, SessionCatalog
from grove.tui._status import (
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    chrome_color,
    ref_color,
    status_color,
)
from grove.tui._turns import ENTRY_TEXT_CAP, TranscriptBuilder, truncate
from grove.tui.widgets.footer import ContextualFooter, FooterKey

# Same trim the workspace card / `grove sessions` CLI use: row labels stay
# one line. The per-entry cap lives in `_turns.py` with the shared renderer.
_LABEL_TRIM: Final = 48
# Same cap the CLI's PROJECT column uses (`cli_sessions.py`).
_PROJECT_TRIM: Final = 24
# Bound the rendered conversation — the panel shows the most recent turns;
# `grove sessions show` / the webapp are the full-transcript surfaces.
_MAX_TURNS: Final = 50
# Bound the host-wide row count — a display cap, not a scan cap (the catalog
# scan itself is metadata-only and cheap; this just keeps one wall-of-text
# screen from rendering thousands of rows).
_MAX_HOST_SESSIONS: Final = 200

_FOOTER_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("s,escape", "Back"),
    ("t", "Tools"),
    ("h", "Host"),
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

    def __init__(
        self,
        listing: SessionListing,
        *,
        dark: bool,
        host: bool = False,
        project: str | None = None,
        branch: str | None = None,
        live: bool = False,
    ) -> None:
        super().__init__()
        self.listing = listing
        self._dark = dark
        # Host-scope-only display extras — absent by default so the
        # project-scoped render stays byte-identical to the plain listing
        # (the same convention `agent_state=None` follows).
        self._host = host
        self._project = project
        self._branch = branch
        self._live = live

    def compose(self) -> ComposeResult:
        yield Static(
            _render_session_row(
                self.listing,
                dark=self._dark,
                host=self._host,
                project=self._project,
                branch=self._branch,
                live=self._live,
            )
        )

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

    def populate_catalog(self, entries: tuple[CatalogEntry, ...], *, dark: bool) -> None:
        """Rebuild one SessionRow per host-wide catalog entry; cursor lands
        on the newest.

        Each entry is synthesized into a `SessionListing` (`_catalog_listing`)
        so `selected_listing` stays the same type either scope renders — the
        turn-fetch/render pipeline downstream (`turns_for`, `TranscriptBuilder`)
        needs no host-scope branch of its own.
        """
        listings = tuple(_catalog_listing(entry) for entry in entries)
        self._listings = listings
        self.clear()
        for entry, listing in zip(entries, listings, strict=True):
            project = entry.project.repo_name if entry.project is not None else entry.ref.cwd
            self.append(
                SessionRow(
                    listing,
                    dark=dark,
                    host=True,
                    project=project,
                    branch=entry.ref.git_branch,
                    live=entry.live,
                )
            )
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
        Binding("h", "toggle_scope", "Host", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(
        self,
        explorer: SessionExplorer,
        *,
        workspace_id: str,
        workspace_title: str,
        registry: RepoRegistry | None = None,
        catalog: SessionCatalog | None = None,
    ) -> None:
        super().__init__()
        self._explorer = explorer
        self._workspace_id = workspace_id
        self._workspace_title = workspace_title
        # The host-scope seam — same injectable-registry pattern
        # as DashboardScreen/WorkspaceListScreen. `registry=None` (a caller
        # that hasn't wired it, e.g. an older test) just leaves the toggle a
        # no-op; the default project scope is untouched either way. `catalog`
        # is the direct-injection escape hatch (mirrors DashboardScreen's
        # `service` param) so a test can hand in a duck-typed fake without
        # building a real RepoRegistry.
        self._registry = registry
        self._catalog = (
            catalog
            if catalog is not None
            else (SessionCatalog(registry) if registry is not None else None)
        )
        self._host_scope = False
        # Turns are recorded history — static once read — so one parse per
        # session per visit is enough. Keyed by (adapter, id), cleared on `r`.
        self._turns_cache: dict[tuple[str, str], tuple[SessionTurn, ...]] = {}
        # Tool detail starts collapsed: consecutive tool calls fold into one
        # "N tool calls" row; `t` expands them. Screen-wide, not per-session
        # — the user's "show me the plumbing" intent outlives one highlight.
        self._expand_tools = False
        # Plain projection of the rendered turns panel — the test seam. The
        # panel itself holds a Rich Group (Markdown bodies + Text chrome),
        # which has no `.plain`, so the screen tracks it as the builder emits.
        self._turns_plain = ""

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

    def action_toggle_scope(self) -> None:
        """Flip between this workspace's own sessions and the host-wide catalog.

        A no-op when no registry/catalog was injected (an older/duck-typed
        test construction) — the same check-and-return shape every other
        precondition-guarded action in this TUI uses, since a raise here would
        surface as a crash rather than a flash.
        """
        if self._catalog is None:
            return
        self._host_scope = not self._host_scope
        self._reload()

    # ─── selection → turns ────────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        del event
        self._show_turns()

    def _show_turns(self) -> None:
        listing = self.query_one(SessionList).selected_listing
        if listing is None:
            return
        dark = self.app.current_theme.dark
        builder = _render_turns(
            listing,
            self._turns(listing),
            dark=dark,
            expand_tools=self._expand_tools,
        )
        self._turns_plain = builder.plain
        self.query_one("#turns-body", Static).update(builder.renderable)
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

    def _catalog_entries(self) -> tuple[CatalogEntry, ...]:
        """Every session the host-wide catalog can see, newest-first.

        Best-effort like every other read this screen already does — a
        scan failure degrades to an honestly empty list, never a crash.
        Bounded to `_MAX_HOST_SESSIONS` for display only (the catalog scan
        itself is a cheap, metadata-only walk — see `SessionCatalog.scan`).
        """
        if self._catalog is None:
            return ()
        try:
            return self._catalog.scan(limit=_MAX_HOST_SESSIONS)
        except Exception as exc:  # best-effort, peek contract — never break the screen
            logger.debug("host session catalog scan failed: {}", exc)
            return ()

    def _reload(self) -> None:
        list_widget = self.query_one(SessionList)
        dark = self.app.current_theme.dark
        if self._host_scope:
            entries = self._catalog_entries()
            list_widget.populate_catalog(entries, dark=dark)
            list_widget.border_title = "sessions · host"
            is_empty = not entries
        else:
            try:
                listings = tuple(self._explorer.for_workspace(self._workspace_id))
            except GroveError as exc:
                logger.debug("session scan for workspace {} failed: {}", self._workspace_id, exc)
                listings = ()
            list_widget.populate(listings, dark=dark)
            list_widget.border_title = "sessions"
            is_empty = not listings
        self.set_class(is_empty, "-empty")
        if is_empty:
            self._turns_plain = ""
            self.query_one("#turns-body", Static).update("")
            empty_text = (
                "no agent sessions found on this host — press [bold]h[/] to go back"
                if self._host_scope
                else "no agent sessions recorded for this workspace — press [bold]s[/] to go back"
            )
            self.query_one("#sessions-empty", Static).update(empty_text)

    @property
    def turns_text(self) -> str:
        """Plain projection of the rendered turns panel — the test seam.

        The panel holds a Rich `Group` (Markdown bodies + Text chrome), not a
        `Text`, so the plain text is tracked as the builder emits it rather
        than read back off the Static.
        """
        return self._turns_plain


# ─── pure render helpers (the test seams) ────────────────────────────────────


def _ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    return humanize.naturaldelta(datetime.now(UTC) - when) + " ago"


def _render_session_row(
    listing: SessionListing,
    *,
    dark: bool,
    host: bool = False,
    project: str | None = None,
    branch: str | None = None,
    live: bool = False,
) -> Text:
    """Two-line session row: state glyph + id + label, then the fact line.

    Same typographic tiers as the workspace card: bold + semantic color for
    values (state glyph/label, adapter), bold default-fg counters (turn
    count), muted labels and connectives. ``grove`` is a quiet provenance
    qualifier (absence is the default), same convention as the root tag.

    ``host``/``project``/``branch``/``live`` are host-scope-only;
    ``host=False`` (the default) renders byte-identical to the plain
    listing — project scope never clutters the row with a redundant
    project/branch, per the design-system "reuse an existing token, invent
    nothing" rule: ``branch`` reuses the existing branch teal, ``live``
    reuses the existing ACTIVE live-signal glyph + color, and the project
    name gets plain bold (no ref color owns "which repo" today).
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
    if host:
        text.append(" · ", style=muted)
        if project is not None:
            text.append(truncate(project, _PROJECT_TRIM), style="bold")
        else:
            text.append("-", style=muted)
        if branch:
            text.append(" ", style=muted)
            text.append(branch, style=f"bold {ref_color('branch', dark=dark)}")
        if live:
            text.append(" · ", style=muted)
            text.append("●", style=f"bold {status_color(WorkspaceStatus.ACTIVE, dark=dark)}")
    return text


def _catalog_listing(entry: CatalogEntry) -> SessionListing:
    """Synthesize a `SessionListing` from a host-wide `CatalogEntry`.

    The catalog is deliberately metadata-only (bounded head reads, never a
    full parse — see `SessionCatalog.scan`), so `title`/`first_prompt`/
    `last_prompt`/`activity` are left at their honest defaults rather than
    guessed; the row renders exactly what it knows. This is what lets the
    existing turn-fetch/render pipeline (`SessionExplorer.turns_for`,
    `_render_turns`, `TranscriptBuilder`) stay untouched for a session Grove
    may never have launched, in a repo this project's explorer never scans.
    """
    ref = entry.ref
    return SessionListing(
        summary=SessionSummary(
            session_id=ref.session_id,
            adapter_kind=ref.adapter_kind,
            transcript_path=ref.transcript_path,
            cwd=ref.cwd,
            created_at=ref.birth,
            modified_at=datetime.fromtimestamp(ref.mtime, tz=UTC),
            size_bytes=0,
            git_branch=ref.git_branch,
        ),
        provenance=entry.provenance,
        workspace_id=entry.workspace_id,
        workspace_title=entry.workspace_title,
    )


def _render_turns(
    listing: SessionListing,
    turns: tuple[SessionTurn, ...],
    *,
    dark: bool,
    expand_tools: bool = False,
) -> TranscriptBuilder:
    """The highlighted session's conversation, oldest first.

    Mirrors the `grove sessions show` shape: a header line, then per turn a
    muted divider and the shared turn body. The body is built by
    :class:`TranscriptBuilder` — the single implementation the peek rail's
    transcript tab also drives — so markdown rendering and tool grouping
    can't drift; this screen only adds the header + dividers as chrome.
    ``expand_tools`` toggles between the grouped "N tool calls" rows
    (default) and the individual ⚒ lines.
    """
    summary = listing.summary
    muted = chrome_color("muted", dark=dark)
    builder = TranscriptBuilder(dark=dark, expand_tools=expand_tools)
    header = Text()
    header.append(summary.session_id[:8], style="bold")
    header.append(" · ", style=muted)
    header.append(summary.adapter_kind, style=f"bold {ref_color('info', dark=dark)}")
    if summary.git_branch:
        header.append(" · ", style=muted)
        header.append(summary.git_branch, style=f"bold {ref_color('branch', dark=dark)}")
    builder.line(header)
    if summary.title:
        builder.line(Text(truncate(summary.title, ENTRY_TEXT_CAP)))
    if not turns:
        builder.gap()
        builder.line(Text("(no turns recorded)", style=muted))
        return builder
    if len(turns) == _MAX_TURNS:
        builder.line(Text(f"showing the most recent {_MAX_TURNS} turns", style=muted))
    for index, turn in enumerate(turns, start=1):
        when = turn.started_at.isoformat(timespec="seconds") if turn.started_at else ""
        builder.gap()
        builder.line(Text(f"── turn {index} {when}".rstrip(), style=muted))
        builder.gap()
        builder.add_turn(turn)
    return builder
