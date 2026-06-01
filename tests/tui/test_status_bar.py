"""StatusBar contract: state classes, segment composition, flash behavior.

Pins what the user sees on the workbench bar at the bottom of the list
screen — the redesigned VS-Code-style row. Each test targets one
contract: brand state class on background, count chip composition,
selection summary, filter chip, flash auto-clear, narrow-tier collapse.
"""

from __future__ import annotations

import shutil
from dataclasses import replace as _dc_replace
from pathlib import Path

import pytest
from rich.text import Text

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceStatus
from grove.tui.app import GroveApp
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


def _plain(bar: StatusBar) -> str:
    rendered = bar.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


# ─── state classes (brand background) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_fleet_sets_empty_class(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """No workspaces → `-empty` class flips bg to neutral panel."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        assert bar.has_class("-empty"), "empty fleet should set -empty class"
        assert not bar.has_class("-attention"), "empty is not attention-worthy"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_healthy_fleet_has_no_state_classes(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A live workspace → default brand bg (no -empty, no -attention)."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        assert not bar.has_class("-empty")
        assert not bar.has_class("-attention")
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_orphaned_workspace_sets_attention_class(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Any ORPHANED workspace flips bg to amber via -attention class."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="orphan"))
    # Drop session AND worktree path so the reconciler reports ORPHANED.
    fake_tmux.sessions.discard(state.tmux_session)
    shutil.rmtree(state.worktree_path, ignore_errors=True)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        assert bar.has_class("-attention"), "ORPHANED workspace should set -attention class"
        await pilot.press("q")
        await pilot.pause()


# ─── segment composition ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brand_segment_shows_repo_name(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """⌂ + repo name renders on the bar's left edge."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        assert "⌂" in plain, f"brand glyph missing: {plain!r}"
        assert tmp_repo.name in plain, f"repo name missing: {plain!r}"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_count_chip_renders_for_active_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """One ACTIVE workspace → ● 1 chip in the left zone."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        assert "● 1" in plain, f"active chip missing: {plain!r}"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_zero_count_statuses_are_omitted(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Statuses with zero count produce no chip — keeps the bar clean."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        # No paused workspaces → no `‖` paused chip.
        assert "‖" not in plain, f"paused glyph should not render at zero count: {plain!r}"
        # No offline → no `○`.
        assert "○" not in plain, f"offline glyph should not render at zero count: {plain!r}"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_count_breakdown_aggregates_multiple_statuses(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Two live + one paused → two chips: ● 2 and ‖ 1."""
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    paused = manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))
    manager.pause(paused.id)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        # `count` property is the convenience sum of breakdown values.
        assert bar.count == 2, f"count should equal sum of breakdown: {bar.breakdown}"
        plain = _plain(bar)
        assert "● 1" in plain or "◐ 1" in plain, plain
        assert "‖ 1" in plain, plain
        del fake_tmux
        await pilot.press("q")
        await pilot.pause()


