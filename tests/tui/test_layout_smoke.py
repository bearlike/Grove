"""End-to-end Pilot smoke: the redesigned list screen mounts cleanly with
all the new chrome — peek rail, contextual footer, filter bar, empty banner —
without any boot-time exception.

If a future change accidentally drops a widget from compose() or breaks
the empty-state CSS, this catches it before any narrower test does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp
from grove.tui.widgets.filter_bar import FilterBar
from grove.tui.widgets.footer import ContextualFooter
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.peek_rail import PeekRail
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
async def test_screen_mounts_with_full_chrome_present(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="smoke"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Every redesigned widget is present in the live DOM.
        screen.query_one(WorkspaceList)
        screen.query_one(PeekRail)
        screen.query_one(FilterBar)
        screen.query_one(StatusBar)
        screen.query_one(ContextualFooter)
        screen.query_one("#empty-banner")
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_full_user_flow_create_filter_jump_kill(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """End-to-end: create two workspaces via TUI, filter to one, jump back to
    full list, kill the survivor, and confirm the empty banner reappears."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.has_class("-empty")

        # Create two via the modal.
        for title in ("alpha", "beta"):
            await pilot.press("n")
            await pilot.pause()
            for ch in title:
                await pilot.press(ch)
            await pilot.press("ctrl+s")
            await pilot.pause()
        assert len(manager.list()) == 2

        # Filter to alpha only.
        await pilot.press("slash")
        await pilot.pause()
        for ch in "alp":
            await pilot.press(ch)
        await pilot.pause()
        wlist = screen.query_one(WorkspaceList)
        assert len(wlist.visible_states) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(wlist.visible_states) == 2

        # Kill the highlighted (top) workspace via k → confirm dialog → 'y'.
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert len(manager.list()) == 1

        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert len(manager.list()) == 0
        # Empty banner returns.
        assert app.screen.has_class("-empty")

        await pilot.press("q")
        await pilot.pause()
