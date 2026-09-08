"""DashboardScreen — the cross-project Activity Dashboard.

Where ``WorkspaceListScreen`` shows one repo's workspaces, this shows *every*
workspace across *every* repo as a wall of agent-activity tiles grouped by
project. It answers "what is every agent doing right now" at a glance: which
sessions are working, which are waiting for the human, which have stalled.

The data hub is the daemon's shared ``/events`` projection. The screen only
subscribes and renders its contract views; it never independently polls or
recomputes agent activity. Its pulse timer advances local chrome and captures
the focused live pane, while activity changes arrive as daemon events.

Opened from the list screen on ``d``; ``escape`` / ``d`` / ``q`` pop back.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar, Final

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Header, Static

from grove.client import BackendConfig, GroveClient, GroveClientError
from grove.core import WorkspaceManager
from grove.core.activity import ActivityService
from grove.core.agents import AgentActivityState
from grove.core.agents.hook import DEFAULT_DAEMON_LOOPBACK_URL
from grove.core.contracts.activity import (
    DashboardEvent,
    DashboardSnapshotView,
    WorkspaceActivityView,
)
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
_STREAM_RETRY_SECONDS: Final = 1.0


class DashboardEventReceived(Message):
    """A daemon event marshalled from the stream worker onto the UI thread."""

    def __init__(self, event: DashboardEvent) -> None:
        super().__init__()
        self.event = event


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
        client: GroveClient | None = None,
    ) -> None:
        super().__init__()
        # The manager supplies config for the daemon endpoint and an optional
        # in-process projection only for isolated TUI tests.
        self._manager = manager
        self._service = service
        # Tests and standalone callers may inject an in-process projection, but
        # normal TUI construction always consumes the daemon's shared stream.
        daemon_config = BackendConfig(
            label="TUI",
            daemon_url=manager.config.hooks.daemon_url or DEFAULT_DAEMON_LOOPBACK_URL,
        )
        self._client = client or GroveClient(daemon_config)
        self._pane_client = GroveClient(daemon_config)
        self._snapshot: DashboardSnapshotView | None = None
        self._activity_cursor: int | None = None
        self._pane_workspace_id: str | None = None
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
        if self._service is not None:
            self._snapshot = DashboardSnapshotView.from_snapshot(self._service.snapshot())
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)
        self.run_worker(self._consume_events(), group="activity-stream", exclusive=True)
        self._pulse_timer = self.set_interval(_PULSE_TICK_SECONDS, self._tick_pulse)

    def on_unmount(self) -> None:
        for attr in ("_pulse_timer",):
            timer: Timer | None = getattr(self, attr)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)

    # ─── actions ──────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def action_cycle_lens(self) -> None:
        self._lens_index = (self._lens_index + 1) % len(_LENSES)
        self.sub_title = self._lens_subtitle()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def action_toggle_group(self) -> None:
        self._group_by_project = not self._group_by_project
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    # ─── shared daemon stream + pulse ──────────────────────────────────────

    async def _consume_events(self) -> None:
        """Keep one bounded reconnecting subscription to the daemon projection."""
        while self.is_mounted:
            try:
                await self._client.connect()
                async for event in self._client.activity_events(
                    last_event_id=self._activity_cursor
                ):
                    self.post_message(DashboardEventReceived(event))
            except asyncio.CancelledError:
                raise
            except GroveClientError:
                await asyncio.sleep(_STREAM_RETRY_SECONDS)
            else:
                await asyncio.sleep(_STREAM_RETRY_SECONDS)
            finally:
                await self._client.close()

    async def _consume_pane_events(self, workspace_id: str) -> None:
        """Forward one focused workspace's daemon pane stream to the UI."""
        try:
            await self._pane_client.connect()
            async for event in self._pane_client.pane_events(workspace_id):
                if event.kind == "pane_snapshot":
                    self.post_message(DashboardEventReceived(event))
        except asyncio.CancelledError:
            raise
        except GroveClientError:
            return
        finally:
            await self._pane_client.close()

    def on_dashboard_event_received(self, message: DashboardEventReceived) -> None:
        event = message.event
        if event.kind == "snapshot" and event.snapshot is not None:
            self._snapshot = event.snapshot
        elif event.kind == "session_activity" and event.workspace is not None:
            self._snapshot = _replace_workspace(self._snapshot, event.workspace)
        elif (
            event.kind == "workspace_changed"
            and event.workspace_id is not None
            and event.detail.get("event") == "killed"
        ):
            self._snapshot = _remove_workspace(self._snapshot, event.workspace_id)
        elif event.kind == "pane_snapshot" and event.pane is not None:
            workspace_id = event.workspace_id
            if workspace_id is not None and workspace_id == self._pane_workspace_id:
                self._pane_cache[workspace_id] = event.pane.ansi
                focused = self._focused_card()
                if focused is not None and focused.workspace_id == workspace_id:
                    focused.set_pane_snapshot(event.pane.ansi)
            return
        else:
            return
        self._activity_cursor = event.seq
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

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
        self._stream_focused_pane()

    def _stream_focused_pane(self) -> None:
        """Keep the daemon's one focused-pane stream aligned with card focus."""
        focused = self._focused_card()
        workspace_id = (
            focused.workspace_id if focused is not None and is_promoted(focused.activity) else None
        )
        if workspace_id == self._pane_workspace_id:
            return
        self._pane_workspace_id = workspace_id
        if workspace_id is None:
            return
        self.run_worker(
            self._consume_pane_events(workspace_id),
            group="activity-pane-stream",
            exclusive=True,
        )

    def _apply_pane_cache(self) -> None:
        """Re-push cached pane snapshots after a wall rebuild (no new tmux calls)."""
        for card in self._all_cards():
            if is_promoted(card.activity):
                card.set_pane_snapshot(self._pane_cache.get(card.workspace_id))

    # ─── rendering ────────────────────────────────────────────────────────

    def _render_snapshot(self, snapshot: DashboardSnapshotView) -> None:
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
        self, snapshot: DashboardSnapshotView
    ) -> list[tuple[str, list[WorkspaceActivityView]]]:
        """Project groups after applying the current lens.

        When grouping is off, everything collapses into one synthetic "all"
        group so the wall is a single flat grid. Empty groups are dropped so a
        lens that filters a whole project out doesn't leave a bare header.
        """
        lens = _LENSES[self._lens_index]
        out: list[tuple[str, list[WorkspaceActivityView]]] = []
        if self._group_by_project:
            for group in snapshot.projects:
                rows = [w for w in group.workspaces if _passes_lens(w, lens)]
                if rows:
                    out.append((group.repo_name, rows))
            return out
        flat = [
            workspace
            for project in snapshot.projects
            for workspace in project.workspaces
            if _passes_lens(workspace, lens)
        ]
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
        return any(
            card.activity.sessions
            and card.activity.sessions[0].activity.state == AgentActivityState.WORKING
            for card in self._all_cards()
        )

    def _grids(self) -> list[DashboardGrid]:
        return list(self.query(DashboardGrid))

    def _all_cards(self) -> list[DashboardCard]:
        return list(self.query(DashboardCard))

    def _focused_card(self) -> DashboardCard | None:
        for card in self._all_cards():
            if card.has_focus:
                return card
        return None

    def _lens_subtitle(self) -> str:
        return f"lens: {_LENS_LABEL[_LENSES[self._lens_index]]}"

    def _empty_message(self) -> str:
        lens = _LENSES[self._lens_index]
        if lens == "all":
            return "no workspaces across any project — press [bold]d[/] to go back"
        label = _LENS_LABEL[lens]
        return f"no workspaces match the [bold]{label}[/] lens — press [bold]l[/] to widen"


