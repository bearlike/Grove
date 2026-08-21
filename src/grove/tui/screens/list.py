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
* slow tick (`cfg.peek_stats_refresh_seconds`, default 3 s) — re-enumerates
  the workspace set (`manager.list()`, id-diffed so out-of-band creates/kills
  appear without a restart), full `peek` (git ahead/behind/diff/dirty),
  refreshes the cache, and recomputes the agent-activity axis for the
  visible rows (cards + rail metrics line).
Every tick asks `_ticks_live()` first, so none of them run while a modal
owns the foreground or while the terminal is handed to an attach — polling
a screen the user cannot see is pure waste, and never more so than when
they are inside the very workspace being polled. Number keys 1-9 jump
cursor.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Header, Input, ListView, Static

from grove.core import (
    BranchAlreadyCheckedOut,
    BranchConflict,
    BranchNotFound,
    CreateWorkspaceRequest,
    GroveError,
    ReleaseChecker,
    ReleaseStatus,
    RepoRegistry,
    SessionExplorer,
    UpdateWorkspaceRequest,
    WorkspaceEvent,
    WorkspaceManager,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
    load_config,
)
from grove.core.activity import ActivityService
from grove.core.agents import AgentActivity, SessionTurn
from grove.core.contracts.tickets import TicketRef
from grove.core.contracts.usage import UsageQuotasView
from grove.core.phase import PhaseReport, TicketClaim
from grove.core.tmux import ContainerAttach, fit_window_to_client
from grove.core.usage import UsageService
from grove.core.workspace import LIVE_STATUSES, Placement, ProvisionProgress
from grove.tui._status import ACTIVE_PULSE_FRAMES
from grove.tui.keys import (
    DEFAULT_BINDINGS,
    LIST_GLOBAL_FOOTER_KEYS,
    LIST_SELECTION_FOOTER_KEYS,
)
from grove.tui.screens.confirm import ConfirmScreen, KillConfirmScreen, KillDecision
from grove.tui.screens.create import CreateWorkspaceScreen
from grove.tui.screens.dashboard import DashboardScreen
from grove.tui.screens.edit import EditWorkspaceScreen, RetryContainerization
from grove.tui.screens.help import HelpScreen
from grove.tui.screens.message import SendMessageScreen
from grove.tui.screens.project_picker import ProjectPickerScreen, RepoChoice
from grove.tui.screens.remap_session import RemapSessionScreen
from grove.tui.screens.sessions import SessionsScreen
from grove.tui.screens.usage import UsageScreen
from grove.tui.widgets.filter_bar import FilterBar
from grove.tui.widgets.footer import ContextualFooter, FooterKey
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.peek_rail import PeekRail
from grove.tui.widgets.quota_footer import QuotaFooter
from grove.tui.widgets.status import FlashLevel, StatusBar

_PEEK_DEBOUNCE_SECONDS = 0.08

# How many tail turns feed the peek rail's transcript tab. The rail is a
# glance surface — the sessions screen (50) and `grove sessions show` are
# the read-deeply surfaces.
_RAIL_TURNS = 20

# Live-signal pulse cadence. 4 Hz — same budget as the existing peek-pane
# fast tick — gives a full ●→◉→● cycle every 0.5 s, which is fast enough
# to read as motion at a glance but slow enough to stay legible. Per-tick
# work is one int increment plus N widget refreshes guarded by a `status
# == ACTIVE` watcher; non-ACTIVE rows skip entirely. When no visible row
# is ACTIVE the tick early-exits and CPU is zero.
_PULSE_TICK_SECONDS = 0.25


class QuotasLoaded(Message):
    """One-shot quota snapshot returned by the list screen's worker."""

    def __init__(self, quotas: UsageQuotasView) -> None:
        super().__init__()
        self.quotas = quotas


class TicketsResolved(Message):
    """Live ticket display fields, resolved for one workspace off the UI thread.

    A `TicketRef` is persisted BARE, so a title exists only once something asks
    the tracker — which is network I/O, and therefore never on Textual's single
    thread. `post_message` is the marshalling primitive here for the same reason
    it is everywhere else on this screen: it is thread-safe from either side,
    where `call_from_thread` raises when it happens to run on the app's own.
    """

    def __init__(self, workspace_id: str, refs: tuple[TicketRef, ...]) -> None:
        super().__init__()
        self.workspace_id = workspace_id
        self.refs = refs


class LifecycleDone(Message):
    """A lifecycle verb finished on a worker thread.

    Carries the outcome back to the UI thread: ``error_text`` is the
    already-formatted flash copy (formatting is pure, so the worker does it)
    or ``None`` on success. Posting a message rather than calling
    ``call_from_thread`` is what makes the hop uniform — ``post_message`` is
    thread-safe from either side, while ``call_from_thread`` *raises* when
    it happens to be called on the app's own thread.
    """

    def __init__(self, label: str, error_text: str | None, key: str | None) -> None:
        super().__init__()
        self.label = label
        self.error_text = error_text
        # Whatever `_InFlightVerbs` key the verb claimed, so the handler can
        # release it — the claim has to outlive the worker, not the action.
        self.key = key


