"""Pilot smoke tests for WorkspaceListScreen — screen mounts, populates,
refreshes, and quits without raising."""

from __future__ import annotations

from dataclasses import replace as _dc_replace
from pathlib import Path

import pytest

from grove.core.activity import SessionActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.release import ReleaseChecker
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceStatus
from grove.tui.app import GroveApp
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.screens.remap_session import RemapSessionScreen
from grove.tui.screens.sessions import SessionRow
from grove.tui.widgets.card import WorkspaceCard
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.status import StatusBar
from tests.conftest import FakeTmux


def _write_transcript(
    claude_home: Path, worktree: Path, session_id: str, *, cwd: Path | None = None
) -> Path:
    """Materialize a real claude transcript so SessionExplorer discovers it.

    Mirrors ``tests/core/test_session_remap.py``'s helper — a genuine
    on-disk session is what lets these tests drive the real
    ``manager.remap_session`` write path (its own internal
    ``SessionExplorer`` resolves session refs against real files, so a
    faked listing id would just bounce as ``AgentSessionNotFound``).
    """
    where = cwd or worktree
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(where)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text(f'{{"type":"user","cwd":"{where}"}}\n', encoding="utf-8")
    return path


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
async def test_release_worker_pushes_update_to_status_bar(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The mount-time release worker flows an injected checker's verdict to the bar (#80)."""
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:  # wide tier so the chip renders
        await pilot.pause()
        # Replace the auto-pushed screen with one wired to a checker that
        # reports a newer release, then let its mount worker run to completion.
        screen = WorkspaceListScreen(
            _manager(tmp_repo, tmp_path),
            release_checker=ReleaseChecker(installed="0.1.0", fetcher=lambda: "v9.9.9"),
        )
        await app.switch_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        bar = screen.query_one(StatusBar)
        assert bar.update_available is True
        assert bar.latest_version == "9.9.9"
        assert "⇡ v9.9.9" in bar.render().plain
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
async def test_stats_tick_picks_up_out_of_band_create_and_kill(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The slow stats tick re-enumerates the workspace set from the store.

    Issue #49: a second TUI / the MCP server / the daemon can create or
    kill a workspace out-of-band; the open TUI's row set was built once at
    mount and never re-read, so the change only appeared on a full restart.
    Now ``_tick_stats`` re-reads ``manager.list()`` and ``populate`` diffs
    by id.

    The out-of-band actor is a *second* manager over the *same* store path
    (a separate process — a second TUI / the MCP server / the daemon). Its
    writes never reach this screen's in-process ``subscribe`` callback, so
    the only way the row appears is the slow tick re-reading the shared
    store. Drives the tick directly (interval timers stopped first per the
    pulse-timer lesson) and asserts: the out-of-band row appears, a second
    tick adds no duplicate, and a removed workspace disappears.
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    first = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    # A second manager over the same store path stands in for a separate
    # process; its lifecycle events never reach this screen's subscription.
    other = WorkspaceManager(repo_root=manager.repo_root, cfg=manager.config, store=manager.store)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        ws_list = screen.query_one(WorkspaceList)
        # Stop the auto-intervals so the manual tick is the only writer
        # (pulse-timer lesson: a live interval can fire inside pilot.pause).
        for attr in ("_stats_timer", "_pane_timer", "_pulse_timer"):
            timer = getattr(screen, attr)
            assert timer is not None, f"{attr} must be wired in on_mount"
            timer.stop()
        assert len(ws_list.visible_states) == 1

        # Out-of-band create: the OTHER manager writes a second workspace to
        # the shared store while this screen is open and idle.
        second = other.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))

        # The set is stale until the slow tick re-enumerates it — this
        # screen's subscription never saw the other manager's event.
        assert len(ws_list.visible_states) == 1

        screen._tick_stats()
        await pilot.pause()
        ids = {s.id for s in ws_list.visible_states}
        assert ids == {first.id, second.id}, "out-of-band create must appear on the slow tick"
        assert len(ws_list.query(WorkspaceCard)) == 2

        # A second tick with no store change is idempotent — no duplicate rows.
        screen._tick_stats()
        await pilot.pause()
        assert len(ws_list.visible_states) == 2
        assert len(ws_list.query(WorkspaceCard)) == 2, "repeated ticks must not duplicate rows"

        # Out-of-band kill: the second workspace disappears on the next tick.
        other.kill(second.id, delete_branch=False)
        screen._tick_stats()
        await pilot.pause()
        assert {s.id for s in ws_list.visible_states} == {first.id}
        assert len(ws_list.query(WorkspaceCard)) == 1, "out-of-band kill must drop the row"

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


