"""WorkspaceCard — one row in the workspace list, rendered as a card.

Two lines of content per card, framed by a `round` border:

* line 1 — colored status glyph + bold-underlined title + muted age
* line 2 — branch (bold $ref teal) + agent (bold $ref-info cyan), an
  optional agent-activity segment (`<glyph> <label>`, bold + agent-state
  color — pushed by the list screen's slow tick, absent by default) +
  status label (bold + status color), an optional muted `root` tag when
  the workspace runs in the repo root (placement ROOT), then an optional
  bold-error `init failed` badge when init_status is FAILED.

Focus is carried by **TCSS chrome**, not glyphs in the rendered text:

* every card carries `border: round $surface` by default. `$surface`
  matches the parent list's background, so the border is read as
  transparent breathing room — *every* card has the same outer shape.
* `WorkspaceList:focus > WorkspaceCard.-highlight` swaps the border to
  `round $primary` (clay) so the focused row becomes a fully framed
  panel — same lazygit-style "active panel keeps the brand color" cue,
  applied per-row instead of per-panel.
* the highlighted card's background also swaps to `$panel` (configured
  on the parent `WorkspaceList`) so the focus cue isn't carried by color
  alone (a11y).

Pure render lives in `_render_card`; the `WorkspaceCard(ListItem)` widget
is a thin Static-wrapping shell that re-renders when its `state` reactive
changes. We do NOT re-render on `highlighted` — the visual change is
purely CSS (border swap + bg swap), so the body Text stays identical
regardless of focus and skipping the watcher avoids a per-cursor-move
repaint.

Card height is fixed at 4 rows (1 border-top + 2 content + 1 border-bottom).
Fixed height keeps scrolling jitter-free and means highlighting never
reflows the layout — only the two border colors swap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import humanize
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import ListItem, Static

from grove.core import InitStatus, WorkspaceState, WorkspaceStatus
from grove.core.agents import AgentActivityState
from grove.core.workspace import Placement
from grove.tui._status import (
    active_pulse,
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    chrome_color,
    init_status_color,
    ref_color,
    status_color,
    status_glyph,
    status_label,
)

# Title is trimmed if it exceeds this — keeps line 1 from wrapping on
# narrow terminals. Matches the trim PeekRail uses for commit subjects.
_TITLE_TRIM = 48


class WorkspaceCard(ListItem):
    """One workspace as a ListItem; body is a single Static rendered via Rich Text.

    The card never holds focusable children (no Input, no Button) because
    Textual's focus chain will steal the cursor from the parent ListView
    if a child becomes focused — see CLAUDE.md note on FilterBar focus.

    Focus chrome is TCSS-only: every card has `border: round $surface`
    (read as transparent against the list bg). When the parent
    `WorkspaceList:focus` AND this card has the `-highlight` class, the
    border swaps to `round $primary` (clay). The selector lives on
    `WorkspaceList` so the rule short-circuits when the list isn't
    focused (e.g. while the FilterBar input has focus, no card pretends
    to be the active selection).
    """

    DEFAULT_CSS = """
    WorkspaceCard {
        height: 4;
        padding: 0 1;
        border: round $surface;
    }
    /* Mouse hover preview — subtle $secondary outline so the user
     * sees mouse position even when a different row is keyboard-
     * highlighted. Textual's default ListItem :hover uses $boost,
     * which is always transparent on Grove themes (CLAUDE.md $boost
     * lesson) — so explicit hover chrome is required for any visible
     * feedback. The list-scoped highlight rule
     * `WorkspaceList:focus > WorkspaceCard.-highlight` is more
     * specific and out-ranks this, so hovering the selected row keeps
     * its clay chrome rather than degrading to gray. */
    WorkspaceCard:hover {
        border: round $secondary;
    }
    """

    state: reactive[WorkspaceState | None] = reactive(None, layout=False)
    # Pulse clock pushed by the parent screen at 4 Hz. Default 0 = resting
    # frame (filled ●, full active green) so cards mounted between ticks
    # — or in tests with no clock at all — render the canonical static
    # appearance. Watcher is gated on ACTIVE so non-live cards skip the
    # repaint entirely. layout=False because the glyph swap never reflows.
    pulse_frame: reactive[int] = reactive(0, layout=False)

    def __init__(self, state: WorkspaceState) -> None:
        super().__init__()
        # Stash on the instance so the body refresh after mount has access
        # without going through the reactive (which fires the watcher and
        # would double-render before mount completes).
        self._state = state
        # Agent-activity axis (what the session is *doing*), pushed by the
        # parent screen's slow stats tick via `set_agent_state`. A plain
        # attribute, NOT a reactive: the slow tick is the only writer, so a
        # watcher buys nothing — `_refresh_body`'s plain-text diff guard
        # already absorbs the no-op pushes. None (the default) renders no
        # agent-state segment at all.
        self._agent_state: AgentActivityState | None = None
        self._last_plain: str = ""

    def compose(self) -> ComposeResult:
        yield Static(self._initial_body(), id="card-body")

    def on_mount(self) -> None:
        # Reactive set drives the watcher chain so subsequent populate()
        # calls in the parent list refresh the body identically to mount.
        self.state = self._state

    def watch_state(self, new_state: WorkspaceState | None) -> None:
        if new_state is None:
            return
        self._state = new_state
        self._refresh_body()

    def watch_pulse_frame(self, _frame: int) -> None:
        # ACTIVE is the only status that reads the pulse — every other row
        # renders identical bytes per frame and the existing diff guard
        # would short-circuit the repaint anyway. Gating here saves the
        # render call itself for the common case of an idle/paused fleet.
        if self._state is not None and self._state.status == WorkspaceStatus.ACTIVE:
            self._refresh_body()

    def set_agent_state(self, state: AgentActivityState | None) -> None:
        """Set (or clear, with ``None``) the agent-activity state segment.

        Pushed by the list screen's slow stats tick (~3 s) for visible rows.
        The diff guard in ``_refresh_body`` short-circuits when nothing
        visible changed, so re-pushing the same state per tick is cheap.
        """
        self._agent_state = state
        self._refresh_body()

    @property
    def workspace_id(self) -> str:
        return self._state.id

    @property
    def body_text(self) -> str:
        """Plain text of the rendered card body. Stable seam for tests."""
        return self._last_plain

    # ─── private ──────────────────────────────────────────────────────────

    def _initial_body(self) -> Text:
        text = _render_card(
            self._state,
            dark=self._dark(),
            pulse_frame=self.pulse_frame,
            agent_state=self._agent_state,
        )
        self._last_plain = text.plain
        return text

    def _refresh_body(self) -> None:
        try:
            body = self.query_one("#card-body", Static)
        except Exception:
            return  # not mounted yet
        text = _render_card(
            self._state,
            dark=self._dark(),
            pulse_frame=self.pulse_frame,
            agent_state=self._agent_state,
        )
        # Diff guard on plain text alone misses the pulse: frame 0 and frame
        # 1 of an ACTIVE card share `●…active`'s plain form (only the glyph
        # and a span color differ, and `◉` ≠ `●` so plain DOES change). For
        # non-ACTIVE rows the bytes are identical across frames and the
        # short-circuit fires as intended.
        if text.plain == self._last_plain:
            return
        self._last_plain = text.plain
        body.update(text)

    def _dark(self) -> bool:
        # `app` is None during pure-construction tests; fall back to dark
        # which matches the default registered theme.
        try:
            return bool(self.app.current_theme.dark)
        except Exception:
            return True


def _render_card(
    state: WorkspaceState,
    *,
    dark: bool,
    now: datetime | None = None,
    pulse_frame: int = 0,
    agent_state: AgentActivityState | None = None,
) -> Text:
    """Render one workspace row as Rich `Text`.

    Pure: identical for highlighted and unhighlighted cards. Focus is
    indicated entirely by CSS (full-border swap + bg swap on the parent
    `WorkspaceList:focus > .-highlight` rule), so this helper has no
    `focused` argument. Adding one would re-introduce the dual-source-of-
    truth we just retired (and force a repaint per cursor move).

    Color language inside the card body — every visible element earns its
    own slot from `grove.tui.theme`, never literal hex:

    * **status glyph** (line 1) — `status_color`, bold. The first thing
      the eye lands on; encodes lifecycle state at a glance.
    * **title** — bold + underline (default fg). Underlines mark this as
      the row's identity; same affordance as a hyperlink in IDE files
      lists, which trains the user's "this is the thing" reflex.
    * **age** — `chrome_color('muted')`. Recedes; only matters on scan.
    * **branch** (line 2) — bold + `ref_color('branch')` (teal). Primary
      anchor when scanning a list of workspaces by feature/task.
    * **agent** — bold + `ref_color('info')` (cyan). Distinct hue from
      branch so the eye separates "what" (branch) from "who" (agent)
      without re-reading the labels.
    * **agent state** (optional) — bold + `agent_state_color`. The agent
      axis ("what the session is doing"), rendered only when the screen's
      activity tick has resolved a session; `None` renders nothing.
    * **status label** — bold + `status_color`. Matches the line-1 glyph
      so the same color reads twice — reinforces the lifecycle cue.
    * **init failed** — bold + `init_status_color(FAILED)`. Bold red so
      a broken init reads as the most urgent thing on the row.
    * **`·` separators** — muted; recede so groups read as groups.

    `now` defaults to `datetime.now(tz=UTC)` and exists for testability —
    callers in tests pass a fixed `now` so age formatting is stable.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    # ACTIVE rows pulse: a screen-level clock pushes `pulse_frame` and the
    # leading glyph + its color swap between the resting state (●, full
    # active green) and the swelled state (◉, mint-tinted). All other
    # statuses ignore the pulse — `active_pulse` is a no-op for them.
    if state.status == WorkspaceStatus.ACTIVE:
        glyph, s_color = active_pulse(pulse_frame, dark=dark)
    else:
        glyph = status_glyph(state.status)
        s_color = status_color(state.status, dark=dark)
    branch_hex = ref_color("branch", dark=dark)
    agent_hex = ref_color("info", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    fail_hex = init_status_color(InitStatus.FAILED, dark=dark)

    # no_wrap: the card's `height: 4` leaves exactly two content rows; a line 2
    # that soft-wraps (long branch + agent + the agent-state segment on a narrow
    # terminal) would push into a phantom third row the border clips. Crop with
    # an ellipsis instead — the same trade the peek rail makes for pane width.
    text = Text(no_wrap=True, overflow="ellipsis")

    # Line 1: status glyph (colored) + underlined title + muted "·" + muted age.
    text.append(f"{glyph} ", style=f"bold {s_color}")
    title = state.title if len(state.title) <= _TITLE_TRIM else state.title[: _TITLE_TRIM - 1] + "…"
    text.append(title, style="bold underline")
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(humanize.naturaltime(now - state.updated_at), style=muted_hex)
    text.append("\n")

    # Line 2: branch · agent · status. Each token gets its own semantic
    # color so the row reads as three separate facts in three separate
    # tiers, rather than a wall of identical-weight tokens.
    text.append(state.branch, style=f"bold {branch_hex}")
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(state.agent_name, style=f"bold {agent_hex}")
    # Agent-activity segment: what the session is *doing* (WORKING / WAITING /
    # BLOCKED / …) — a separate axis from the workspace lifecycle status, so
    # it earns its own glyph + hue right after the "who" (agent name). Bold +
    # agent-state color: same typography tier as the status label. None (no
    # session, or the activity tick hasn't run yet) renders nothing — absence
    # is the default, same convention as the `root` tag below.
    if agent_state is not None:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append(
            f"{agent_state_glyph(agent_state)} {agent_state_label(agent_state)}",
            style=f"bold {agent_state_color(agent_state, dark=dark)}",
        )
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(status_label(state.status), style=f"bold {s_color}")
    # Root tag: a muted "root" marks a workspace that runs in the repo root
    # (no dedicated worktree). Muted + lowercase keeps it as a quiet
    # qualifier — the lifecycle status stays the line's loudest token, the
    # tag just tells the user this one has no isolated worktree. Worktree
    # workspaces render nothing here, so the absence is the default.
    if state.placement is Placement.ROOT:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("root", style=muted_hex)
    if state.init_status == InitStatus.FAILED:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("! init failed", style=f"bold {fail_hex}")

    # No trailing newline — the card's `height: 4` is exactly
    # `border-top + 2 content rows + border-bottom`. A trailing `\n`
    # would push line 2 into a phantom third content row that the
    # border would clip.
    return text