class _InFlightVerbs:
    """Which lifecycle verb is running on which workspace.

    The single-threaded UI was an implicit mutex over the whole lifecycle
    surface: no two verbs could interleave because there was only one thread
    to run them on. Moving every verb to a worker thread deleted that
    invariant silently, and a double-pressed key is then two genuinely
    concurrent verbs on one workspace — a `kill` tearing down the worktree
    while a `respawn` is halfway through `devcontainer up`.

    Keyed per workspace, never globally: verbs on *different* workspaces
    running at once is the entire point of the worker, so a global lock
    would give the invariant back by taking the fix away.

    Needs no lock of its own — both halves run on the UI thread (claimed from
    an action handler, released from the `LifecycleDone` handler); the worker
    never touches it.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}

    def claim(self, key: str, label: str) -> str | None:
        """Reserve `key` for `label`; returns the label already holding it."""
        holder = self._by_key.get(key)
        if holder is None:
            self._by_key[key] = label
        return holder

    def release(self, key: str) -> None:
        self._by_key.pop(key, None)

    @property
    def busy_label(self) -> str | None:
        """Any verb still in flight — the quit guard's question, not a count."""
        return next(iter(self._by_key.values()), None)


class ManagerSignal(Message):
    """A ``WorkspaceEvent`` from the engine, re-posted onto the UI thread.

    The manager emits from whatever thread called the verb — since lifecycle
    verbs now run in worker threads, its subscribers fire off-loop and must
    not touch widgets directly. Every event takes the same one-hop route
    whether it originated on the UI thread or in a worker.
    """

    def __init__(self, event: WorkspaceEvent) -> None:
        super().__init__()
        self.event = event


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
        release_checker: ReleaseChecker | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        # Newer-release nudge. The TUI is a separate process from the
        # daemon, so it holds its OWN checker — same engine code + bounded cache,
        # not a second polling implementation. Best-effort and run in a thread
        # worker so the GitHub GET never blocks the UI. Injectable for tests.
        self._release_checker = release_checker or ReleaseChecker()
        # Agent-activity machinery — same construction pattern as
        # DashboardScreen: built once here from the manager's config + shared
        # store unless a test injects pre-built fakes. The list screen only
        # ever reads its own repo through it (`sessions_for(self._manager, …)`),
        # but sharing the service keeps the blend + hook-sidecar policy in the
        # engine's single site instead of re-implementing it TUI-side.
        if registry is None:
            registry = RepoRegistry(
                cfg=manager.config, store=manager.store, config_loader=load_config
            )
        # Retained so the project switcher (`P`) can resolve a Manager for any
        # repo the store knows — the same cache the daemon/dashboard share, so
        # switching back to a repo reuses its already-built Manager.
        self._registry = registry
        if service is None:
            service = ActivityService(registry=registry)
        self._service = service
        # Session read-path for the rail's transcript tab and the sessions
        # screen — stateless over the manager, so one instance serves both.
        self._explorer = SessionExplorer(manager)
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
        # Tail turns of the selected row's newest session, refreshed on the
        # same slow path as the peek. The fast pane tick re-passes the
        # cached tuple so the rail's transcript tab doesn't flicker off
        # between slow ticks (same reason the agent activity is re-passed).
        # `_turns_wid` scopes the cache to the workspace it was read for, so
        # a degraded read can fall back to it without ever leaking one
        # workspace's tail into another selection.
        self._cached_turns: tuple[SessionTurn, ...] = ()
        self._turns_wid: str | None = None
        # Build progress of the selected row, but only while it is
        # PROVISIONING — `None` for every other status, which is what makes
        # the rail's provisioning branch disappear the moment the container
        # comes up. Read on the same slow path as the peek.
        self._cached_provision: ProvisionProgress | None = None
        # Resolved ticket display fields, keyed by workspace id. Populated by
        # a one-shot thread worker per selection and kept for the session:
        # a title changes on a human timescale, and re-reading it per tick
        # would put a forge request on the render path.
        self._ticket_refs: dict[str, tuple[TicketRef, ...]] = {}
        self._cached_phase: PhaseReport | None = None
        # True between handing the terminal to an attach and the user's next
        # input. Every periodic tick asks `_ticks_live()` before doing any
        # work, so a screen the user cannot see costs nothing.
        self._handed_over = False
        # One lifecycle verb per workspace at a time (see `_InFlightVerbs`).
        self._inflight = _InFlightVerbs()
        # A thread worker cannot be interrupted, so quitting mid-verb waits
        # for it; the first `q` says so instead of looking like a hang.
        self._quit_warned = False

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
        yield QuotaFooter()
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
        # One-shot newer-release check in a thread worker — the GitHub GET
        # is bounded + best-effort, and a session is short-lived relative to the
        # 6h release cadence, so checking once at mount is enough (a daemon/web
        # client re-checks on its own TTL). `thread=True` keeps the blocking GET
        # off the UI loop; `exclusive` coalesces if mount ever re-fires.
        self.run_worker(self._poll_release, thread=True, group="release", exclusive=True)
        # Quota collection can contact a metered provider. It runs once per TUI
        # launch; the collector's durable TTL and cool-off coordinate all other
        # Grove processes. It must never share the render/tick path.
        self.run_worker(self._read_quotas, thread=True, group="quota", exclusive=True)

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
        # A resize reaches us only when a client is actually displaying this
        # pane — switching a tmux client back onto it is one of the ways it
        # fires, and it costs nothing to treat that as "the user is back".
        self._take_back_terminal()
        if self.size.width < self.NARROW_THRESHOLD:
            self.add_class("-narrow")
        else:
            self.remove_class("-narrow")

    # ─── action handlers ──────────────────────────────────────────────────

    def action_quit(self) -> None:
        """Quit — but warn once if a lifecycle verb would hold the exit.

        A thread worker cannot be cancelled: Textual runs it on asyncio's
        default executor, whose threads are non-daemon and joined by
        `asyncio.run`'s `shutdown_default_executor`. Measured on Textual
        8.2.5: `app.run()` returns only after the worker's own function does,
        however long the `devcontainer up` inside it takes. So quitting
        during a create restores the terminal and then sits there silently,
        which reads exactly like a hang.

        The alternative — a daemon thread we can abandon at exit — is worse:
        it kills a half-built container and worktree mid-write and leaves a
        store record describing neither state. Side-effecting work gets to
        finish; what it does NOT get is to finish in silence. So the first
        `q` names what is running, and a second one quits for real.
        """
        busy = self._inflight.busy_label
        if busy is not None and not self._quit_warned:
            self._quit_warned = True
            self._flash(f"{busy} still running — press q again to quit and wait for it")
            return
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

    def action_open_usage(self) -> None:
        """Open the host-wide historical usage audit without starting a timer."""
        service = UsageService(cfg=self._manager.config, registry=self._registry)
        self.app.push_screen(UsageScreen(service=service))

    def action_switch_project(self) -> None:
        """Open the project switcher: re-point this screen at another repo.

        Counts come from one cheap ``store.load_all()`` grouped by repo_root —
        no git/tmux reconciliation (that's the dashboard's job). On a chosen
        repo, ``_handle_switch_result`` resolves a Manager through the shared
        registry and ``switch_screen``s to a fresh list screen for it, so the
        screen stack never grows on repeated switches.
        """
        choices = RepoChoice.group(
            self._manager.store.load_all(),
            current=self._manager.repo_root,
            known=self._registry.known_roots(),
        )
        self.app.push_screen(ProjectPickerScreen(choices), self._handle_switch_result)

    def _handle_switch_result(self, repo_root: Path | None) -> None:
        if repo_root is None or repo_root.resolve() == self._manager.repo_root.resolve():
            return
        new_manager = self._registry.get(repo_root)
        self.app.switch_screen(WorkspaceListScreen(new_manager, registry=self._registry))

    def action_open_sessions(self) -> None:
        """Browse the selected workspace's agent-session history.

        Reads through the engine's bounded ``SessionExplorer.for_workspace``
        seam (one-cwd scan). Transcripts outlive worktrees — they live under
        the agent tool's own data dir — so the key stays available in every
        status: a paused or orphaned workspace still has readable history.
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        state = self._selected_state()
        title = state.title if state is not None else wid[:8]
        self.app.push_screen(
            SessionsScreen(
                self._explorer,
                workspace_id=wid,
                workspace_title=title,
                registry=self._registry,
            )
        )

    def action_remap_session(self) -> None:
        """Manually pin a session as the selected workspace's tracked primary.

        Sources candidates from ``SessionExplorer.candidates_for`` — the
        UNGATED cwd-scoped scan — never ``for_workspace``, which drops
        exactly the sessions this verb exists to recover (a dead-minted
        pointer's pre-birth live successor, a foreign session sharing a
        ROOT cwd). ``manager.remap_session`` is likewise ungated, so
        nothing offered here can be refused except an adapter-kind
        mismatch, which surfaces as a typed ``_safe_call`` error flash.
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        state = self._selected_state()
        title = state.title if state is not None else wid[:8]
        candidates = self._explorer.candidates_for(wid)
        if not candidates:
            self._flash("no sessions to remap")
            return

        def _on_result(session_id: str | None) -> None:
            if session_id is None:
                return
            self._safe_call("remap", lambda: self._manager.remap_session(wid, session_id), key=wid)

        self.app.push_screen(RemapSessionScreen(candidates, workspace_title=title), _on_result)

    def action_send_message(self) -> None:
        """Send a follow-up message to the selected workspace's agent.

        The manager owns the per-kind dispatch (tmux inject for
        claude_code/generic, remote API for mewbo) — the TUI never reads
        ``agent_kind``. Success surfaces through the manager's
        ``message_sent`` event (same convention as the lifecycle flashes);
        typed refusals (offline/paused, no pane, remote errors) land as an
        error flash via ``_safe_call``.
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        state = self._selected_state()
        title = state.title if state is not None else wid[:8]

        def _on_result(text: str | None) -> None:
            if text is None:
                return
            self._safe_call("message", lambda: self._manager.send_message(wid, text), key=wid)

        self.app.push_screen(SendMessageScreen(workspace_title=title), _on_result)

    def action_focus_filter(self) -> None:
        bar = self.query_one(FilterBar)
        bar.add_class("-active")
        bar.focus()

    def action_jump_to(self, index: int) -> None:
        # 1-based input → 0-based table index.
        self.query_one(WorkspaceList).jump_to(index - 1)

    def action_new_workspace(self) -> None:
        """Open the create modal for a new workspace.

        Refuses (with a flash) when the resolved roster is empty — the same
        guard-at-the-caller shape as the ORPHANED check in
        ``action_edit_workspace``: ``CreateWorkspaceScreen`` asserts a non-empty
        roster as its precondition, and the user-facing recovery belongs here.
        An empty roster means ``builtin_agents: false`` with nothing declared,
        so the flash names that field rather than just the symptom —
        every other route to an empty list is impossible, since the built-ins
        seed layer 0 of the cascade.
        """
        agents = self._manager.config.agents
        if not agents:
            self._flash("no agents configured — declare one or set builtin_agents: true")
            return
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
            agents,
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
            current_runtime=state.runtime,
            current_runtime_fallback_reason=state.runtime_fallback_reason,
            current_runtime_no_tmux=state.runtime_no_tmux,
        )
        self.app.push_screen(screen, self._handle_edit_result)

    def _handle_edit_result(
        self, result: UpdateWorkspaceRequest | RetryContainerization | None
    ) -> None:
        if result is None:
            return
        wid = self._selected_id()
        if wid is None:
            return
        if isinstance(result, RetryContainerization):
            # A verb, not a field edit — respawn re-evaluates the
            # runtime decision tree only when a fallback reason is set;
            # this button only renders when it is, so no extra guard here.
            self._safe_call("respawn", lambda: self._manager.respawn(wid), key=wid)
            return
        # UpdateWorkspaceRequest's None on a field means "do not change";
        # the manager accepts the same convention via its _UNSET sentinel,
        # so we forward only the fields the user actually populated.
        # `Any`, not `str`: `update` also takes a bool `share`, so a
        # str-valued dict no longer type-checks at the splat. This screen never
        # sets it — sharing is a browser affordance — but the splat is typed
        # against the whole signature, not against the subset used here.
        kwargs: dict[str, Any] = {}
        if result.title is not None:
            kwargs["title"] = result.title
        if result.description is not None:
            kwargs["description"] = result.description
        self._safe_call("edit", lambda: self._manager.update(wid, **kwargs), key=wid)

    def action_pause_workspace(self) -> None:
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return

        def _on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._safe_call("pause", lambda: self._manager.pause(wid), key=wid)

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
        self._safe_call("resume", lambda: self._manager.resume(wid), key=wid)

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
        self._safe_call("respawn", lambda: self._manager.respawn(wid), key=wid)

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
                key=wid,
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
        """Hand the terminal to the workspace's tmux session.

        Stays on the UI thread deliberately — `app.suspend()` is the UI
        thread's to give. That also means the two suspending branches need no
        tick gate: the whole event loop is blocked inside `subprocess.run`
        for the attach's duration, so no timer callback can run (measured on
        Textual 8.2.5: zero ticks across a suspend, then exactly one per
        timer on resume — `Timer._run`'s `skip` drops the missed ones).
        """
        wid = self._selected_id()
        if wid is None:
            self._flash("nothing selected")
            return
        try:
            instr = self._manager.attach(wid)
        except GroveError as exc:
            self._flash(f"attach failed: {exc}")
            return
        argv = instr.terminal_argv()
        if isinstance(instr, ContainerAttach):
            # The container's own tmux owns this session, so there is no
            # host session to pre-size and no host client to re-point:
            # `fit_window_to_client` and `switch-client` both address THIS
            # host's server, and the target lives on another one. Suspend and
            # exec in, whether or not Grove itself is running inside tmux.
            with self.app.suspend():
                subprocess.run(argv, check=False)
            return
        # Let tmux size the workspace's windows to the client we're about to
        # hand over to, BEFORE the switch/attach — so the client's own resize
        # pass applies it. Never `resize-window -x/-y`: explicit dimensions pin
        # the window to `window-size: manual` (tmux then stops re-fitting it
        # forever) and must exclude the status-bar rows that `#{client_height}`
        # counts but the window never gets. See `tmux.fit_window_to_client`.
        fit_window_to_client(instr.tmux_session)
        if instr.inside_outer_tmux:
            # Inside outer tmux — switch the existing client; Grove keeps
            # running, unwatched, in a session the user has just left. This is
            # the ONLY attach branch that needs the tick gate: `switch-client`
            # returns immediately, so without it the pane tick keeps paying a
            # `docker exec` at 4 Hz for the entire attach (measured ~24%
            # of a core) to repaint a screen nobody is looking at.
            self._hand_over_terminal()
            subprocess.run(argv, check=False)
        else:
            # Not in tmux — suspend Textual, attach, resume after detach.
            with self.app.suspend():
                subprocess.run(argv, check=False)

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

    # ─── tick gating ─────────────────────────────────────────────────────

    def _ticks_live(self) -> bool:
        """Should a periodic tick do any work right now?

        Two reasons it shouldn't, and both mean the same thing: whatever the
        tick would render, nobody can see. A modal owns the foreground, or we
        handed the terminal to an attach and the user is inside the workspace
        we would be polling — the worst possible moment to spend a
        ``docker exec`` per pane tick on a screen that isn't on screen.

        Deliberately NOT gated on `app.app_focus`: an unfocused Grove is a
        legitimate live surface (watching the fleet on a second monitor while
        typing elsewhere is a primary use), so blur would freeze a screen the
        user is looking straight at.
        """
        return self.app.screen is self and not self._handed_over

    def _hand_over_terminal(self) -> None:
        """Stop ticking — the user's terminal now belongs to something else.

        Open-ended on purpose: `switch-client` returns the instant tmux
        re-points the client, so the *call* finishing tells us nothing about
        when the user comes back. `_take_back_terminal` is wired to the
        signals that do mean they are back (a key, a mouse move, a resize).
        """
        self._handed_over = True

    def _take_back_terminal(self) -> None:
        """The user is back — go live again and snap the screen current.

        The refresh matters because the ticks were off for the whole attach:
        without it the first thing they'd see is a frame that could be hours
        stale, quietly correcting itself a tick later.
        """
        if not self._handed_over:
            return
        self._handed_over = False
        self.action_refresh()

    def _tick_stats(self) -> None:
        """Slow ticker: re-enumerate the workspace set, then full peek refresh.

        Frozen on modal: when any modal is on top of us, `app.screen` is
        not this screen. Skipping the recompute keeps the user's typing
        in the create dialog snappy and avoids spurious git/tmux calls
        when the rail is not visible to the user anyway.

        Re-reads ``manager.list()`` first so out-of-band lifecycle changes
        (a workspace created/killed/paused/resumed via MCP, the daemon, or
        a second TUI on the same project) appear without a restart. The
        store is the shared source of truth and ``list()`` re-reads it
        whole-file (the engine's atomic ``os.replace`` writes mean reads
        are always consistent); ``populate`` diffs by id and preserves the
        selected row, so repeated ticks are idempotent (no duplicate rows,
        no cursor jump). The manager's ``_maybe_emit_status_drift`` guard
        is idempotent across consecutive ``list()`` calls, so this refresh
        can't recurse through ``_on_manager_event``.

        Then recomputes the agent-activity axis (one transcript parse per
        visible row) over the *fresh* set *before* the peek refresh so the
        rail's metrics line renders from this tick's data, not the previous
        one's.
        """
        if not self._ticks_live():
            return
        self._refresh()
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
        ws_list.set_phases(self._visible_phases(ws_list))

    def _visible_phases(self, ws_list: WorkspaceList) -> dict[str, PhaseReport]:
        """Each visible workspace's reported phase, for the card axis.

        It rides this tick rather than getting one of its own because the cost
        is a rounding error next to what the tick already pays: measured on
        this host, one `phase()` is **0.47 ms** against the transcript parse
        `sessions_for` costs per row immediately above — so a 20-row fleet adds
        ~10 ms per 3 s tick, well under a percent of a core.

        That measurement is the whole reason the axis is pushed to every card
        instead of staying on the peek rail's selected workspace. The cost was
        assumed to be per-row file I/O worth avoiding; it is not.

        Best-effort per row, like the agent axis: an unreadable phase file
        leaves that card with no segment rather than failing the tick.
        """
        phases: dict[str, PhaseReport] = {}
        for state in ws_list.visible_states:
            try:
                report = self._manager.phase(state.id)
            except Exception as exc:  # best-effort, peek contract
                logger.debug("phase for {} failed: {}", state.id, exc)
                continue
            if report is not None:
                phases[state.id] = report
        return phases

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
        if not self._ticks_live():
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
        # Pass the cached agent activity and turns too — otherwise the
        # splice would drop the metrics line / transcript tab and the slow
        # tick would re-add them (flicker). The fast tick stays tmux-only:
        # no transcript parse on this path.
        self.query_one(PeekRail).set_peek(
            spliced,
            agent=self._agent_activity.get(wid),
            turns=self._cached_turns,
            tickets=self._ticket_refs.get(wid),
            ticket_claims=self._ticket_claims(),
        )

    def _tick_pulse(self) -> None:
        """Advance the live-signal pulse and push it to cards + status bar.

        Frozen on modal (same convention as the other ticks). When no
        visible workspace is ACTIVE the tick early-exits — the pulse is
        purely a "this row is producing output" cue, so no ACTIVE rows
        means no work to do and CPU floors at zero. Frame wraps modulo
        ``ACTIVE_PULSE_FRAMES`` so the int never grows unbounded.
        """
        if not self._ticks_live():
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
            self._cached_turns = ()
            self._turns_wid = None
            self._cached_provision = None
            rail.set_peek(None)
            return
        try:
            peek = self._manager.peek(wid)
        except GroveError:
            # peek() is contractually best-effort, but workspace might have
            # been killed externally between selection and recompute.
            self._cached_peek = None
            self._cached_turns = ()
            self._turns_wid = None
            self._cached_provision = None
            rail.set_peek(None)
            return
        self._cached_peek = peek
        self._cached_turns = self._recent_turns(wid)
        self._turns_wid = wid
        self._cached_provision = self._provision_progress(peek)
        self._cached_phase = self._phase_report(wid)
        self._resolve_tickets(peek.state)
        # The agent map is fed by the slow tick; a row it hasn't covered yet
        # (fresh selection, sessionless workspace) simply renders no line.
        rail.set_peek(
            peek,
            agent=self._agent_activity.get(wid),
            turns=self._cached_turns,
            provision=self._cached_provision,
            tickets=self._ticket_refs.get(wid),
            ticket_claims=self._ticket_claims(),
        )

    def _ticket_claims(self) -> dict[str, TicketClaim] | None:
        """Per-ticket phase claims, keyed the way `TicketRef.key` spells them.

        A pure projection of the phase report this screen already read — the
        join needs no second source, because the agent writes its claims under
        the same `provider:id` string the store deduplicates refs by.
        """
        if self._cached_phase is None:
            return None
        return {claim.ticket: claim for claim in self._cached_phase.tickets}

    def _phase_report(self, wid: str) -> PhaseReport | None:
        """The selection's reported phase. One file read, on the slow path.

        Best-effort like every other read here: a workspace whose agent never
        reported has no file, which is a `None` rather than a failure.
        """
        try:
            return self._manager.phase(wid)
        except Exception as exc:  # best-effort, peek contract
            logger.debug("phase read for {} failed: {}", wid, exc)
            return None

    def _resolve_tickets(self, state: WorkspaceState) -> None:
        """Resolve this selection's ticket titles once, off the UI thread.

        The read is a forge request per ref, so it can never run inline: Textual
        is single-threaded, and a slow tracker would freeze every timer, every
        keypress and the whole repaint. It is also not on any tick — one shot
        per workspace per session, cached, because a ticket title moves on a
        human timescale while this screen repaints at 4 Hz.
        """
        if not state.ticket_refs or state.id in self._ticket_refs:
            return
        # Claim the slot before the worker starts so a second selection of the
        # same row cannot queue a duplicate fetch; the stored value is replaced
        # with the resolved one when it lands.
        self._ticket_refs[state.id] = tuple(state.ticket_refs)
        wid, refs = state.id, tuple(state.ticket_refs)
        self.run_worker(
            lambda: self._read_tickets(wid, refs),
            thread=True,
            group="tickets",
        )

    def _read_tickets(self, wid: str, refs: tuple[TicketRef, ...]) -> None:
        """Worker body: one GET per ref, every failure degrading to the bare ref."""
        providers = self._manager.ticket_providers
        resolved: list[TicketRef] = []
        for ref in refs:
            try:
                provider = providers.get(ref.provider)
                resolved.append(
                    provider.get_pull_request(ref.id)
                    if ref.kind == "pull_request"
                    else provider.get_ticket(ref.id)
                )
            except Exception as exc:  # enrichment never breaks the rail
                logger.debug("ticket enrichment failed for {}: {}", ref.key, exc)
                resolved.append(ref)
        self.post_message(TicketsResolved(wid, tuple(resolved)))

    def on_tickets_resolved(self, message: TicketsResolved) -> None:
        """Store the resolved refs and repaint if they are still on screen."""
        self._ticket_refs[message.workspace_id] = message.refs
        if self._selected_id() == message.workspace_id and self._cached_peek is not None:
            self.query_one(PeekRail).set_peek(
                self._cached_peek,
                agent=self._agent_activity.get(message.workspace_id),
                turns=self._cached_turns,
                provision=self._cached_provision,
                tickets=message.refs,
                ticket_claims=self._ticket_claims(),
            )

    def _provision_progress(self, peek: WorkspacePeek) -> ProvisionProgress | None:
        """Build progress for a PROVISIONING selection, else ``None``.

        Rides the slow path (selection debounce + 3 s stats tick), never the
        4 Hz pane tick: this is a file read of a log that reaches ~1 MB on a
        cold build, which is the same cost class as the ``peek()`` git work
        beside it and emphatically not the class the fast tick is allowed to
        pay. One selected row, not the whole fleet — the cards get their
        elapsed time from the persisted stamp with no I/O at all.

        Best-effort like every other read on this path: a failure renders the
        provisioning affordance without a tail rather than breaking the rail.
        """
        if peek.state.status != WorkspaceStatus.PROVISIONING:
            return None
        try:
            return self._manager.provision_progress(peek.state.id)
        except Exception as exc:  # best-effort, peek contract
            logger.debug("provision progress for {} failed: {}", peek.state.id, exc)
            return None

    def _recent_turns(self, wid: str) -> tuple[SessionTurn, ...]:
        """Tail turns of the selected row's newest session, for the rail.

        Rides the same slow path as ``peek()`` (selection debounce + slow
        stats tick), so the cost — one directory scan plus one transcript
        parse — is the same class the activity tick already pays per row.
        Best-effort like peek — but a FAILED read for the selection we
        already have a tail for keeps the last-good tuple instead of
        returning empty: a remote session's ``/events`` fetch times out
        routinely, and flapping the rail to "(no transcript)" and back on
        every degraded tick would flicker the transcript for no reason
        (the engine's ``_settle`` precedent applied at this seam). A
        different workspace never inherits the stale cache.
        """
        try:
            listings = self._explorer.for_workspace(wid)
            if not listings:
                return ()
            return self._explorer.turns_for(listings[0], last=_RAIL_TURNS)
        except Exception as exc:  # best-effort, peek contract
            logger.debug("transcript tail for {} failed: {}", wid, exc)
            return self._cached_turns if wid == self._turns_wid else ()

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
        # Any key means the user is looking at us again — a key can only
        # reach this pane from a client that is displaying it. Resuming here
        # (rather than swallowing the key) keeps the press doing its own job.
        self._take_back_terminal()
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
        # Create rides the same off-thread seam as every other verb; only its
        # error copy differs, and that lives in one pure formatter.
        self._safe_call(
            "create",
            lambda: self._manager.create(request),
            key=None,
            describe=_create_error_message,
        )

    def _on_manager_event(self, event: WorkspaceEvent) -> None:
        # May arrive on a lifecycle worker thread — hop to the UI thread
        # before anything reads or writes a widget.
        self.post_message(ManagerSignal(event))

    def on_manager_signal(self, message: ManagerSignal) -> None:
        self._apply_manager_event(message.event)

    def _apply_manager_event(self, event: WorkspaceEvent) -> None:
        # Subscriptions fire from arbitrary call sites; re-list to stay accurate.
        self._refresh()
        self._refresh_peek()
        self._refresh_footer()
        kind = event.kind
        # Constant-message kinds resolve through the data table (same
        # data-not-branches idiom as the footer gating); only kinds whose
        # message depends on the event detail keep a branch.
        flash = _EVENT_FLASH.get(kind)
        if flash is not None:
            self._flash(flash[0], level=flash[1])
        elif kind == "error":
            error = event.detail.get("error") or event.detail.get("exit_code") or ""
            self._flash(f"error in {event.detail.get('phase', '?')}: {error}", level="error")
        elif kind == "created":
            title = event.detail.get("title", "")
            self._flash(
                f"created '{title}'" if title else "workspace created",
                level="success",
            )
        elif kind == "updated":
            # Tailor the message so the user sees what actually changed.
            title_changed = event.detail.get("title_changed") == "true"
            description_changed = event.detail.get("description_changed") == "true"
            session_remapped = event.detail.get("session_remapped")
            if title_changed and description_changed:
                self._flash("renamed and updated description", level="success")
            elif title_changed:
                self._flash("workspace renamed", level="success")
            elif description_changed:
                self._flash("description updated", level="success")
            elif session_remapped:
                self._flash(f"session remapped to {session_remapped[:8]}", level="success")

    def _safe_call(
        self,
        label: str,
        fn: Callable[[], object],
        *,
        key: str | None,
        describe: Callable[[str, GroveError], str] | None = None,
    ) -> None:
        """Run a blocking engine verb on a worker THREAD, never the UI loop.

        Textual is single-threaded: calling `manager.create(...)` inline froze
        the *whole* application for the verb's duration — every timer (the
        stats tick, the pane tick, the pulse), every keypress and every
        repaint — which for a container provision is minutes of dead UI.
        The verb therefore runs in a thread worker and its outcome comes back
        as a `LifecycleDone` message; only that handler touches widgets.

        `key` is the exclusion domain — the workspace id for every verb that
        names one, `None` for `create` (there is no workspace yet, and two
        creates are two different workspaces, so they may legitimately
        overlap). A second verb on a busy workspace is REFUSED, not queued:
        the press was decided against a view of the workspace that the verb
        already in flight is busy invalidating, so running it three minutes
        later — `pause` landing after the `respawn` it was meant to follow —
        obeys the keystroke while betraying the intent. Refusing says so at
        the moment the user can still choose again.

        Not `run_worker(exclusive=True)`, which would be worse than doing
        nothing: it cancels the *other* worker in the group, and a thread
        worker cannot be interrupted (Textual runs it on the default executor
        and merely drops the awaiting task), so a cancelled `devcontainer up`
        keeps provisioning with nothing left to reconcile its result. It is
        also group-wide, so it would cancel verbs on other workspaces.

        `describe` formats the error flash (pure — it runs in the worker);
        the default is the `"<label> failed: <exc>"` copy every verb but
        `create` uses.
        """
        if key is not None:
            holder = self._inflight.claim(key, label)
            if holder is not None:
                self._flash(f"{label} ignored — {holder} still running on this workspace")
                return
        self._flash(f"{label} in progress…")

        def _body() -> None:
            error_text: str | None = None
            try:
                fn()
            except GroveError as exc:
                error_text = (describe or _verb_error_message)(label, exc)
            self.post_message(LifecycleDone(label, error_text, key))

        self.run_worker(_body, thread=True, group="lifecycle", description=label)

    def on_lifecycle_done(self, message: LifecycleDone) -> None:
        """Apply a worker-run verb's outcome — the UI-thread half of `_safe_call`."""
        if message.key is not None:
            self._inflight.release(message.key)
        if self._inflight.busy_label is None:
            self._quit_warned = False
        if message.error_text is not None:
            self._flash(message.error_text, level="error")
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

    def _poll_release(self) -> None:
        """Thread-worker body: run the best-effort release check, push to the bar.

        `check()` never raises (engine contract); the result is applied on the UI
        thread via `call_from_thread` so the reactive write is loop-safe.
        """
        status = self._release_checker.check()
        self.app.call_from_thread(self._apply_release_status, status)

    def _apply_release_status(self, status: ReleaseStatus) -> None:
        bar = self.query_one(StatusBar)
        bar.update_available = status.update_available
        bar.latest_version = status.latest or ""

    def _read_quotas(self) -> None:
        """Read quotas once off-loop; failures leave existing chrome untouched."""
        service: UsageService | None = None
        try:
            service = UsageService(cfg=self._manager.config, registry=self._registry)
            quotas = service.quotas()
        except Exception as exc:  # best-effort chrome must not surface a failure
            logger.debug("quota footer read failed: {}", exc)
            return
        finally:
            if service is not None:
                service.close()
        self.post_message(QuotasLoaded(quotas))

    def on_quotas_loaded(self, message: QuotasLoaded) -> None:
        self.query_one(QuotaFooter).set_quotas(message.quotas)

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