def _passes_lens(activity: WorkspaceActivityView, lens: _Lens) -> bool:
    if lens == "all":
        return True
    if lens == "attention":
        return activity.needs_attention
    # "active": any session in a live/pending agent state.
    return bool(activity.sessions) and activity.sessions[0].activity.state in _ACTIVE_STATES


def _remove_workspace(
    snapshot: DashboardSnapshotView | None, workspace_id: str
) -> DashboardSnapshotView | None:
    """Drop a lifecycle-deleted row from the daemon projection."""
    if snapshot is None:
        return None
    data = snapshot.model_dump(mode="json")
    for project in data["projects"]:
        project["workspaces"] = [
            row for row in project["workspaces"] if row["state"]["id"] != workspace_id
        ]
    return DashboardSnapshotView.model_validate(data)


def _replace_workspace(
    snapshot: DashboardSnapshotView | None, workspace: WorkspaceActivityView
) -> DashboardSnapshotView | None:
    """Replace one daemon-projected row without re-reading activity in the TUI."""
    if snapshot is None:
        return None
    data = snapshot.model_dump(mode="json")
    for project in data["projects"]:
        rows = project["workspaces"]
        for index, row in enumerate(rows):
            if row["state"]["id"] == workspace.state.id:
                rows[index] = workspace.model_dump(mode="json")
                return DashboardSnapshotView.model_validate(data)
    return snapshot


def _project_header(repo_name: str, count: int, *, dark: bool) -> Text:
    text = Text()
    text.append(repo_name, style="bold")
    text.append(f"  ({count})", style=chrome_color("muted", dark=dark))
    return text
