"""DashboardGrid — responsive grid of agent-activity tiles, grouped by project.

The Activity Dashboard's body (epic #11 §8). Where the list screen shows *one*
repo's workspaces in a vertical list, this shows *every* workspace across every
repo as a wall of tiles — the "what is every agent doing right now" view.

Two widgets live here:

* ``DashboardCard`` — one tile rendering a ``WorkspaceActivity``. Idle tiles are
  *compact* (glyph · title · age / branch · agent · state / diff · counts); live
  tiles are *promoted* — taller, with the agent's task summary, token usage, and
  a live, fit-to-cell tmux pane tail filling the extra space. Root-placement
  workspaces carry a quiet ``root`` tag. Pure renderer in ``_render_card_body``;
  the widget is a thin Static-wrapping shell with a per-tile diff guard so an
  unchanged tile never repaints.
* ``DashboardGrid`` — a Textual ``Grid`` that lays the tiles out. Column count is
  width-driven (one tile per ``_MIN_TILE_WIDTH`` cells, capped at
  ``_MAX_COLUMNS``) so the wall *fills* the terminal instead of floating in empty
  space. Tiles reflow on resize. Agent state drives span: working / waiting /
  blocked / error tiles promote to a taller row so the live work is the biggest
  thing on screen; idle / offline tiles collapse to a compact single row.

The grid is a *renderer* exactly like ``PeekRail`` — callers hand it a
``DashboardSnapshot`` (and the focused tile drives a separate pane-capture
cadence on the screen). It never calls git or tmux itself, which keeps the test
seam at the ``ActivityService`` / manager and lets the same data shape serve any
future client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import humanize
from rich.style import Style
from rich.text import Span, Text
from textual.app import ComposeResult
from textual.containers import Grid
from textual.reactive import reactive
from textual.widgets import Static

from grove.core.activity import WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState
from grove.core.workspace import Placement, WorkspaceState
from grove.tui._status import (
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    chrome_color,
    ref_color,
)

# Tile content trims — keep each line legible inside a grid cell. The tile width
# shrinks as the column count climbs, but the whole body is rendered ``no_wrap``
# (crop, not wrap) so a long line never steals a row from the fit math below;
# these trims just keep the ellipsis tasteful rather than mid-glyph.
_TITLE_TRIM: Final = 36
_TASK_TRIM: Final = 80
_BRANCH_TRIM: Final = 28

# Column packing. The wall fills the available width: one column per
# ``_MIN_TILE_WIDTH`` cells, capped at ``_MAX_COLUMNS`` so an enormous fleet
# scrolls rather than shaving every tile into an unreadable sliver. This replaces
# the old ``ceil(sqrt(N))`` heuristic, which left a wide terminal three-quarters
# empty (sqrt(9)=3 columns on a 200-cell screen — the "too spaced out" bug).
_MAX_COLUMNS: Final = 6
_MIN_TILE_WIDTH: Final = 36

# Row model. A grid row track is ``_GRID_ROW_UNIT`` cells tall; the round border
# eats ``_BORDER_ROWS`` of every tile. A compact tile spans one track (3 content
# rows — title / identity / stats, an exact fit, no wasted space); a promoted
# tile spans two (8 content rows) and fills the extra space with a live,
# fit-to-cell tmux pane tail rather than leaving it blank — the whole point of
# the redesign. ``grid-rows`` in the TCSS below MUST equal ``_GRID_ROW_UNIT``.
_GRID_ROW_UNIT: Final = 5
_BORDER_ROWS: Final = 2
_COMPACT_ROWS: Final = 1
_PROMOTED_ROWS: Final = 2

# Statuses (agent-activity states) that earn the taller "promoted" row span —
# the live work the user most wants to watch. Everything else collapses to the
# compact span. WAITING/BLOCKED promote too: they want the human, so they
# should not hide in the compact strip.
_PROMOTED_STATES: Final[frozenset[AgentActivityState]] = frozenset(
    {
        AgentActivityState.WORKING,
        AgentActivityState.WAITING,
        AgentActivityState.BLOCKED,
        AgentActivityState.ERROR,
    }
)


def is_promoted(activity: WorkspaceActivity) -> bool:
    """Whether a tile is promoted (taller, with a live pane tail) vs. compact.

    The single promotion rule — consumed by the grid's row-span layout, the card
    render's shape choice, AND the screen's pane-capture cadence — so the three
    can't drift. A tile is promoted when its primary session is in a live or
    attention state (working / waiting / blocked / error); idle / offline /
    starting / untracked tiles stay compact.
    """
    primary = activity.primary
    return primary is not None and primary.state in _PROMOTED_STATES


class DashboardCard(Static):
    """One workspace tile on the Activity Dashboard.

    Body is a single Static rendered via Rich ``Text`` — same one-widget-per-tile
    discipline as ``WorkspaceCard``. The tile re-renders only when its underlying
    ``WorkspaceActivity`` fingerprint changes (diff guard on plain text) or when
    its focused/pane state changes, so a wall of idle tiles costs ~zero repaints
    per tick.

    Focus chrome is TCSS-only (border swap), mirroring the list screen: the grid
    sets ``can_focus`` and the focused tile takes a ``$primary`` border. A live,
    fit-to-cell tmux pane tail fills the bottom of every *promoted* tile; the
    screen pushes captures in via ``set_pane_snapshot`` (every promoted tile on
    the slow tick, the focused tile faster). Compact (idle) tiles never stream a
    pane — the render ignores any snapshot set on a compact tile.
    """

    DEFAULT_CSS = """
    DashboardCard {
        height: 100%;
        padding: 0 1;
        border: round $surface;
        background: $surface;
    }
    DashboardCard:focus {
        border: round $primary;
        background: $panel;
    }
    DashboardCard.-attention {
        border: round $warning;
    }
    DashboardCard:focus.-attention {
        border: round $primary;
    }
    """

    can_focus = True

    # Pulse clock pushed by the screen at ~4 Hz; only WORKING tiles read it (the
    # live-signal heartbeat). Default 0 = resting frame so tiles mounted between
    # ticks render the canonical static appearance.
    pulse_frame: reactive[int] = reactive(0, layout=False)

    def __init__(self, activity: WorkspaceActivity) -> None:
        super().__init__()
        self._activity = activity
        # The most recent live pane capture for THIS tile, or None when the
        # tile is unfocused / not live. Only the focused live tile carries one.
        self._pane_snapshot: str | None = None
        self._last_plain: str = ""

    @property
    def workspace_id(self) -> str:
        return self._activity.state.id

    @property
    def activity(self) -> WorkspaceActivity:
        return self._activity

    @property
    def body_text(self) -> str:
        """Plain text of the rendered tile body. Stable seam for tests."""
        return self._last_plain

    def on_mount(self) -> None:
        self.set_class(self._activity.needs_attention, "-attention")
        self._repaint()

    def set_activity(self, activity: WorkspaceActivity) -> None:
        """Swap the tile's data and repaint if anything visible changed.

        The diff guard inside ``_repaint`` short-circuits when the rendered
        bytes are identical, so pushing a structurally-unchanged activity is
        cheap — that's what lets the screen re-key every tile on every snapshot
        without churn.
        """
        self._activity = activity
        self.set_class(activity.needs_attention, "-attention")
        self._repaint()

    def set_pane_snapshot(self, snapshot: str | None) -> None:
        """Set (or clear) the live-pane capture for this tile.

        ``None`` clears it. The screen pushes captures for every promoted tile
        (the focused one at a faster cadence); a compact tile's render ignores
        whatever is set here, so it never grows a streaming pane.
        """
        if snapshot == self._pane_snapshot:
            return
        self._pane_snapshot = snapshot
        self._repaint()

    def watch_pulse_frame(self, _frame: int) -> None:
        # Only the WORKING heartbeat reads the pulse — every other tile renders
        # identical bytes per frame and the diff guard would no-op anyway.
        primary = self._activity.primary
        if primary is not None and primary.state == AgentActivityState.WORKING:
            self._repaint()

    # ─── private ──────────────────────────────────────────────────────────

    def _repaint(self) -> None:
        text = _render_card_body(
            self._activity,
            dark=self._dark(),
            pulse_frame=self.pulse_frame,
            pane_snapshot=self._pane_snapshot,
        )
        if text.plain == self._last_plain:
            return
        self._last_plain = text.plain
        self.update(text)

    def _dark(self) -> bool:
        # `app` is None during pure-construction tests; default dark to match
        # the default registered theme (same fallback as WorkspaceCard).
        try:
            return bool(self.app.current_theme.dark)
        except Exception:
            return True


class DashboardGrid(Grid):
    """Responsive grid of ``DashboardCard`` tiles for one ``DashboardSnapshot``.

    Public surface (the test contract): ``populate(snapshot, lens)``,
    ``set_pulse_frame(frame)``, ``focused_card``, ``cards``. The screen and Pilot
    tests consume only this surface.

    Layout: a Textual ``Grid`` whose ``grid-size`` (column count) and per-tile
    row span are recomputed on every populate and on resize. Column count is
    ``ceil(sqrt(N))`` capped by both ``_MAX_COLUMNS`` and a width-derived maximum
    (so tiles never get unreadably thin). The grid uses uniform rows; a WORKING /
    attention tile gets ``row-span: 2`` so it reads as the biggest thing on
    screen, while idle tiles stay one row tall — fit-to-view at typical sizes,
    no page scroll.
    """

    DEFAULT_CSS = """
    DashboardGrid {
        height: 1fr;
        grid-gutter: 0 1;
        grid-rows: 5;
        padding: 0 1;
    }
    """

    # Tiles per project group are not visually separated here (a flat wall reads
    # cleaner than nested grids in a terminal); the screen renders a project
    # header band above each group's tiles instead. Grouping order is preserved
    # by populate so a group's tiles stay contiguous.

    def __init__(self, activities: list[WorkspaceActivity] | None = None) -> None:
        super().__init__()
        # Cards are created eagerly so they can be yielded from ``compose`` —
        # mounting them in compose (rather than a post-mount ``mount_all``)
        # sidesteps the async-mount race a caller hits when it mounts the grid
        # and immediately populates it. ``populate`` is still available for an
        # in-place rebuild after construction.
        self._pending: list[WorkspaceActivity] = list(activities or [])
        self._cards: list[DashboardCard] = []

    def compose(self) -> ComposeResult:
        self._cards = [DashboardCard(a) for a in self._pending]
        yield from self._cards

    def on_mount(self) -> None:
        self._apply_layout()

    @property
    def cards(self) -> list[DashboardCard]:
        return list(self._cards)

    @property
    def focused_card(self) -> DashboardCard | None:
        for card in self._cards:
            if card.has_focus:
                return card
        return None

    def populate(self, activities: list[WorkspaceActivity]) -> None:
        """Rebuild the tile wall in place from ``activities`` (display order).

        N is small (the whole fleet, typically < 30); incremental diffing isn't
        worth the complexity, so each populate clears and remounts. Focus is the
        screen's concern (it re-focuses by id after a rebuild). The screen
        normally constructs a fresh grid per snapshot instead — this in-place
        path exists for callers that keep one grid alive.
        """
        self.remove_children()
        self._cards = [DashboardCard(a) for a in activities]
        if self._cards:
            self.mount_all(self._cards)
        self._apply_layout()

    def set_pulse_frame(self, frame: int) -> None:
        """Push the live-signal pulse to every WORKING tile (heartbeat)."""
        for card in self._cards:
            card.pulse_frame = frame

    def on_resize(self) -> None:
        # Column count and tile spans are width-derived; recompute on resize so
        # the wall reflows (epic acceptance: "tiles reflow on resize").
        self._apply_layout()

    # ─── internal ─────────────────────────────────────────────────────────

    def _apply_layout(self) -> None:
        n = len(self._cards)
        columns = self._column_count(n)
        self.styles.grid_size_columns = columns
        # Per-tile row span: promote the live / attention tiles to two rows.
        for card in self._cards:
            rows = _PROMOTED_ROWS if is_promoted(card.activity) else _COMPACT_ROWS
            card.styles.row_span = rows

    def _column_count(self, n: int) -> int:
        """Fill the available width: one tile per ``_MIN_TILE_WIDTH`` cells.

        Width-driven, not square: a wide terminal should be *full* of tiles, not
        a small square wall floating in empty space (the old ``ceil(sqrt(N))``
        left a 200-cell screen three-columns wide). Capped at ``_MAX_COLUMNS`` so
        an enormous fleet scrolls instead of shaving tiles into slivers, and at
        ``n`` so a two-workspace fleet doesn't stretch into six thin columns.
        Before the widget knows its width (pre-layout) fall back to the cap.
        """
        if n <= 0:
            return 1
        width = self.size.width or 0
        if not width:
            return min(_MAX_COLUMNS, n)
        fit = max(1, width // _MIN_TILE_WIDTH)
        return max(1, min(fit, _MAX_COLUMNS, n))


def _render_card_body(
    activity: WorkspaceActivity,
    *,
    dark: bool,
    now: datetime | None = None,
    pulse_frame: int = 0,
    pane_snapshot: str | None = None,
) -> Text:
    """Render one dashboard tile as Rich ``Text`` (pure: identical inputs → bytes).

    Two shapes, chosen by whether the agent state is *promoted* (``_PROMOTED_STATES``):

    * **compact** (idle / offline / starting / unknown) — three rows, an exact
      fit for a one-track cell with no wasted space::

          ▶ title                3m ago
          branch · agent · idle
          +12 / -3 · ↑2 ↓0 · 8t 14r 31⚒

    * **promoted** (working / waiting / blocked / error) — eight rows in a
      two-track cell. Same identity, plus the agent's own task summary (or the
      reserved ``interpreted_status`` once an external-LLM interpreter fills it,
      #20), a fuller stat line with token usage, and a live, fit-to-cell tmux
      pane tail that fills the remaining rows instead of leaving them blank — the
      whole point of the redesign.

    Root-placement workspaces carry a quiet ``root`` tag (the metadata the user
    asked to surface). The whole body is ``no_wrap``, so a long line crops to the
    cell rather than wrapping and stealing a row from the pane-fill math.
    ``now`` is injectable for deterministic age formatting in tests.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    s = activity.state
    primary = activity.primary
    agent_state = primary.state if primary is not None else AgentActivityState.UNKNOWN
    promoted = is_promoted(activity)
    body_rows = (_PROMOTED_ROWS if promoted else _COMPACT_ROWS) * _GRID_ROW_UNIT - _BORDER_ROWS

    branch_hex = ref_color("branch", dark=dark)
    agent_hex = ref_color("info", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    add_hex = ref_color("diff_add", dark=dark)
    rem_hex = ref_color("diff_remove", dark=dark)

    # Agent-state glyph + color. WORKING pulses (heartbeat) between the resting
    # glyph and a hollow frame so the eye reads motion at a glance; static else.
    glyph, state_hex = _agent_glyph_color(agent_state, pulse_frame, dark=dark)

    text = Text(no_wrap=True, overflow="ellipsis")

    # Rows 1-2: identity (glyph · title · age · root / branch · agent · model|state).
    _append_identity(
        text,
        s,
        primary,
        promoted=promoted,
        now=now,
        glyph=glyph,
        state_label=agent_state_label(agent_state),
        state_hex=state_hex,
        branch_hex=branch_hex,
        agent_hex=agent_hex,
        muted_hex=muted_hex,
    )
    lines = 2

    # Row 3 (promoted, when present): the agent's own one-line summary — the
    # interpreted status when an LLM interpreter has set it (#20), else the
    # session ai-title, else the current task. Omitted (not blank-filled) when
    # absent so the pane tail simply claims one more row.
    if promoted:
        summary = _summary(primary)
        if summary:
            text.append("\n")
            text.append(_trim(summary, _TASK_TRIM), style=muted_hex)
            lines += 1

    # Stat row: ongoing-changes metadata (diff numstat · ahead/behind) plus the
    # transcript counts (turns/replies/tools) and, for promoted tiles, tokens.
    text.append("\n")
    _append_stats(
        text,
        activity,
        primary,
        promoted=promoted,
        add_hex=add_hex,
        rem_hex=rem_hex,
        muted_hex=muted_hex,
    )
    lines += 1

    # Promoted fill: a live, fit-to-cell tmux pane tail occupies the rest of the
    # tile. The screen captures it for every promoted tile (the focused tile at a
    # faster cadence); a tile not yet captured shows a quiet placeholder.
    if promoted:
        remaining = body_rows - lines
        if remaining > 0:
            text.append("\n")
            text.append_text(_pane_tail(pane_snapshot, remaining, muted_hex=muted_hex))

    return text


def _append_identity(
    text: Text,
    s: WorkspaceState,
    primary: AgentActivity | None,
    *,
    promoted: bool,
    now: datetime,
    glyph: str,
    state_label: str,
    state_hex: str,
    branch_hex: str,
    agent_hex: str,
    muted_hex: str,
) -> None:
    """Rows 1-2 of a tile: the identity block both shapes share.

    Row 1 is ``glyph · title · [state (promoted)] · age · [root tag]``; row 2 is
    ``branch · agent · (model on promoted, state on compact)``. Branch teal,
    agent cyan, state in its activity hue — the same who/what separation the list
    card uses. A root-placement workspace carries a quiet muted ``root`` tag.
    """
    text.append(f"{glyph} ", style=f"bold {state_hex}")
    text.append(_trim(s.title, _TITLE_TRIM), style="bold underline")
    if promoted:
        text.append("  ")
        text.append(state_label, style=f"bold {state_hex}")
    text.append("  ")
    text.append(humanize.naturaltime(now - s.updated_at), style=muted_hex)
    if s.placement is Placement.ROOT:
        text.append("  root", style=muted_hex)
    text.append("\n")
    text.append(_trim(s.branch, _BRANCH_TRIM), style=f"bold {branch_hex}")
    text.append(" · ", style=muted_hex)
    text.append(s.agent_name, style=f"bold {agent_hex}")
    if promoted and primary is not None and primary.model:
        text.append(" · ", style=muted_hex)
        text.append(primary.model, style=muted_hex)
    elif not promoted:
        text.append(" · ", style=muted_hex)
        text.append(state_label, style=f"bold {state_hex}")


def _summary(primary: AgentActivity | None) -> str | None:
    """The agent's own one-line summary, interpreter-first.

    Prefers ``interpreted_status`` — the slot a future external-LLM interpreter
    (#20) fills with a human one-liner — then the session ai-title, then the raw
    current task. ``None`` when a STARTING / generic session has surfaced none.
    """
    if primary is None:
        return None
    return primary.interpreted_status or primary.title or primary.current_task


def _append_stats(
    text: Text,
    activity: WorkspaceActivity,
    primary: AgentActivity | None,
    *,
    promoted: bool,
    add_hex: str,
    rem_hex: str,
    muted_hex: str,
) -> None:
    """Diff numstat · ahead/behind · turns/replies/tools · (promoted) tokens.

    Bold + semantic color on the values the eye lands on (added green, removed
    red); muted connectives and reference counts — the card-body tier rules.
    """
    text.append("+", style=add_hex)
    text.append(str(activity.diff_added), style=f"bold {add_hex}")
    text.append(" / ", style=muted_hex)
    text.append("-", style=rem_hex)
    text.append(str(activity.diff_removed), style=f"bold {rem_hex}")
    if activity.base_ahead or activity.base_behind:
        text.append(" · ", style=muted_hex)
        text.append(f"↑{activity.base_ahead} ↓{activity.base_behind}", style=muted_hex)
    if primary is not None and (
        primary.human_turns or primary.assistant_replies or primary.tool_calls
    ):
        text.append(" · ", style=muted_hex)
        text.append(
            f"{primary.human_turns}t {primary.assistant_replies}r {primary.tool_calls}⚒",
            style=muted_hex,
        )
    if promoted and primary is not None and (primary.tokens_in or primary.tokens_out):
        text.append(" · ", style=muted_hex)
        text.append(
            f"{_human_tokens(primary.tokens_in)}↑ {_human_tokens(primary.tokens_out)}↓",
            style=muted_hex,
        )


def _pane_tail(snapshot: str | None, max_lines: int, *, muted_hex: str) -> Text:
    """The agent's live tmux pane tail, SGR-decoded, background-stripped, fit to cell.

    Mirrors PeekRail: ``Text.from_ansi`` of the last ``max_lines`` rows, captured
    SGR backgrounds cleared (the agent terminal's own bg fights the tile's
    ``$surface``), ``no_wrap`` so wide lines crop rather than wrap. ``None`` (a
    promoted tile the screen hasn't captured yet) renders a quiet placeholder so
    the tile still reads as a framed live container.
    """
    if not snapshot:
        return Text("· · ·", style=muted_hex)
    tail = snapshot.splitlines()[-max_lines:]
    text = _strip_bg(Text.from_ansi("\n".join(tail)))
    text.no_wrap = True
    return text


def _agent_glyph_color(
    state: AgentActivityState, pulse_frame: int, *, dark: bool
) -> tuple[str, str]:
    """Resolve the line-1 glyph + color, applying the WORKING heartbeat.

    WORKING alternates the glyph between ``▶`` and ``▷`` on the pulse so the tile
    reads as live without a color change (the color stays the WORKING lime so
    the semantic is stable). Every other state is static.
    """
    base = agent_state_glyph(state)
    color = agent_state_color(state, dark=dark)
    if state == AgentActivityState.WORKING and pulse_frame % 2 == 1:
        return "▷", color
    return base, color


def _strip_bg(text: Text) -> Text:
    """Clear ``bgcolor`` from every styled span (same rationale as PeekRail)."""
    text.spans[:] = [
        Span(sp.start, sp.end, _without_bg(sp.style)) if isinstance(sp.style, Style) else sp
        for sp in text.spans
    ]
    return text


def _without_bg(style: Style) -> Style:
    return Style(
        color=style.color,
        bold=style.bold,
        dim=style.dim,
        italic=style.italic,
        underline=style.underline,
        blink=style.blink,
        reverse=style.reverse,
        strike=style.strike,
        link=style.link,
    )


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"
    return value[: limit - 1] + "…"


def _human_tokens(n: int) -> str:
    """Compact token count: 1234 → 1.2k, 1_200_000 → 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"