def _verb_error_message(label: str, exc: GroveError) -> str:
    """Default failure copy for a lifecycle verb — pure, so a worker can format it."""
    return f"{label} failed: {exc}"


def _create_error_message(label: str, exc: GroveError) -> str:
    """Create's failure copy: the three branch refusals name the fix, not the verb.

    An isinstance chain, not ordered `except` clauses — the exception
    crosses a thread boundary as a value, so the dispatch has to be one
    too. Order matters: the specific branch errors are `GroveError`
    subclasses, so they must be tested first.
    """
    if isinstance(exc, BranchConflict):
        return f"branch already exists: {exc}"
    if isinstance(exc, BranchNotFound):
        return f"branch not found: {exc}"
    if isinstance(exc, BranchAlreadyCheckedOut):
        return f"branch is already checked out at {exc.worktree}"
    return _verb_error_message(label, exc)


def _breakdown(states: list[WorkspaceState]) -> dict[WorkspaceStatus, int]:
    """Count workspaces by status. Empty input → empty dict (clean -empty class flip)."""
    out: dict[WorkspaceStatus, int] = {}
    for s in states:
        out[s.status] = out.get(s.status, 0) + 1
    return out


# Manager events whose flash message is a constant. Kinds with
# detail-dependent copy (error / created / updated) stay as branches in
# `_on_manager_event`.
_EVENT_FLASH: dict[str, tuple[str, FlashLevel]] = {
    "offline_detected": ("workspace went offline — press 'o' to respawn", "error"),
    "orphaned_detected": ("worktree missing on disk — press 'k' to clean up", "error"),
    "paused": ("workspace paused", "success"),
    "resumed": ("workspace resumed", "success"),
    "respawned": ("workspace respawned", "success"),
    "killed": ("workspace killed", "success"),
    "message_sent": ("message sent", "success"),
}


