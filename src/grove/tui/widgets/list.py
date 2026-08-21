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

Selection by id (the lifecycle contract) is preserved by *reconciling*
mounted cards against the visible states (`_reconcile`): a surviving id
keeps its existing WorkspaceCard and only gets a new `state` pushed into
it, so a tick where nothing changed mounts nothing, unmounts nothing,
and never writes the cursor.

This is deliberately NOT "clear and remount, it's only ~30 rows": `populate`
runs on the screen's slow stats tick forever (out-of-band creates/kills must
appear without a restart), so a full teardown per tick would blink the whole
list and lose the selected row's highlight and scroll position every few
seconds on the product's primary surface. The cost is never the widget
count; it is that a teardown on a *timer* runs whether or not anything
changed. Restoring the cursor afterwards would be a symptom, not a fix.

There is no rebuild path left, deliberately. Reordering does NOT justify
one: rows are sorted by `(status rank, -updated_at)` and ACTIVE outranks
IDLE, so the product's most frequent transition reorders the list, and a
rebuild-on-reorder would restore the flicker precisely on a busy fleet.
Cards are moved (`Widget.move_child`), never destroyed.
"""

from __future__ import annotations

from textual.widgets import ListView

from grove.core import WorkspaceState
from grove.core.agents import AgentActivityState
from grove.core.phase import PhaseReport
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
        """Cache `states` and reconcile the visible cards under the current filter.

        Called on the screen's slow stats tick forever, so the no-change
        case must be inert: identical states leave every card mounted and
        the cursor untouched (see `_reconcile`).
        """
        prior_id = self.selected_id
        self._states = list(states)
        self._reconcile(prior_id)

    def set_filter(self, query: str) -> None:
        """Apply a substring filter (case-insensitive). Empty string clears it.

        Same reconcile as `populate` — a filter narrows or widens the
        *membership* of an order-preserving list, which is exactly what the
        reconcile expresses. Typing into the filter therefore keeps the
        still-matching rows mounted instead of remounting the list per
        keystroke; nothing here needs a rebuild.
        """
        self._filter = query.strip().lower()
        prior_id = self.selected_id
        self._reconcile(prior_id)

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

    def set_agent_states(self, states: dict[str, AgentActivityState]) -> None:
        """Push the agent-activity axis onto every mounted WorkspaceCard.

        Owned by the parent screen's slow stats tick (~3 s) — same
        ownership split as ``set_pulse_frame``: the list iterates its own
        cards so the screen stays decoupled from the card population. An
        id missing from ``states`` maps to ``None`` (no live session),
        which clears any stale segment on that card.
        """
        for card in self.query(WorkspaceCard):
            card.set_agent_state(states.get(card.workspace_id))

    def set_phases(self, phases: dict[str, PhaseReport]) -> None:
        """Push the task-phase axis onto every mounted WorkspaceCard.

        The exact shape and ownership of :meth:`set_agent_states` — same tick,
        same iterate-my-own-cards split, and an id missing from *phases* maps
        to ``None``, which clears a stale segment rather than freezing it.

        Absence is load-bearing here in a way it is not for the agent axis: a
        workspace whose agent never reported has no claim, and rendering one
        would assert progress on work nobody said anything about.
        """
        for card in self.query(WorkspaceCard):
            card.set_phase(phases.get(card.workspace_id))

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

    def _reconcile(self, prior_id: str | None) -> None:
        """Bring the mounted cards in line with the visible states, in place.

        No card is ever destroyed to express a change. Four treatments, and
        *nothing at all* happens when nothing changed, which is the point —
        this runs on a timer:

        * a **surviving** id keeps its card; the fresh state is pushed into
          the existing widget, where `WorkspaceCard`'s plain-text diff guard
          absorbs it when the render is unchanged. Same in-place channel
          `set_agent_states` / `set_pulse_frame` already use every tick.
        * a **departed** id is unmounted via `ListView.remove_items`.
        * a **new** id is mounted before the first surviving card that follows
          it in target order — a widget anchor, not an integer index, because
          removals land asynchronously and would invalidate indices.
        * a **reordered** id is *moved*, never remounted (`_apply_order`).

        Reordering is the hot path, not an exotic one: `manager.list()` sorts
        by `(status rank, -updated_at)` and ACTIVE outranks IDLE, so every
        ACTIVE↔IDLE flip — the most frequent transition in the product, an
        agent going quiet and speaking again — moves a row, as does any write
        that bumps `updated_at`. Rebuilding on a reorder would put the flicker
        back exactly when the fleet is busy, which is when someone is watching.

        Mounts and moves shift rows without touching `index`, so the cursor is
        re-derived once at the end. The highlighted widget never loses its
        `-highlight` class along the way; it is only re-pointed at.
        """
        visible = self._visible_states()
        mounted = list(self.query(WorkspaceCard))
        by_id = {card.workspace_id: card for card in mounted}
        target_ids = [s.id for s in visible]
        target_set = set(target_ids)

        for state in visible:
            card = by_id.get(state.id)
            if card is not None:
                card.state = state

        if [card.workspace_id for card in mounted] == target_ids:
            return  # nothing mounted, unmounted, moved, or re-pointed

        departed = [i for i, card in enumerate(mounted) if card.workspace_id not in target_set]
        if departed:
            self.remove_items(departed)

        for i, state in enumerate(visible):
            if state.id in by_id:
                continue
            card = WorkspaceCard(state)
            anchor = next((by_id[wid] for wid in target_ids[i + 1 :] if wid in by_id), None)
            if anchor is None:
                self.append(card)
            else:
                self.mount(card, before=anchor)
            by_id[state.id] = card

        self._apply_order(target_ids)

        if departed:
            # `remove_items` fixes `index` from inside a coroutine, so a tick
            # that also removed rows has to let that settle before the cursor
            # is re-derived — otherwise the two corrections stack.
            self.call_after_refresh(self._restore_cursor, prior_id)
        else:
            self._restore_cursor(prior_id)

    def _apply_order(self, target_ids: list[str]) -> None:
        """Move mounted cards into `target_ids` order, minimally, without remounting.

        Only the cards that genuinely have to move do: everything in the
        longest run already in relative target order stays put, which is the
        provable minimum (`_stationary_ids`). Walking forward, the card at
        `i - 1` is already final — stationary, or moved on an earlier pass —
        so it is a safe anchor; position 0 anchors on the first child instead.

        Anchors are widgets, never indices: a card removed earlier this tick
        is still in the DOM (removal is async) and would throw the numbers off,
        but it can only sit *between* survivors, so their relative order — all
        `move_child` cares about — stays right.
        """
        wanted = set(target_ids)
        placed = [card for card in self.query(WorkspaceCard) if card.workspace_id in wanted]
        current = [card.workspace_id for card in placed]
        if current == target_ids:
            return
        cards = {card.workspace_id: card for card in placed}
        stationary = self._stationary_ids(current, target_ids)
        for i, wid in enumerate(target_ids):
            if wid in stationary:
                continue
            if i:
                self.move_child(cards[wid], after=cards[target_ids[i - 1]])
            else:
                self.move_child(cards[wid], before=self.children[0])

    @staticmethod
    def _stationary_ids(current: list[str], target: list[str]) -> set[str]:
        """The ids that can stay where they are — the longest already-ordered run.

        Any two ids inside a longest-increasing subsequence of the target
        positions are already correctly ordered relative to each other, and
        `len(current) - len(run)` is the minimum number of moves. O(n²) because
        n is the visible row count (<30) and the quadratic form is the readable
        one; patience sorting would buy nothing at this size.
        """
        rank = {wid: i for i, wid in enumerate(target)}
        seq = [rank[wid] for wid in current]
        if not seq:
            return set()
        lengths = [1] * len(seq)
        prior = [-1] * len(seq)
        for i in range(len(seq)):
            for j in range(i):
                if seq[j] < seq[i] and lengths[j] + 1 > lengths[i]:
                    lengths[i] = lengths[j] + 1
                    prior[i] = j
        stationary: set[str] = set()
        cursor = lengths.index(max(lengths))
        while cursor != -1:
            stationary.add(current[cursor])
            cursor = prior[cursor]
        return stationary

    def _restore_cursor(self, prior_id: str | None) -> None:
        """Point the cursor back at `prior_id`, or at the first row if it's gone."""
        visible = self._visible_states()
        if not visible:
            return
        target = 0
        if prior_id is not None:
            for i, state in enumerate(visible):
                if state.id == prior_id:
                    target = i
                    break
        self.index = target
