"""Pilot smoke tests for WorkspaceListScreen — screen mounts, populates,
refreshes, and quits without raising."""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from pathlib import Path

import pytest

from grove.core.activity import SessionActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceStatus
from grove.tui.app import GroveApp
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.widgets.card import WorkspaceCard
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.status import StatusBar
from tests.conftest import FakeTmux


def _manager(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.mark.asyncio
async def test_list_screen_renders_empty_and_quits(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux  # patches grove.core.tmux module funcs
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(WorkspaceList)
        status = screen.query_one(StatusBar)
        assert len(table.visible_states) == 0
        assert status.count == 0
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_shows_existing_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="visible"))

    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(WorkspaceList)
        status = screen.query_one(StatusBar)
        assert len(table.visible_states) == 1
        assert status.count == 1
        # The selected row should be the one we created.
        assert table.selected_id == state.id
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_refresh_picks_up_new_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)

    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one(WorkspaceList)
        assert len(table.visible_states) == 0

        # Create after the screen is mounted; press 'r' to refresh.
        manager.create(CreateWorkspaceRequest(agent_name="claude", title="late"))
        await pilot.press("r")
        await pilot.pause()
        assert len(table.visible_states) == 1
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_tick_pulse_propagates_frame_to_card_and_status_bar(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """One tick of the pulse clock advances every WorkspaceCard's
    `pulse_frame` reactive plus the StatusBar's. Pins the screen → list →
    card propagation chain end-to-end. Forces the visible card to ACTIVE
    so `_tick_pulse`'s gate (any visible row is ACTIVE?) passes; without
    the force the FakeTmux setup may reconcile to IDLE and the tick would
    early-exit."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        ws_list = screen.query_one(WorkspaceList)
        bar = screen.query_one(StatusBar)
        card = ws_list.query_one(WorkspaceCard)

        # Stop the auto-pulse timer so manual ``_tick_pulse()`` calls below
        # are the only writers. The 250ms ``set_interval`` on macOS / Windows
        # CI can fire inside ``pilot.pause()`` (initial mount AND between
        # manual ticks), which bumps the counter and breaks both the
        # initial-state and modulo-wraparound assertions. Linux happens to
        # schedule outside that window and never raced.
        assert screen._pulse_timer is not None, "pulse timer must be wired in on_mount"
        screen._pulse_timer.stop()

        # Reset frame state to a known zero — the timer may have fired once
        # during the initial pilot.pause above before we got to stop it.
        screen._pulse_frame = 0
        ws_list.set_pulse_frame(0)
        bar.pulse_frame = 0
        await pilot.pause()
        assert card.pulse_frame == 0
        assert bar.pulse_frame == 0

        # Force the card's underlying state to ACTIVE so _tick_pulse's
        # gate fires (it requires at least one ACTIVE visible row).
        active_state = _dc_replace(card._state, status=WorkspaceStatus.ACTIVE)
        ws_list._states = [active_state]
        card.state = active_state
        await pilot.pause()

        # Drive one tick deliberately; don't rely on the timer's wallclock.
        screen._tick_pulse()
        await pilot.pause()
        assert card.pulse_frame == 1, "tick must propagate to mounted cards"
        assert bar.pulse_frame == 1, "tick must propagate to the status bar"

        # Frame wraps to 0 on the next tick — confirms modulo arithmetic.
        screen._tick_pulse()
        await pilot.pause()
        assert card.pulse_frame == 0
        assert bar.pulse_frame == 0

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_tick_pulse_skips_when_no_active_row_visible(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Resource-saving gate: when no visible workspace is ACTIVE, the tick
    must early-exit and the frame must NOT advance. Pulsing an idle/paused
    fleet is wasted CPU and contradicts the semantic ('●' means producing
    output right now)."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        ws_list = screen.query_one(WorkspaceList)
        card = ws_list.query_one(WorkspaceCard)

        # Force the card's status to a non-ACTIVE value so the gate fails.
        paused_state = _dc_replace(card._state, status=WorkspaceStatus.PAUSED)
        ws_list._states = [paused_state]
        card.state = paused_state
        await pilot.pause()

        before = screen._pulse_frame
        screen._tick_pulse()
        await pilot.pause()
        assert screen._pulse_frame == before, "pulse must not advance when no visible row is ACTIVE"

        await pilot.press("q")
        await pilot.pause()


class _FakeActivityService:
    """Stands in for ``ActivityService`` — the list screen only calls
    ``sessions_for``. Returns one WORKING session per workspace so the
    tick has a deterministic agent axis without touching the filesystem."""

    def sessions_for(self, mgr: object, state: object) -> list[SessionActivity]:
        del mgr, state
        session = AgentSession(
            session_id="s-1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
            tmux_window="agent",
        )
        return [
            SessionActivity(
                session=session,
                activity=AgentActivity(state=AgentActivityState.WORKING),
            )
        ]


@pytest.mark.asyncio
async def test_stats_tick_pushes_agent_state_to_cards(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The slow stats tick recomputes the agent axis via the injected
    service's public ``sessions_for`` and pushes each visible row's primary
    state onto its WorkspaceCard (screen → list → card chain). Injection
    mirrors ``tests/tui/test_dashboard.py``; the interval timers are
    stopped so the manual tick is the only writer (pulse-timer lesson)."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = WorkspaceListScreen(manager, service=_FakeActivityService())  # type: ignore[arg-type]
        app.push_screen(screen)
        await pilot.pause()
        for attr in ("_stats_timer", "_pane_timer", "_pulse_timer"):
            timer = getattr(screen, attr)
            assert timer is not None, f"{attr} must be wired in on_mount"
            timer.stop()
        card = screen.query_one(WorkspaceCard)
        assert "working" not in card.body_text  # no segment before the tick

        screen._tick_stats()
        await pilot.pause()
        assert "working" in card.body_text, "tick must push the agent state to the card"

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_stats_tick_clears_agent_state_for_sessionless_rows(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A row whose workspace has no agent session gets ``None`` pushed —
    the segment clears and the card returns to its byte-identical
    agent-less render."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))

    class _SessionlessService:
        def sessions_for(self, mgr: object, state: object) -> list[SessionActivity]:
            del mgr, state
            return []

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = WorkspaceListScreen(manager, service=_SessionlessService())  # type: ignore[arg-type]
        app.push_screen(screen)
        await pilot.pause()
        for attr in ("_stats_timer", "_pane_timer", "_pulse_timer"):
            getattr(screen, attr).stop()
        card = screen.query_one(WorkspaceCard)
        # Seed a stale segment, then let the tick clear it.
        card.set_agent_state(AgentActivityState.WORKING)
        await pilot.pause()
        assert "working" in card.body_text

        screen._tick_stats()
        await pilot.pause()
        assert "working" not in card.body_text, "sessionless rows must clear the segment"

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_create_modal_creates_workspace_via_keybindings(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        # n opens the create modal.
        await pilot.press("n")
        await pilot.pause()
        # Title input is focused on mount; type a title.
        for ch in "hello":
            await pilot.press(ch)
        await pilot.pause()
        # Ctrl-S submits.
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Workspace exists in the manager and shows up in the table.
        states = manager.list()
        assert len(states) == 1
        assert states[0].title == "hello"
        list_screen = app.screen
        table = list_screen.query_one(WorkspaceList)
        assert len(table.visible_states) == 1
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_enter_and_a_both_trigger_attach(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Enter must work as well as `a` despite DataTable consuming `enter`.

    DataTable defines its own `Binding("enter", "select_cursor")`, which
    shadows the screen-level `enter,a` binding while the table is focused.
    Without on_data_table_row_selected forwarding, only `a` would attach.
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="row-1"))

    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        list_screen = app.screen
        calls: list[str] = []
        # Spy on the action so we don't actually exec `tmux attach`.
        list_screen.action_attach_workspace = lambda: calls.append("hit")  # type: ignore[method-assign]

        await pilot.press("a")
        await pilot.pause()
        assert calls == ["hit"], "`a` should trigger attach"

        await pilot.press("enter")
        await pilot.pause()
        assert calls == ["hit", "hit"], "`enter` should also trigger attach"

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_o_key_respawns_offline_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Press 'o' on a workspace whose tmux session has vanished — manager
    re-creates the session and the table reflects the live state."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="offlinable"))
    # Simulate the tmux session vanishing externally.
    fake_tmux.sessions.discard(state.tmux_session)
    # Sanity: list() reports OFFLINE before the user presses 'o'.
    assert manager.list()[0].status.value == "offline"

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        # Session is back and the workspace is live again.
        assert state.tmux_session in fake_tmux.sessions
        assert manager.list()[0].status.value in {"active", "idle"}
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_o_key_no_op_for_active_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """'o' on a live workspace flashes a hint instead of touching tmux."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alive"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        # Workspace remains active (no respawn happened).
        assert manager.list()[0].status.value in {"active", "idle"}
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_create_modal_cancel_does_nothing(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert manager.list() == []
        await pilot.press("q")
        await pilot.pause()


# ─── card focus chrome (TCSS-only) ──────────────────────────────────────────


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    s = value.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _rgb_close(a: tuple[int, int, int], b: tuple[int, int, int], *, atol: int = 2) -> bool:
    """Approximate per-channel RGB equality.

    Textual's `Color.hex` accessor round-trips through float math and
    occasionally rounds a channel by ±1 (e.g. ``#d97757`` re-emerges as
    ``#d87757``). For style assertions we don't care about that delta —
    we care that the resolved color is *the configured one*, not a
    different palette slot. ±2 per channel is wide enough to absorb the
    rounding and tight enough to fail if a wrong palette slot lands.
    """
    return all(abs(x - y) <= atol for x, y in zip(a, b, strict=True))


@pytest.mark.asyncio
async def test_highlighted_card_is_fully_framed_in_clay(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The highlighted card carries a *full* clay border (top, right,
    bottom, left) when the parent list has focus — turning the focused
    row into a fully framed panel rather than a row with a left rule.
    Unhighlighted cards keep the default `$surface`-coloured border so
    they're sized identically (no layout shift on cursor move) but read
    as transparent breathing room against the list bg.

    Pinning all four edges defends against partial regressions where
    only some edges are styled and the focus chrome reads as half-broken.
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        wlist = app.screen.query_one(WorkspaceList)
        # Force the cursor to the first row deterministically — sort order
        # depends on creation timestamps and isn't part of this contract.
        wlist.jump_to(0)
        await pilot.pause()

        cards = list(wlist.query(WorkspaceCard))
        assert len(cards) == 2
        highlighted = next(c for c in cards if c.has_class("-highlight"))
        unhighlighted = next(c for c in cards if not c.has_class("-highlight"))

        primary_hex = app.current_theme.primary
        surface_hex = app.current_theme.surface
        assert primary_hex is not None and surface_hex is not None
        primary_rgb = _hex_to_rgb(primary_hex)
        surface_rgb = _hex_to_rgb(surface_hex)

        # Round border on every edge. Style + color asserted on each
        # so a future TCSS edit that styles only the top/bottom (say)
        # fails immediately.
        for edge in ("border_top", "border_right", "border_bottom", "border_left"):
            hi_style, hi_color = getattr(highlighted.styles, edge)
            un_style, un_color = getattr(unhighlighted.styles, edge)
            assert hi_style == "round", f"highlighted {edge} should be 'round'; got {hi_style!r}"
            assert un_style == "round", f"unhighlighted {edge} should be 'round'; got {un_style!r}"
            assert hi_color is not None and un_color is not None
            assert _rgb_close(hi_color.rgb, primary_rgb), (
                f"highlighted {edge} should match {primary_hex} ({primary_rgb}); "
                f"got {hi_color.hex} ({hi_color.rgb})"
            )
            assert _rgb_close(un_color.rgb, surface_rgb), (
                f"unhighlighted {edge} should match {surface_hex} ({surface_rgb}); "
                f"got {un_color.hex} ({un_color.rgb})"
            )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_hovered_card_gets_secondary_outline(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Mouse hover over a non-selected card paints a `$secondary` outline
    so the user sees mouse position separately from keyboard selection.

    Why this regression-tests: Textual's default `ListItem:hover` uses
    `$boost`, which is always transparent on Grove themes (CLAUDE.md
    `$boost` lesson). Without an explicit hover rule the user sees zero
    feedback when mousing over rows. This test pins the rule so a future
    TCSS edit can't silently delete it again.

    The selection rule (`WorkspaceList:focus > WorkspaceCard.-highlight`)
    is more specific, so hovering the keyboard-selected row keeps its
    clay chrome — checked here too.
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        wlist = app.screen.query_one(WorkspaceList)
        wlist.jump_to(0)
        await pilot.pause()

        cards = list(wlist.query(WorkspaceCard))
        unhighlighted = next(c for c in cards if not c.has_class("-highlight"))
        highlighted = next(c for c in cards if c.has_class("-highlight"))

        await pilot.hover(unhighlighted)
        await pilot.pause()

        secondary_hex = app.current_theme.secondary
        primary_hex = app.current_theme.primary
        assert secondary_hex is not None and primary_hex is not None
        secondary_rgb = _hex_to_rgb(secondary_hex)
        primary_rgb = _hex_to_rgb(primary_hex)

        # Hovered (non-selected) card → muted gray outline on every edge.
        for edge in ("border_top", "border_right", "border_bottom", "border_left"):
            style, color = getattr(unhighlighted.styles, edge)
            assert style == "round"
            assert color is not None
            assert _rgb_close(color.rgb, secondary_rgb), (
                f"hovered {edge} should match secondary {secondary_hex} ({secondary_rgb}); "
                f"got {color.hex} ({color.rgb})"
            )

        # Selection out-specifies hover — the highlighted card stays clay
        # while the unhighlighted neighbour is being hovered.
        for edge in ("border_top", "border_right", "border_bottom", "border_left"):
            _, color = getattr(highlighted.styles, edge)
            assert color is not None
            assert _rgb_close(color.rgb, primary_rgb), (
                f"selected {edge} should stay primary while another card is hovered; "
                f"got {color.hex} ({color.rgb})"
            )

        await pilot.press("q")
        await pilot.pause()


# ─── edit (rename + description) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e_key_opens_edit_modal_with_current_state(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Pressing 'e' on a selected workspace opens EditWorkspaceScreen
    pre-filled with the workspace's current title + description."""
    from textual.widgets import Input  # noqa: PLC0415

    from grove.tui.screens.edit import EditWorkspaceScreen  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="initial"))

    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, EditWorkspaceScreen)
        assert app.screen.query_one("#title", Input).value == "initial"
        assert app.screen.query_one("#description", Input).value == ""
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_e_then_submit_renames_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Edit modal Ctrl+S → manager.update is called and the card title
    refreshes to the new value."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="initial"))

    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        # Erase the existing "initial" then type "renamed".
        for _ in range(len("initial")):
            await pilot.press("backspace")
        for ch in "renamed":
            await pilot.press(ch)
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Workspace was renamed in the manager.
        assert manager.get(state.id).title == "renamed"
        # Identity stayed.
        reloaded = manager.get(state.id)
        assert reloaded.tmux_session == state.tmux_session
        assert reloaded.worktree_path == state.worktree_path
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_e_with_no_selection_flashes(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Empty list → 'e' is a no-op (modal does not open)."""
    from grove.tui.screens.edit import EditWorkspaceScreen  # noqa: PLC0415

    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert not isinstance(app.screen, EditWorkspaceScreen)
        await pilot.press("q")
        await pilot.pause()