_AVAILABLE_KEYS_BY_STATUS: dict[WorkspaceStatus, frozenset[str]] = {
    # Edit ('e') is permitted in every status except ORPHANED — the engine's
    # ensure_can_update has the same rule (orphaned records are headed for
    # kill; renaming a doomed record adds confusion). ERROR allows edit so
    # users can annotate ("see ticket #X") while a workspace is broken.
    # Sessions ('s') is permitted in EVERY status — transcripts outlive
    # worktrees (they live in the agent tool's own data dir), so even an
    # orphaned record's history is readable.
    # Message ('m') is RUNNING-family only — same gate family as pause:
    # steering needs a live session (the engine refuses OFFLINE/PAUSED with
    # a typed error; the footer dims the key so the modal isn't a trap).
    # Remap ('x') shares edit's gate exactly — same `ensure_can_update`
    # rule the engine's `remap_session` enforces (a doomed ORPHANED record
    # gains nothing from a re-pinned session).
    WorkspaceStatus.ACTIVE: frozenset({"enter,a", "m", "e", "s", "x", "p", "k"}),
    WorkspaceStatus.IDLE: frozenset({"enter,a", "m", "e", "s", "x", "p", "k"}),
    WorkspaceStatus.RUNNING: frozenset(
        {"enter,a", "m", "e", "s", "x", "p", "k"}
    ),  # raw intent leak
    WorkspaceStatus.PAUSED: frozenset({"e", "s", "x", "R", "k"}),
    WorkspaceStatus.OFFLINE: frozenset({"e", "s", "x", "o", "k"}),
    WorkspaceStatus.ORPHANED: frozenset({"s", "k"}),
    WorkspaceStatus.ERROR: frozenset({"e", "s", "x", "k"}),
    # Provisioning offers what ERROR does, and the omissions carry the
    # message: attach ('enter,a') and message ('m') need a session the
    # container has not started yet (the engine refuses both with a typed
    # error naming the elapsed time), and respawn ('o') is OFFLINE-only —
    # dimming it is the point, since restarting a build that is already
    # running is exactly the reflex this status exists to head off. Kill ('k')
    # stays: it is the universal escape hatch and the honest way out of a
    # build the user no longer wants.
    WorkspaceStatus.PROVISIONING: frozenset({"e", "s", "x", "k"}),
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