# ─── message (steer the agent, issue #38) ────────────────────────────────────


@pytest.mark.asyncio
async def test_m_then_submit_sends_message_to_agent_pane(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """'m' → type → Enter reaches the manager's tmux-inject seam at the
    pane_target-resolved window, and the success flash rides the
    `message_sent` event."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="steerable"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        for ch in "run the tests":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert fake_tmux.sent_texts == [(f"{state.tmux_session}:agent", "run the tests")]
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == "message sent"
        assert bar.flash_level == "success"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_m_refusal_flashes_error_and_never_injects(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A typed refusal (session vanished → OFFLINE) surfaces as an error
    flash — the screen never crashes — and nothing reaches the inject seam."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="gone"))
    fake_tmux.sessions.discard(state.tmux_session)  # vanished externally → OFFLINE

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert fake_tmux.sent_texts == []
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message.startswith("message failed:")
        assert bar.flash_level == "error"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_m_modal_cancel_sends_nothing(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        for ch in "discard me":
            await pilot.press(ch)
        await pilot.press("escape")
        await pilot.pause()
        assert fake_tmux.sent_texts == []
        await pilot.press("q")
        await pilot.pause()


# ─── remap session (manual session pin, issue #132) ─────────────────────────


@pytest.mark.asyncio
async def test_x_opens_remap_picker_and_pinning_a_candidate_calls_manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'x' offers `SessionExplorer.candidates_for`'s (ungated) listing; picking
    one reaches the real `manager.remap_session`, which persists the pin and
    fires the success flash via the `updated`/`session_remapped` event."""
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="broken"))
    hand_started = "cafef00d-1111-2222-3333-444455556666"
    _write_transcript(cfg_home, Path(state.worktree_path), hand_started)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, RemapSessionScreen)
        rows = list(screen.query(SessionRow))
        assert len(rows) == 1
        assert hand_started[:8] in rows[0].body_text
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceListScreen)
        assert manager.get(state.id).agent_session_id == hand_started
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == f"session remapped to {hand_started[:8]}"
        assert bar.flash_level == "success"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_x_modal_escape_cancels_without_remapping(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="broken"))
    original_session_id = state.agent_session_id
    _write_transcript(cfg_home, Path(state.worktree_path), "cafef00d-0000-0000-0000-000000000000")

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, RemapSessionScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceListScreen)
        # Cancel must never write — the pin stays whatever it was pre-modal.
        assert manager.get(state.id).agent_session_id == original_session_id
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_x_with_no_sessions_flashes_and_never_opens_picker(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A workspace with nothing in its directories (the common case — no
    agent has ever run there) flashes rather than opening an empty modal."""
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="fresh"))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceListScreen)
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == "no sessions to remap"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_x_with_no_selection_flashes(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Empty list → 'x' is a no-op (modal does not open)."""
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert not isinstance(app.screen, RemapSessionScreen)
        await pilot.press("q")
        await pilot.pause()


def test_key_available_remap_shares_edit_gate() -> None:
    """Pure-function gate: 'x' (remap) is available everywhere 'e' (edit) is —
    both ride the engine's `ensure_can_update` rule — and dimmed for ORPHANED."""
    from grove.tui.screens.list import _key_available  # noqa: PLC0415

    for status in (
        WorkspaceStatus.ACTIVE,
        WorkspaceStatus.IDLE,
        WorkspaceStatus.RUNNING,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.ERROR,
    ):
        assert _key_available("x", status) is True
    assert _key_available("x", WorkspaceStatus.ORPHANED) is False
