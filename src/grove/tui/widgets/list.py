"""WorkspaceList — ListView of WorkspaceCard rows.

Public surface: `populate(states)`, `set_filter(query)`,
`filter_query`, `selected_id`, `states`, `visible_states`,
`jump_to(index)`. The list screen and Pilot tests consume only this
surface — internals (mount/unmount choreography, cursor restoration)
are private. Renaming any of the above breaks tests loudly, which is
the point.

Why ListView (not a hand-rolled VerticalScroll): Textual's ListView
extends VerticalScroll, manages cursor state, fires Highlighted/Selected
events on cursor moves and Enter, and ships TCSS hooks for the
`-highlight` class on the active ListItem. Reimplementing all of that
would be 100+ lines of churn for zero gain. (CLAUDE.md: proven library
for infrastructure, custom code only for business logic.)

Selection by id (the lifecycle contract) is preserved by mounting one
WorkspaceCard per visible state in `_rebuild`, then moving the cursor
back to whichever card carries the prior id. N is small (typically <30);
incremental diffs aren't worth the complexity, so each rebuild clears
and remounts.
"""

from __future__ import annotations

from textual.widgets import ListView

from grove.core import WorkspaceState
from grove.tui.widgets.card import WorkspaceCard


class WorkspaceList(ListView):
    """Read-only scrollable list of workspaces, one WorkspaceCard per row."""

    DEFAULT_CSS = """
    WorkspaceList {
        height: 1fr;
        background: $surface;
        border: round $secondary;
        border-title-color: $primary;
        border-title-align: left;
        padding: 0 1;
    }
    WorkspaceList:focus {
        border: round $primary;
    }
    /* Hover/highlight chrome: every card carries `border: round $surface`
     * (transparent against the list bg) by default. Highlighting swaps
     * the full border to `round $primary` (clay) so the focused row
     * becomes a fully framed panel — same lazygit "active panel keeps
     * the brand color" cue, applied per-row. The bg also tier-shifts
     * to $panel so the cue isn't carried by color alone (a11y). */
    WorkspaceList:focus > WorkspaceCard.-highlight {
        background: $panel;
        border: round $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._states: list[WorkspaceState] = []
        self._filter: str = ""

    def on_mount(self) -> None:
        # `border_title` is set after compose because Textual binds it on
        # the widget instance; doing it in __init__ would race with mount.
        self.border_title = "workspaces"

    # ─── public surface ───────────────────────────────────────────────────

    def populate(self, states: list[WorkspaceState]) -> None:
        """Cache `states` and rebuild visible cards under the current filter.

        Preserves the cursor by id when possible — the user shouldn't
        lose their place because someone else's lifecycle event ticked.
        """
        prior_id = self.selected_id
        self._states = list(states)
        self._rebuild(prior_id)

    def set_filter(self, query: str) -> None:
        """Apply a substring filter (case-insensitive). Empty string clears it."""
        self._filter = query.strip().lower()
        prior_id = self.selected_id
        self._rebuild(prior_id)

    @property
    def filter_query(self) -> str:
        return self._filter

    @property
    def selected_id(self) -> str | None:
        """Workspace id of the highlighted card, or None if empty."""
        idx = self.index
        visible = self._visible_states()
        if idx is None or not visible:
            return None
        if idx < 0 or idx >= len(visible):
            return None
        return visible[idx].id

    @property
    def states(self) -> list[WorkspaceState]:
        """The full state list (unfiltered)."""
        return list(self._states)

    @property
    def visible_states(self) -> list[WorkspaceState]:
        """The state list after applying the current filter."""
        return list(self._visible_states())

    def jump_to(self, index: int) -> None:
        """Move the cursor to the visible card at `index` (0-based)."""
        visible = self._visible_states()
        if 0 <= index < len(visible):
            self.index = index

    def set_pulse_frame(self, frame: int) -> None:
        """Push the live-signal pulse frame to every mounted WorkspaceCard.

        Owned by the parent screen's pulse clock (~4 Hz). The list is the
        natural owner of "all my visible cards" — keeping the iteration
        here means the screen stays decoupled from the card population.
        Non-ACTIVE cards short-circuit inside ``watch_pulse_frame``, so
        pushing to every card is cheap regardless of fleet shape.
        """
        for card in self.query(WorkspaceCard):
            card.pulse_frame = frame

    # ─── internal ─────────────────────────────────────────────────────────

    def _visible_states(self) -> list[WorkspaceState]:
        if not self._filter:
            return self._states
        q = self._filter
        return [
            s
            for s in self._states
            if q in s.title.lower() or q in s.branch.lower() or q in s.agent_name.lower()
        ]

    def _rebuild(self, prior_id: str | None) -> None:
        # ListView.clear() unmounts all children; we then re-mount one
        # WorkspaceCard per visible state. The cursor is restored by id
        # when possible, otherwise it lands at the first row (matching
        # the prior DataTable behavior).
        self.clear()
        visible = self._visible_states()
        for s in visible:
            self.append(WorkspaceCard(s))
        if not visible:
            return
        target = 0
        if prior_id is not None:
            for i, s in enumerate(visible):
                if s.id == prior_id:
                    target = i
                    break
        self.index = target
