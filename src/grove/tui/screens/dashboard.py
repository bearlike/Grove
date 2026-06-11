"""DashboardScreen — the cross-project Activity Dashboard (epic #11 §8, issue #16).

Where ``WorkspaceListScreen`` shows one repo's workspaces, this shows *every*
workspace across *every* repo as a wall of agent-activity tiles grouped by
project. It answers "what is every agent doing right now" at a glance: which
sessions are working, which are waiting for the human, which have stalled.

The data hub is the engine's ``ActivityService`` (consumed in-process, exactly
as the daemon consumes it over SSE). The screen owns the tick — the same
discipline as the list screen's peek rail — and never invents a new timer
pattern:

* **slow tick** (``cfg.peek_stats_refresh_seconds``, 3 s default) drives
  ``ActivityService.poll_once()``, which recomputes activity and emits a
  ``session_activity`` delta per changed workspace. Lifecycle changes
  (create/kill/pause/…) arrive promptly via the service's bridged manager bus —
  no waiting for the next poll.
* **fast tick** (``cfg.peek_pane_refresh_seconds``, 0.25 s default) advances the
  WORKING heartbeat pulse AND captures the *focused* tile's live pane. Only the
  focused live tile streams a pane — idle and unfocused tiles never do (epic
  acceptance: "idle panes don't stream").

Both ticks freeze while a modal sits on top (``app.screen is not self``), same
convention as the list screen.

Opened from the list screen on ``d``; ``escape`` / ``d`` / ``q`` pop back.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Final

from loguru import logger
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Header, Static

from grove.core import RepoRegistry, WorkspaceManager
from grove.core.activity import (
    ActivityService,
    DashboardDelta,
    DashboardSnapshot,
    WorkspaceActivity,
)
from grove.core.agents import AgentActivityState
from grove.tui._status import chrome_color
from grove.tui.widgets.dashboard_grid import DashboardCard, DashboardGrid, is_promoted
from grove.tui.widgets.footer import ContextualFooter, FooterKey

# Lens — which slice of the fleet the wall shows. "All" is the default: the whole
# point is "see every agent across every project at a glance", and a fleet with
# nothing yet needing attention must not open to an empty wall. From there the
# user narrows: "needs attention" surfaces the sessions that want the human,
# "active" shows the live ones. A closed set drives the cycle, so it's a tuple.
_Lens = str
_LENSES: Final[tuple[_Lens, ...]] = ("all", "attention", "active")
_LENS_LABEL: Final[dict[_Lens, str]] = {
    "attention": "needs attention",
    "active": "active",
    "all": "all",
}

# Agent states the "active" lens keeps — the ones with a live or pending signal.
_ACTIVE_STATES: Final[frozenset[AgentActivityState]] = frozenset(
    {
        AgentActivityState.STARTING,
        AgentActivityState.WORKING,
        AgentActivityState.WAITING,
        AgentActivityState.BLOCKED,
    }
)

# Footer keys for this screen — globals plus the lens/group toggles. Same
# data-driven shape as the list screen; every key here is always available
# (nothing is selection-gated on the dashboard).
_FOOTER_KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("d,escape", "Back"),
    ("l", "Lens"),
    ("g", "Group"),
    ("r", "Refresh"),
    ("q", "Quit"),
)

# Pulse cadence — reuse the list screen's 4 Hz live-signal budget.
_PULSE_TICK_SECONDS: Final = 0.25

# Live-pane capture budget per slow tick. Every promoted tile shows a fit-to-cell
# tmux tail; this caps how many tmux subprocesses one tick may spawn so a huge
# fleet can't stall the UI in a capture burst. The focused tile always wins a
# slot (it also re-captures on the fast tick); the rest are first-come and the
# overflow is logged, never silently dropped.
_MAX_LIVE_CAPTURES: Final = 12


class DashboardScreen(Screen[None]):
    """Cross-project activity wall: every workspace, grouped by project."""

    DEFAULT_CSS = """
    DashboardScreen #dashboard-body {
        height: 1fr;
        padding: 0 1;
    }
    DashboardScreen .project-header {
        height: 1;
        color: $primary;
        text-style: bold;
        padding: 0 1;
        margin-top: 1;
    }
    DashboardScreen #dashboard-empty {
        width: auto;
        height: auto;
        padding: 1 3;
        color: $text-muted;
        text-style: italic;
        text-align: center;
    }
    DashboardScreen.-empty #dashboard-body {
        align: center middle;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("d", "back", "Back", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Quit", show=False),
        Binding("l", "cycle_lens", "Lens", show=False),
        Binding("g", "toggle_group", "Group", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(
        self,
        manager: WorkspaceManager,
        *,
        service: ActivityService | None = None,
        registry: RepoRegistry | None = None,
    ) -> None:
        super().__init__()
        # The dashboard is cross-project: it reads through a RepoRegistry over
        # EVERY known repo, not just the manager's own repo. The manager only
        # supplies config + the shared store (and is the registry's cache for
        # its own repo). The screen keeps its OWN reference to the registry so
        # the focused-tile pane capture can resolve any repo's manager — the
        # service keeps its registry private. Tests inject a pre-built service +
        # registry over an in-memory store so the screen never touches the real
        # filesystem.
        self._manager = manager
        if registry is None:
            registry = RepoRegistry(cfg=manager.config, store=manager.store)
        self._registry = registry
        if service is None:
            service = ActivityService(registry=registry)
        self._service = service
        self._unsub: Callable[[], None] | None = None
        self._poll_timer: Timer | None = None
        self._pulse_timer: Timer | None = None
        self._pulse_frame: int = 0
        self._lens_index: int = 0
        self._group_by_project: bool = True
        # The id of the tile that should hold focus across a rebuild — preserves
        # the user's place when a delta re-renders the wall.
        self._focused_id: str | None = None
        # Last live-pane capture per workspace id. Keyed by id (not card) so it
        # survives a wall rebuild: a delta re-creates every DashboardCard, and
        # re-applying the cache after the rebuild is what stops the promoted
        # tiles from flashing blank between captures. Pruned to the promoted set.
        self._pane_cache: dict[str, str | None] = {}

    # ─── compose / lifecycle ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="dashboard-body")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.title = "Grove — Activity"
        self.sub_title = self._lens_subtitle()
        self._refresh_footer()
        self._render_snapshot(self._service.snapshot())
        # Stream changes: lifecycle events bridge in immediately; poll catches
        # in-place activity drift on the slow tick.
        self._unsub = self._service.subscribe(self._on_delta)
        cfg = self._manager.config.tmux
        self._poll_timer = self.set_interval(cfg.peek_stats_refresh_seconds, self._tick_poll)
        self._pulse_timer = self.set_interval(_PULSE_TICK_SECONDS, self._tick_pulse)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for attr in ("_poll_timer", "_pulse_timer"):
            timer: Timer | None = getattr(self, attr)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)

    # ─── actions ──────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._render_snapshot(self._service.snapshot())

    def action_cycle_lens(self) -> None:
        self._lens_index = (self._lens_index + 1) % len(_LENSES)
        self.sub_title = self._lens_subtitle()
        self._render_snapshot(self._service.snapshot())

    def action_toggle_group(self) -> None:
        self._group_by_project = not self._group_by_project
        self._render_snapshot(self._service.snapshot())

    # ─── delta bus + ticks ────────────────────────────────────────────────

    def _on_delta(self, delta: DashboardDelta) -> None:
        # A lifecycle wake-up (create/kill/…) or an in-place activity change.
        # ``workspace_changed`` carries no payload (re-fetch); ``session_activity``
        # carries the one changed row, but re-snapshotting is simplest and N is
        # small — the diff guard on each tile means an unchanged tile won't
        # repaint anyway.
        del delta
        if self.app.screen is not self:
            # Still re-render so the wall is fresh when the modal closes; just
            # don't fight the modal for focus.
            self._render_snapshot(self._service.snapshot())
            return
        self._render_snapshot(self._service.snapshot())

    def _tick_poll(self) -> None:
        if self.app.screen is not self:
            return
        # Refresh every promoted tile's live pane tail against the SETTLED card
        # set before poll_once — poll may emit a delta that rebuilds the wall,
        # after which _apply_pane_cache re-applies these snapshots to the new
        # cards (the cache is keyed by id, so it survives the rebuild).
        self._capture_promoted_panes()
        # poll_once emits deltas for changed workspaces; _on_delta re-renders.
        # No-op when nothing changed (fingerprint guard inside the service).
        self._service.poll_once()

    def _tick_pulse(self) -> None:
        """Advance the WORKING heartbeat and capture the focused tile's pane.

        Frozen on modal. The pulse only does work when a WORKING tile is
        visible; the pane capture only fires for the focused live tile, so an
        idle wall costs ~zero per tick.
        """
        if self.app.screen is not self:
            return
        if self._any_working():
            self._pulse_frame = (self._pulse_frame + 1) % 2
            for grid in self._grids():
                grid.set_pulse_frame(self._pulse_frame)
        self._capture_focused_pane()

    def _capture_focused_pane(self) -> None:
        """Re-capture the focused tile's live pane on the fast tick.

        Every promoted tile gets a pane on the slow tick; the focused tile also
        re-captures here (4 Hz) so the tile the user is watching is the most live
        thing on the wall. Best-effort and cached so a rebuild re-applies it. A
        focused *compact* tile (idle) clears its unused snapshot.
        """
        focused = self._focused_card()
        if focused is None:
            return
        if not is_promoted(focused.activity):
            self._pane_cache.pop(focused.workspace_id, None)
            focused.set_pane_snapshot(None)
            return
        snap = self._safe_capture(focused.activity)
        self._pane_cache[focused.workspace_id] = snap
        focused.set_pane_snapshot(snap)

    def _capture_promoted_panes(self) -> None:
        """Refresh the live pane tail of every promoted tile (bounded, best-effort).

        Caps at ``_MAX_LIVE_CAPTURES`` captures per tick (focused tile first) so a
        large fleet never spawns an unbounded tmux burst; the overflow is logged,
        not silently dropped. Snapshots cache by id for the next rebuild; the
        cache is pruned to the tiles still promoted so a finished agent's pane
        doesn't linger.
        """
        promoted = [c for c in self._all_cards() if is_promoted(c.activity)]
        keep = {c.workspace_id for c in promoted}
        self._pane_cache = {k: v for k, v in self._pane_cache.items() if k in keep}
        if not promoted:
            return
        focused = self._focused_card()
        ordered = sorted(promoted, key=lambda c: c is not focused)  # focused wins a slot
        for idx, card in enumerate(ordered):
            if idx >= _MAX_LIVE_CAPTURES:
                logger.debug(
                    "dashboard: live-pane capture capped at {} of {} promoted tiles",
                    _MAX_LIVE_CAPTURES,
                    len(ordered),
                )
                break
            snap = self._safe_capture(card.activity)
            self._pane_cache[card.workspace_id] = snap
            card.set_pane_snapshot(snap)

    def _apply_pane_cache(self) -> None:
        """Re-push cached pane snapshots after a wall rebuild (no new tmux calls)."""
        for card in self._all_cards():
            if is_promoted(card.activity):
                card.set_pane_snapshot(self._pane_cache.get(card.workspace_id))

    def _safe_capture(self, activity: WorkspaceActivity) -> str | None:
        """Capture one workspace's agent pane, best-effort (never raises).

        Resolves the owning repo's manager and runs the cheap tmux-only peek the
        rail uses; any failure (dead session, missing target) degrades to None.
        """
        if activity.pane_target is None:
            return None
        mgr = self._manager_for(activity.state.repo_root)
        try:
            snap, _ = mgr.peek_pane(activity.state.id)
        except Exception:
            return None
        return snap

    # ─── rendering ────────────────────────────────────────────────────────

    def _render_snapshot(self, snapshot: DashboardSnapshot) -> None:
        body = self.query_one("#dashboard-body", VerticalScroll)
        focused = self._focused_card()
        self._focused_id = focused.workspace_id if focused is not None else None
        body.remove_children()
        groups = self._filtered_groups(snapshot)
        total = sum(len(rows) for _, rows in groups)
        self.set_class(total == 0, "-empty")
        self._refresh_footer()
        if total == 0:
            body.mount(Static(self._empty_message(), id="dashboard-empty"))
            return
        dark = self.app.current_theme.dark
        for repo_name, rows in groups:
            if self._group_by_project:
                body.mount(
                    Static(
                        _project_header(repo_name, len(rows), dark=dark),
                        classes="project-header",
                    )
                )
            body.mount(DashboardGrid(rows))
        # Cards mount asynchronously (their grid's compose runs on the next
        # message-loop pass), so defer focus restoration AND pane-cache re-apply
        # until the mount settles — querying for cards synchronously here would
        # find none. Re-applying the cache is what keeps promoted tiles from
        # flashing blank when a delta rebuilds the wall.
        self.call_after_refresh(self._restore_focus)
        self.call_after_refresh(self._apply_pane_cache)

    def _filtered_groups(
        self, snapshot: DashboardSnapshot
    ) -> list[tuple[str, list[WorkspaceActivity]]]:
        """Project groups after applying the current lens.

        When grouping is off, everything collapses into one synthetic "all"
        group so the wall is a single flat grid. Empty groups are dropped so a
        lens that filters a whole project out doesn't leave a bare header.
        """
        lens = _LENSES[self._lens_index]
        out: list[tuple[str, list[WorkspaceActivity]]] = []
        if self._group_by_project:
            for group in snapshot.projects:
                rows = [w for w in group.workspaces if _passes_lens(w, lens)]
                if rows:
                    out.append((group.repo_name, rows))
            return out
        flat = [w for w in snapshot.iter_workspaces() if _passes_lens(w, lens)]
        if flat:
            out.append(("all workspaces", flat))
        return out

    def _restore_focus(self) -> None:
        cards = self._all_cards()
        if not cards:
            return
        target = cards[0]
        if self._focused_id is not None:
            for card in cards:
                if card.workspace_id == self._focused_id:
                    target = card
                    break
        target.focus()

    def _refresh_footer(self) -> None:
        keys = [FooterKey(k, label, available=True) for k, label in _FOOTER_KEYS]
        self.query_one(ContextualFooter).set_keys(keys)

    # ─── key handling ─────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        # Arrow + j/k move focus between tiles. ``focus_next`` / ``focus_previous``
        # walk the focus chain in DOM order, which is the tile order
        # (group-by-group), so the wall feels like a grid walk. ``l`` / ``h`` are
        # deliberately NOT bound here — ``l`` is the lens toggle, and stealing it
        # for navigation would shadow a labeled footer key. Tab also works (it's
        # Textual's native focus-next).
        if not self._all_cards():
            return
        if event.key in ("down", "right", "j"):
            self.focus_next()
            event.stop()
        elif event.key in ("up", "left", "k"):
            self.focus_previous()
            event.stop()

    # ─── internal ─────────────────────────────────────────────────────────

    def _any_working(self) -> bool:
        for card in self._all_cards():
            primary = card.activity.primary
            if primary is not None and primary.state == AgentActivityState.WORKING:
                return True
        return False

    def _grids(self) -> list[DashboardGrid]:
        return list(self.query(DashboardGrid))

    def _all_cards(self) -> list[DashboardCard]:
        return list(self.query(DashboardCard))

    def _focused_card(self) -> DashboardCard | None:
        for card in self._all_cards():
            if card.has_focus:
                return card
        return None

    def _manager_for(self, repo_root: str) -> WorkspaceManager:
        # RepoRegistry already caches one Manager per resolved repo_root, so a
        # bare get() is the right call — no second cache needed here.
        return self._registry.get(Path(repo_root))

    def _lens_subtitle(self) -> str:
        return f"lens: {_LENS_LABEL[_LENSES[self._lens_index]]}"

    def _empty_message(self) -> str:
        lens = _LENSES[self._lens_index]
        if lens == "all":
            return "no workspaces across any project — press [bold]d[/] to go back"
        label = _LENS_LABEL[lens]
        return f"no workspaces match the [bold]{label}[/] lens — press [bold]l[/] to widen"


def _passes_lens(activity: WorkspaceActivity, lens: _Lens) -> bool:
    if lens == "all":
        return True
    if lens == "attention":
        return activity.needs_attention
    # "active": any session in a live/pending agent state.
    primary = activity.primary
    return primary is not None and primary.state in _ACTIVE_STATES


def _project_header(repo_name: str, count: int, *, dark: bool) -> Text:
    text = Text()
    text.append(repo_name, style="bold")
    text.append(f"  ({count})", style=chrome_color("muted", dark=dark))
    return text
