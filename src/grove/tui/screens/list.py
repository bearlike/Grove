"""WorkspaceListScreen — main screen.

Layout: a horizontal split with the workspace table on the left and a
`PeekRail` on the right. Selection drives a debounced peek recompute
(80 ms) so cursor scrubbing stays responsive without firing a subprocess
fan-out per keystroke. The rail collapses (display: none) below
`NARROW_THRESHOLD` columns; the screen also flips into an empty-state
view when no workspaces exist.

Two refresh cadences keep the rail live without burning resources:
* fast tick (`cfg.peek_pane_refresh_seconds`, default 0.25 s) — `peek_pane`
  only, splices the fresh snapshot into the cached full peek;
* slow tick (`cfg.peek_stats_refresh_seconds`, default 3 s) — full `peek`
  (git ahead/behind/diff/dirty), refreshes the cache, and recomputes the
  agent-activity axis for the visible rows (cards + rail metrics line).
Both ticks are frozen when a modal is on top of us. Number keys 1-9 jump
cursor.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace as _dc_replace
from typing import ClassVar

from loguru import logger
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Header, Input, ListView, Static

from grove.core import (
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    CreateWorkspaceRequest,
    GroveError,
    RepoRegistry,
    UpdateWorkspaceRequest,
    WorkspaceEvent,
    WorkspaceManager,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)
from grove.core.activity import ActivityService
from grove.core.agents import AgentActivity
from grove.core.workspace import LIVE_STATUSES, Placement
from grove.tui._status import ACTIVE_PULSE_FRAMES
from grove.tui.keys import (
    DEFAULT_BINDINGS,
    LIST_GLOBAL_FOOTER_KEYS,
    LIST_SELECTION_FOOTER_KEYS,
)
from grove.tui.screens.confirm import ConfirmScreen, KillConfirmScreen, KillDecision
from grove.tui.screens.create import CreateWorkspaceScreen
from grove.tui.screens.dashboard import DashboardScreen
from grove.tui.screens.edit import EditWorkspaceScreen
from grove.tui.screens.help import HelpScreen
from grove.tui.widgets.filter_bar import FilterBar
from grove.tui.widgets.footer import ContextualFooter, FooterKey
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.peek_rail import PeekRail
from grove.tui.widgets.status import FlashLevel, StatusBar

_PEEK_DEBOUNCE_SECONDS = 0.08

# Live-signal pulse cadence. 4 Hz — same budget as the existing peek-pane
# fast tick — gives a full ●→◉→● cycle every 0.5 s, which is fast enough
# to read as motion at a glance but slow enough to stay legible. Per-tick
# work is one int increment plus N widget refreshes guarded by a `status
# == ACTIVE` watcher; non-ACTIVE rows skip entirely. When no visible row
# is ACTIVE the tick early-exits and CPU is zero.
_PULSE_TICK_SECONDS = 0.25


class WorkspaceListScreen(Screen[None]):
    """Repo-scoped workspace list with full lifecycle bindings + peek rail."""

    DEFAULT_CSS = """
    WorkspaceListScreen #main {
        height: 1fr;
    }
    /* Outer canvas frame on the left column so WorkspaceList reads as
     * an inset panel on canvas, mirroring PeekRail's `padding: 0 1`.
     * Without this, the left column fills edge-to-edge while the rail
     * shows canvas around its cards — and the screen reads as two
     * unrelated visual languages instead of "panels on canvas". */
    WorkspaceListScreen #left-col {
        padding-left: 1;
    }
    WorkspaceListScreen #empty-wrap {
        display: none;
        height: 1fr;
        align: center middle;
    }
    WorkspaceListScreen #empty-banner {
        width: auto;
        height: auto;
        padding: 1 3;
        color: $text-muted;
        text-style: italic;
        text-align: center;
    }
    WorkspaceListScreen.-empty WorkspaceList {
        display: none;
    }
    WorkspaceListScreen.-empty #empty-wrap {
        display: block;
    }
    WorkspaceListScreen.-narrow PeekRail {
        display: none;
    }
    WorkspaceListScreen.-narrow WorkspaceList {
        width: 1fr;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        *(Binding(*b) for b in DEFAULT_BINDINGS),
        # Hidden numeric jumps. show=False keeps them out of the footer.
        *(Binding(str(n), f"jump_to({n})", f"jump {n}", show=False) for n in range(1, 10)),
    ]

    NARROW_THRESHOLD: ClassVar[int] = 100

    def __init__(
        self,
        manager: WorkspaceManager,
        *,
        service: ActivityService | None = None,
        registry: RepoRegistry | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        # Agent-activity machinery — same construction pattern as
        # DashboardScreen: built once here from the manager's config + shared
        # store unless a test injects pre-built fakes. The list screen only
        # ever reads its own repo through it (`sessions_for(self._manager, …)`),
        # but sharing the service keeps the blend + hook-sidecar policy in the
        # engine's single site instead of re-implementing it TUI-side.
        if service is None:
            if registry is None:
                registry = RepoRegistry(cfg=manager.config, store=manager.store)
            service = ActivityService(registry=registry)
        self._service = service
        # Primary AgentActivity per workspace id, recomputed by the slow
        # stats tick for the *visible* rows. Cards take the state enum; the
        # peek rail takes the selected row's full activity (metrics line).
        self._agent_activity: dict[str, AgentActivity] = {}
        self._unsub: Callable[[], None] | None = None
        self._peek_timer: Timer | None = None
        self._stats_timer: Timer | None = None
        self._pane_timer: Timer | None = None
        self._pulse_timer: Timer | None = None
        # Live-signal pulse frame; advances on each `_tick_pulse` and is
        # pushed into every WorkspaceCard plus the StatusBar's
        # selection-summary slot so they swell in lockstep.
        self._pulse_frame: int = 0
        # Most-recent full peek of the currently selected workspace. The
        # fast pane tick splices fresh snapshots into this without redoing
        # the git work. Invalidated on selection change and rebuilt by the
        # next slow stats tick (or on the debounced selection-change tick).
        self._cached_peek: WorkspacePeek | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield FilterBar()
            with Horizontal(id="main"):
                with Vertical(id="left-col"):
                    yield WorkspaceList()
                    with Vertical(id="empty-wrap"):
                        yield Static(
                            "no workspaces yet — press [bold]n[/] to create one",
                            id="empty-banner",
                            classes="grove-card",
                        )
                yield PeekRail()
            yield StatusBar(self._manager.repo_root)
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.title = "Grove"
        self.sub_title = self._manager.repo_root.name
        self._refresh()
        self._refresh_peek()
        self._refresh_footer()
        # Default focus on the table so global hotkeys (r, n, k, …) don't get
        # eaten by the FilterBar input — the bar sits earlier in the DOM.
        self.query_one(WorkspaceList).focus()
        # Re-render whenever the manager fires a lifecycle event.
        self._unsub = self._manager.subscribe(self._on_manager_event)
        cfg = self._manager.config.tmux
        self._stats_timer = self.set_interval(cfg.peek_stats_refresh_seconds, self._tick_stats)
        self._pane_timer = self.set_interval(cfg.peek_pane_refresh_seconds, self._tick_pane)
        self._pulse_timer = self.set_interval(_PULSE_TICK_SECONDS, self._tick_pulse)

    def on_unmount(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._cancel_peek_timer()
        for attr in ("_stats_timer", "_pane_timer", "_pulse_timer"):
            timer: Timer | None = getattr(self, attr)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)

    # Width-responsive: hide the peek rail on narrow terminals so the table
    # can use the full width. Threshold is conservative; users can resize up.
    def on_resize(self, event: events.Resize) -> None:
        del event
        if self.size.width < self.NARROW_THRESHOLD:
            self.add_class("-narrow")
        else:
            self.remove_class("-narrow")

    # ─── action handlers ──────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.app.exit()

    def action_refresh(self) -> None:
        self._refresh()
        self._refresh_peek()

    def action_help(self) -> None:
        self.app.push_screen(
            HelpScreen(
                DEFAULT_BINDINGS,
                has_selection=self._selected_id() is not None,
            )
        )

    def action_open_dashboard(self) -> None:
        """Open the cross-project Activity Dashboard.

        The dashboard reads through a ``RepoRegistry`` over every known repo
        (not just this screen's repo), built from this manager's shared config +
        store. A fresh ``DashboardScreen`` is pushed each time — it owns its own
        ticks and tears them down on unmount, so re-opening is cheap and never
        leaks a timer.
        """
        self.app.push_screen(DashboardScreen(self._manager))

    def action_focus_filter(self) -> None:
        bar = self.query_one(FilterBar)
        bar.add_class("-active")
        bar.focus()

    def action_jump_to(self, index: int) -> None:
        # 1-based input → 0-based table index.
        self.query_one(WorkspaceList).jump_to(index - 1)

    def action_new_workspace(self) -> None:
        # Branch read helpers populate the modal's dropdowns (Existing /
        # Remote / Base). Eager fetch at modal-open time — two subprocess
        # calls that finish well under the user's perception threshold;
        # if a slow repo ever makes this perceptible we'll move them
        # into a worker thread or expose a Refresh action. For now the
        # eager path is what the test seam expects.
        try:
            local = self._manager.list_local_branches()
            remote = self._manager.list_remote_branches()
            default_base = self._manager.default_branch()
        except Exception as exc:
            logger.warning("could not enumerate branches for create modal: {}", exc)
            local = remote = ()
            default_base = "HEAD"
        screen = CreateWorkspaceScreen(
            self._manager.config.agents,
            cfg=self._manager.config,
            repo_root=self._manager.repo_root,
            local_branches=local,
            remote_branches=remote,
            default_base=default_base,
        )
        self.app.push_screen(screen, self._handle_create_result)

    def action_edit_workspace(self) -> None:
        """Open the edit modal for the selected workspace.

        Refuses (with a flash) when the selected workspace is ORPHANED —
        the engine's ``ensure_can_update`` enforces this too, but the
        flash is the user-visible nudge so the modal doesn't pop just to
        fail on submit.
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        try:
            state = self._manager.get(wid)
        except GroveError as exc:
            self._flash(f"edit failed: {exc}", level="error")
            return
        if state.status == WorkspaceStatus.ORPHANED:
            self._flash("cannot edit an orphaned workspace")
            return
        screen = EditWorkspaceScreen(
            current_title=state.title,
            current_description=state.description,
        )
        self.app.push_screen(screen, self._handle_edit_result)

    def _handle_edit_result(self, request: UpdateWorkspaceRequest | None) -> None:
        if request is None:
            return
        wid = self._selected_id()
        if wid is None:
            return
        # UpdateWorkspaceRequest's None on a field means "do not change";
        # the manager accepts the same convention via its _UNSET sentinel,
        # so we forward only the fields the user actually populated.
        kwargs: dict[str, str] = {}
        if request.title is not None:
            kwargs["title"] = request.title
        if request.description is not None:
            kwargs["description"] = request.description
        self._safe_call("edit", lambda: self._manager.update(wid, **kwargs))

    def action_pause_workspace(self) -> None:
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return

        def _on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._safe_call("pause", lambda: self._manager.pause(wid))

        peek = self._safe_peek(wid)
        self.app.push_screen(
            ConfirmScreen(
                "Pause this workspace? (worktree removed; branch retained)",
                title="Pause",
                details=_pause_details(peek),
            ),
            _on_confirm,
        )

    def action_resume_workspace(self) -> None:
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        self._safe_call("resume", lambda: self._manager.resume(wid))

    def action_respawn_workspace(self) -> None:
        """Recreate the tmux session for an OFFLINE workspace.

        Refuses (with a flash) when the selected workspace is in any other
        status. Manager-side ``ensure_can_respawn`` enforces this too — the
        flash is the user-visible nudge before the GroveError surfaces.
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        peek = self._safe_peek(wid)
        if peek is not None and peek.state.status != WorkspaceStatus.OFFLINE:
            self._flash("respawn applies only to offline workspaces")
            return
        self._safe_call("respawn", lambda: self._manager.respawn(wid))

    def action_kill_workspace(self) -> None:
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return

        def _on_decision(decision: KillDecision | None) -> None:
            if decision is None or not decision.confirmed:
                return
            self._safe_call(
                "kill",
                lambda: self._manager.kill(wid, delete_branch=decision.delete_branch),
            )

        peek = self._safe_peek(wid)
        state = peek.state if peek is not None else self._manager.get(wid)
        message = "Kill this workspace? The worktree and tmux session will be removed."
        self.app.push_screen(
            KillConfirmScreen(
                message,
                branch_name=state.branch,
                branch_provenance=state.branch_provenance,
                title="Kill",
                details=_kill_details(peek),
            ),
            _on_decision,
        )

    def action_attach_workspace(self) -> None:
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        try:
            instr = self._manager.attach(wid)
        except GroveError as exc:
            self._flash(f"attach failed: {exc}")
            return
        target = instr.tmux_session
        cols, rows = _attach_dimensions(instr.inside_outer_tmux)
        if instr.inside_outer_tmux:
            # Inside outer tmux — switch the existing client; Grove keeps running.
            subprocess.run(["tmux", "switch-client", "-t", target], check=False)
            # Resize the workspace's window to OUR client's terminal size.
            # We pass explicit dimensions (not `-a`/`-A`) because sessions
            # imported from claude-squad have `window-size manual` set AND
            # may have other clients viewing at smaller sizes — `-A` would
            # pick the smaller one, leaving Grove's user with a dotted gap.
            # Explicit dimensions guarantee the attaching user sees their
            # full terminal. Other viewers get cropped, which is the
            # expected trade-off for an attach (active user wins).
            if cols and rows:
                subprocess.run(
                    ["tmux", "resize-window", "-t", target, "-x", cols, "-y", rows],
                    check=False,
                )
        else:
            # Not in tmux — suspend Textual, attach, resume after detach.
            # Pre-resize for the same reason as above (sessions with
            # `window-size manual` won't auto-fit on attach).
            if cols and rows:
                subprocess.run(
                    ["tmux", "resize-window", "-t", target, "-x", cols, "-y", rows],
                    check=False,
                )
            with self.app.suspend():
                subprocess.run(["tmux", "attach", "-t", target], check=False)

    # ─── selection-driven rail recompute ─────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Cursor moved to a different card — coalesce rapid moves into one
        # peek, refresh the footer so selection-only keys re-enable, and
        # push the new selection into the StatusBar's selection-summary.
        del event
        self._schedule_peek()
        self._refresh_footer()
        self._refresh_status_bar()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Enter on the focused list never reaches the screen-level binding —
        # ListView's own `enter` binding consumes it and posts Selected.
        # Forward it to attach so Enter behaves the same as `a`.
        del event
        self.action_attach_workspace()

    def _schedule_peek(self) -> None:
        """Coalesce rapid cursor moves into one peek call.

        Without debouncing, holding j/k on a table of 30 rows fires 30
        peek() calls in 300 ms — that's 100+ subprocess invocations. The
        80 ms timer drops that to roughly one peek per pause-in-scrolling.
        """
        self._cancel_peek_timer()
        self._peek_timer = self.set_timer(_PEEK_DEBOUNCE_SECONDS, self._refresh_peek)

    def _cancel_peek_timer(self) -> None:
        if self._peek_timer is not None:
            self._peek_timer.stop()
            self._peek_timer = None

    def _tick_stats(self) -> None:
        """Slow ticker: full peek refresh (git ahead/behind/diff/dirty + tmux).

        Frozen on modal: when any modal is on top of us, `app.screen` is
        not this screen. Skipping the recompute keeps the user's typing
        in the create dialog snappy and avoids spurious git/tmux calls
        when the rail is not visible to the user anyway.

        Also recomputes the agent-activity axis (one transcript parse per
        visible row) *before* the peek refresh so the rail's metrics line
        renders from this tick's data, not the previous one's.
        """
        if self.app.screen is not self:
            return
        self._tick_agent_states()
        self._refresh_peek()

    def _tick_agent_states(self) -> None:
        """Recompute the agent axis for every visible row and push it to cards.

        One ``sessions_for`` call (≈ one transcript parse) per visible
        workspace per 3 s tick — the same per-tick cost discipline the
        daemon's ``poll_once`` pays for the same data. Best-effort per row:
        a row whose parse fails just keeps no agent segment this tick.
        """
        ws_list = self.query_one(WorkspaceList)
        fresh: dict[str, AgentActivity] = {}
        for state in ws_list.visible_states:
            try:
                sessions = self._service.sessions_for(self._manager, state)
            except Exception as exc:  # best-effort, peek contract
                logger.debug("agent activity for {} failed: {}", state.id, exc)
                continue
            if sessions:
                fresh[state.id] = sessions[0].activity
        self._agent_activity = fresh
        ws_list.set_agent_states({wid: act.state for wid, act in fresh.items()})

    def _tick_pane(self) -> None:
        """Fast ticker: tmux-only pane snapshot, spliced into the cached peek.

        Cheaper than the slow tick — no git work, just one capture-pane
        subprocess. Skipped when:
        - a modal is on top of us (frozen),
        - no row is selected,
        - we have no cached peek yet for this selection (slow tick will
          populate; we don't want to call full peek here),
        - the cached peek isn't running (no live pane to capture).
        Identical successive frames coalesce in the rail's diff guard, so
        an idle agent costs ~one capture-pane call per tick and zero
        Static repaints.
        """
        if self.app.screen is not self:
            return
        wid = self._selected_id()
        if wid is None or self._cached_peek is None:
            return
        if self._cached_peek.state.id != wid:
            return  # selection changed; wait for next slow tick to repopulate
        if self._cached_peek.state.status not in LIVE_STATUSES:
            return
        snap, captured_at = self._manager.peek_pane(wid)
        if snap is None:
            return
        spliced = _dc_replace(
            self._cached_peek,
            agent_snapshot=snap,
            snapshot_taken_at=captured_at,
        )
        # Pass the cached agent activity too — otherwise the splice would
        # drop the metrics line and the slow tick would re-add it (flicker).
        self.query_one(PeekRail).set_peek(spliced, agent=self._agent_activity.get(wid))

    def _tick_pulse(self) -> None:
        """Advance the live-signal pulse and push it to cards + status bar.

        Frozen on modal (same convention as the other ticks). When no
        visible workspace is ACTIVE the tick early-exits — the pulse is
        purely a "this row is producing output" cue, so no ACTIVE rows
        means no work to do and CPU floors at zero. Frame wraps modulo
        ``ACTIVE_PULSE_FRAMES`` so the int never grows unbounded.
        """
        if self.app.screen is not self:
            return
        ws_list = self.query_one(WorkspaceList)
        if not any(s.status == WorkspaceStatus.ACTIVE for s in ws_list.visible_states):
            return
        self._pulse_frame = (self._pulse_frame + 1) % ACTIVE_PULSE_FRAMES
        ws_list.set_pulse_frame(self._pulse_frame)
        self.query_one(StatusBar).pulse_frame = self._pulse_frame

    def _refresh_peek(self) -> None:
        rail = self.query_one(PeekRail)
        wid = self._selected_id()
        if wid is None:
            self._cached_peek = None
            rail.set_peek(None)
            return
        try:
            peek = self._manager.peek(wid)
        except GroveError:
            # peek() is contractually best-effort, but workspace might have
            # been killed externally between selection and recompute.
            self._cached_peek = None
            rail.set_peek(None)
            return
        self._cached_peek = peek
        # The agent map is fed by the slow tick; a row it hasn't covered yet
        # (fresh selection, sessionless workspace) simply renders no line.
        rail.set_peek(peek, agent=self._agent_activity.get(wid))

    # ─── filter ───────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if isinstance(event.input, FilterBar):
            self.query_one(WorkspaceList).set_filter(event.value)
            self._refresh_peek()
            self._refresh_footer()
            self._refresh_status_bar()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if isinstance(event.input, FilterBar):
            # Enter — keep the filter, return focus to the table.
            self.query_one(WorkspaceList).focus()

    def on_key(self, event: events.Key) -> None:
        # Esc on the filter bar clears it and returns focus to the table.
        if event.key == "escape" and isinstance(self.focused, FilterBar):
            bar = self.query_one(FilterBar)
            bar.value = ""
            bar.remove_class("-active")
            self.query_one(WorkspaceList).set_filter("")
            self.query_one(WorkspaceList).focus()
            self._refresh_peek()
            self._refresh_footer()
            self._refresh_status_bar()
            event.stop()

    # ─── internal ──────────────────────────────────────────────────────────

    def _handle_create_result(self, request: CreateWorkspaceRequest | None) -> None:
        if request is None:
            return
        try:
            self._manager.create(request)
        except BranchConflict as exc:
            self._flash(f"branch already exists: {exc}", level="error")
        except BranchNotFound as exc:
            self._flash(f"branch not found: {exc}", level="error")
        except BranchAlreadyCheckedOut as exc:
            self._flash(f"branch is already checked out at {exc.worktree}", level="error")
        except GroveError as exc:
            self._flash(f"create failed: {exc}", level="error")

    def _on_manager_event(self, event: WorkspaceEvent) -> None:
        # Subscriptions fire from arbitrary call sites; re-list to stay accurate.
        self._refresh()
        self._refresh_peek()
        self._refresh_footer()
        kind = event.kind
        if kind == "error":
            error = event.detail.get("error") or event.detail.get("exit_code") or ""
            self._flash(f"error in {event.detail.get('phase', '?')}: {error}", level="error")
        elif kind == "offline_detected":
            self._flash("workspace went offline — press 'o' to respawn", level="error")
        elif kind == "orphaned_detected":
            self._flash("worktree missing on disk — press 'k' to clean up", level="error")
        elif kind == "created":
            title = event.detail.get("title", "")
            self._flash(
                f"created '{title}'" if title else "workspace created",
                level="success",
            )
        elif kind == "paused":
            self._flash("workspace paused", level="success")
        elif kind == "resumed":
            self._flash("workspace resumed", level="success")
        elif kind == "respawned":
            self._flash("workspace respawned", level="success")
        elif kind == "killed":
            self._flash("workspace killed", level="success")
        elif kind == "updated":
            # Tailor the message so the user sees what actually changed.
            title_changed = event.detail.get("title_changed") == "true"
            description_changed = event.detail.get("description_changed") == "true"
            if title_changed and description_changed:
                self._flash("renamed and updated description", level="success")
            elif title_changed:
                self._flash("workspace renamed", level="success")
            elif description_changed:
                self._flash("description updated", level="success")

    def _safe_call(self, label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except GroveError as exc:
            self._flash(f"{label} failed: {exc}", level="error")
            return
        self._refresh()
        self._refresh_peek()
        self._refresh_footer()

    def _selected_id(self) -> str | None:
        return self.query_one(WorkspaceList).selected_id

    def _selected_state(self, states: list[WorkspaceState] | None = None) -> WorkspaceState | None:
        wid = self._selected_id()
        if wid is None:
            return None
        pool = states if states is not None else self.query_one(WorkspaceList).states
        for s in pool:
            if s.id == wid:
                return s
        return None

    def _refresh(self) -> None:
        states = self._manager.list()
        self.query_one(WorkspaceList).populate(states)
        self._refresh_status_bar(states)
        self.set_class(not states, "-empty")

    def _refresh_status_bar(self, states: list[WorkspaceState] | None = None) -> None:
        pool = states if states is not None else self.query_one(WorkspaceList).states
        bar = self.query_one(StatusBar)
        bar.breakdown = _breakdown(pool)
        bar.selection = self._selected_state(pool)
        bar.filter_query = self.query_one(WorkspaceList).filter_query

    def _refresh_footer(self) -> None:
        groups = self._footer_groups()
        self.query_one(ContextualFooter).set_groups(groups)

    def _footer_groups(self) -> list[list[FooterKey]]:
        """Globals always shown; selection group dropped when empty."""
        wid = self._selected_id()
        has_sel = wid is not None
        peek = self._safe_peek(wid) if wid is not None else None
        status = peek.state.status if peek is not None else None
        placement = peek.state.placement if peek is not None else None
        by_key = {key: (action, label) for key, action, label in DEFAULT_BINDINGS}
        globals_group: list[FooterKey] = [
            FooterKey(k, by_key[k][1], available=True)
            for k in LIST_GLOBAL_FOOTER_KEYS
            if k in by_key
        ]
        selection_group: list[FooterKey] = [
            FooterKey(
                k,
                by_key[k][1],
                available=has_sel and _key_available(k, status, placement),
            )
            for k in LIST_SELECTION_FOOTER_KEYS
            if k in by_key
        ]
        return [globals_group, selection_group]

    def _flash(self, message: str, *, level: FlashLevel = "info") -> None:
        self.query_one(StatusBar).flash(message, level=level)

    def _safe_peek(self, wid: str) -> WorkspacePeek | None:
        """peek() never raises by contract, but workspace might have been
        killed between selection and confirm — be defensive."""
        try:
            return self._manager.peek(wid)
        except GroveError:
            return None


def _breakdown(states: list[WorkspaceState]) -> dict[WorkspaceStatus, int]:
    """Count workspaces by status. Empty input → empty dict (clean -empty class flip)."""
    out: dict[WorkspaceStatus, int] = {}
    for s in states:
        out[s.status] = out.get(s.status, 0) + 1
    return out


_AVAILABLE_KEYS_BY_STATUS: dict[WorkspaceStatus, frozenset[str]] = {
    # Edit ('e') is permitted in every status except ORPHANED — the engine's
    # ensure_can_update has the same rule (orphaned records are headed for
    # kill; renaming a doomed record adds confusion). ERROR allows edit so
    # users can annotate ("see ticket #X") while a workspace is broken.
    WorkspaceStatus.ACTIVE: frozenset({"enter,a", "e", "p", "k"}),
    WorkspaceStatus.IDLE: frozenset({"enter,a", "e", "p", "k"}),
    WorkspaceStatus.RUNNING: frozenset({"enter,a", "e", "p", "k"}),  # raw intent leak
    WorkspaceStatus.PAUSED: frozenset({"e", "R", "k"}),
    WorkspaceStatus.OFFLINE: frozenset({"e", "o", "k"}),
    WorkspaceStatus.ORPHANED: frozenset({"k"}),
    WorkspaceStatus.ERROR: frozenset({"e", "k"}),
}

# Keys a placement strips out *after* the status gate. ROOT workspaces have no
# worktree, so the engine refuses pause ('p') and resume ('R') — a root
# workspace can reconcile to ACTIVE/IDLE/OFFLINE like any other, so the status
# table would otherwise offer pause/resume the engine will reject. Data, not a
# branch: a new placement constraint is one more entry here. The default
# (empty set) leaves WORKTREE's keys untouched.
_KEYS_REMOVED_BY_PLACEMENT: dict[Placement, frozenset[str]] = {
    Placement.ROOT: frozenset({"p", "R"}),
}


def _key_available(
    key: str, status: WorkspaceStatus | None, placement: Placement | None = None
) -> bool:
    """Status- and placement-aware footer gating.

    Returns True when the binding is meaningful for the currently-selected
    workspace's status *and* its placement. ``None`` status (no peek
    available — e.g. workspace was killed externally between selection and
    render) defaults to permissive so the user can still try; the action
    handler will flash on failure. Placement removes keys the engine refuses
    for that shape (root workspaces drop pause/resume).
    """
    removed = _KEYS_REMOVED_BY_PLACEMENT.get(placement) if placement is not None else None
    if removed is not None and key in removed:
        return False
    if status is None:
        return True
    allowed = _AVAILABLE_KEYS_BY_STATUS.get(status)
    if allowed is None:
        # Unknown / unhandled status: keep permissive so the user isn't stuck.
        return True
    return key in allowed


def _attach_dimensions(inside_outer_tmux: bool) -> tuple[str, str] | tuple[None, None]:
    """Return (cols, rows) as strings for the user's effective terminal.

    Inside an outer tmux: ask tmux for the current client's dimensions.
    The current client is Grove's outer-tmux client (we're a process
    inside its pane). Outside tmux: use the controlling terminal directly.

    Returns ``(None, None)`` if dimensions can't be determined. Best-effort —
    callers must tolerate the missing values and skip the resize.
    """
    if inside_outer_tmux:
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "-F", "#{client_width}x#{client_height}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (subprocess.SubprocessError, OSError):
            return (None, None)
        spec = result.stdout.strip()
        if "x" in spec:
            cols, _, rows = spec.partition("x")
            if cols.isdigit() and rows.isdigit():
                return (cols, rows)
        return (None, None)
    size = shutil.get_terminal_size((0, 0))
    if size.columns and size.lines:
        return (str(size.columns), str(size.lines))
    return (None, None)


def _pause_details(peek: WorkspacePeek | None) -> str | None:
    if peek is None:
        return None
    s = peek.state
    lines = [
        f"branch:    {s.branch}",
        f"worktree:  {s.worktree_path}",
    ]
    if peek.dirty_files > 0:
        lines.append(
            f"[bold yellow]⚠ {peek.dirty_files} uncommitted change(s)[/]"
            " — pause will refuse unless force=True"
        )
    return "\n".join(lines)


def _kill_details(peek: WorkspacePeek | None) -> str | None:
    if peek is None:
        return None
    s = peek.state
    lines = [
        f"branch (deleted):  {s.branch}",
        f"worktree (deleted): {s.worktree_path}",
        f"commits ahead of base: {peek.base_ahead}",
        f"diff: +{peek.diff_added} / -{peek.diff_removed}",
    ]
    if peek.dirty_files > 0:
        lines.append(f"[bold red]⚠ {peek.dirty_files} uncommitted change(s) will be lost[/]")
    return "\n".join(lines)