# ─── selection summary ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_selection_summary_shows_title_branch_status(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Selected workspace → summary segment with title, branch, status label."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        # Selection glyph + chrome divider + branch glyph + status glyph all present.
        assert "›" in plain, f"selection glyph missing: {plain!r}"  # noqa: RUF001
        assert "│" in plain, f"divider missing: {plain!r}"
        assert "⎇" in plain, f"branch glyph missing: {plain!r}"
        assert state.title in plain, plain
        assert state.branch in plain, plain
        # Status label (active/idle): both are valid post-reconciliation.
        assert "active" in plain or "idle" in plain, plain
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_active_selection_summary_swells_with_pulse_frame(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """ACTIVE selection's summary glyph swaps in lockstep with `pulse_frame`.

    Pins both contracts at once: (1) the StatusBar consumes the screen's
    pulse clock, and (2) only the ACTIVE branch of `_render_summary` reads
    it — bumping the frame on a non-ACTIVE selection MUST NOT change the
    rendered glyph (idle/paused/etc. would contradict their semantics).
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        bar = screen.query_one(StatusBar)

        # Stop the screen's auto-pulse timer so it doesn't race with the
        # test's manual ``bar.pulse_frame =`` assignments. On macOS / Windows
        # CI runners the 250ms ``set_interval`` can fire inside ``pilot.pause()``
        # and overwrite the value before ``_plain(bar)`` reads the rendered
        # glyph — Linux schedules differently and never raced. Stopping the
        # timer makes the test drive the only writer.
        #
        # The timer may have *already fired* during the initial ``pilot.pause``
        # above on slow Windows runners, so reset the bar's frame back to a
        # known starting value after stopping.
        if screen._pulse_timer is not None:
            screen._pulse_timer.stop()
        bar.pulse_frame = 0

        # Force the selection into a known ACTIVE state — the FakeTmux
        # setup may reconcile to IDLE depending on the activity threshold,
        # which would make the test flaky on its own assertion.
        bar.selection = _dc_replace(state, status=WorkspaceStatus.ACTIVE)
        await pilot.pause()

        bar.pulse_frame = 0
        await pilot.pause()
        rest_plain = _plain(bar)

        bar.pulse_frame = 1
        await pilot.pause()
        swell_plain = _plain(bar)

        assert rest_plain != swell_plain, (
            f"ACTIVE summary glyph must swap with pulse_frame; rest={rest_plain!r}"
        )
        assert "●" in rest_plain, rest_plain
        assert "◉" in swell_plain, swell_plain

        # Now flip the same selection to PAUSED; pulse_frame must not affect render.
        bar.selection = _dc_replace(state, status=WorkspaceStatus.PAUSED)
        bar.pulse_frame = 0
        await pilot.pause()
        paused_0 = _plain(bar)
        bar.pulse_frame = 1
        await pilot.pause()
        paused_1 = _plain(bar)
        assert paused_0 == paused_1, "non-ACTIVE selection must ignore pulse_frame"

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_no_selection_no_summary(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """Empty fleet → no selection, no summary, no divider."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        # No selection means none of the selection-summary chrome glyphs render.
        assert "›" not in plain, plain  # noqa: RUF001
        assert "│" not in plain, plain
        assert "⎇" not in plain, plain
        await pilot.press("q")
        await pilot.pause()


# ─── filter chip ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_chip_appears_when_filter_active(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Typing in the filter bar shows ⌕ "<query>" on the right."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "alp":
            await pilot.press(ch)
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        assert "⌕" in plain, f"filter glyph missing: {plain!r}"
        assert '"alp"' in plain, f"filter query missing: {plain!r}"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


# ─── theme chip (width-tier sensitive) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_theme_chip_shows_at_wide_width(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """At ≥110 cols the theme indicator (dark/light) renders on the right."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        assert "dark" in plain or "light" in plain, f"theme chip missing: {plain!r}"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_theme_chip_drops_at_medium_width(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """At 80-109 cols the theme indicator is dropped to free space."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(95, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        plain = _plain(bar)
        assert "dark" not in plain and "light" not in plain, (
            f"theme chip should drop at medium width: {plain!r}"
        )
        await pilot.press("q")
        await pilot.pause()


# ─── flash messages ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flash_renders_in_selection_slot(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A flash message replaces the selection summary."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        bar.flash("hello world", level="info")
        await pilot.pause()
        plain = _plain(bar)
        assert "hello world" in plain, plain
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_flash_clears_when_message_emptied(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Calling flash('') immediately clears any active flash."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        bar.flash("temporary", level="success")
        await pilot.pause()
        assert "temporary" in _plain(bar)
        bar.flash("")
        await pilot.pause()
        assert "temporary" not in _plain(bar)
        await pilot.press("q")
        await pilot.pause()


# ─── breakdown reactive on screen events ────────────────────────────────────


@pytest.mark.asyncio
async def test_breakdown_updates_when_workspace_paused(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Pausing a workspace flips its bucket from ACTIVE → PAUSED in breakdown."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        # Pre-pause: at least one ACTIVE/IDLE bucket.
        assert (
            bar.breakdown.get(WorkspaceStatus.ACTIVE, 0)
            + bar.breakdown.get(WorkspaceStatus.IDLE, 0)
            >= 1
        )
        manager.pause(state.id)
        await pilot.pause()
        # Manager event triggers _refresh which rebuilds breakdown.
        assert bar.breakdown.get(WorkspaceStatus.PAUSED, 0) == 1, bar.breakdown
        await pilot.press("q")
        await pilot.pause()
