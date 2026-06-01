"""Pilot tests for filter, jump keys, contextual footer, empty state.

These pin the user-visible R-E behaviors:
- `/` reveals the filter bar; typing narrows the table.
- Esc clears the filter and returns focus to the table.
- Number keys 1-9 move the cursor to that visible row.
- Footer shows global keys always; selection keys dim when nothing is selected.
- Empty banner replaces the table when there are no workspaces.
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


# ─── filter ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slash_opens_filter_and_typing_narrows_table(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one(WorkspaceList)
        assert len(table.visible_states) == 2

        await pilot.press("slash")
        await pilot.pause()
        bar = app.screen.query_one(FilterBar)
        assert bar.has_class("-active")

        # Type "alp" — should leave only the alpha workspace visible.
        for ch in "alp":
            await pilot.press(ch)
        await pilot.pause()
        assert len(table.visible_states) == 1
        assert table.visible_states[0].title == "alpha"

        await pilot.press("escape")
        await pilot.pause()
        # Filter cleared, both rows back.
        assert not bar.has_class("-active")
        assert len(table.visible_states) == 2

        await pilot.press("q")
        await pilot.pause()


# ─── jump keys ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_number_keys_jump_cursor_to_row(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    s1 = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    s2 = manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))
    s3 = manager.create(CreateWorkspaceRequest(agent_name="claude", title="gamma"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one(WorkspaceList)
        # Cursor lands on row 0 by default; press '3' to jump to row 2.
        await pilot.press("3")
        await pilot.pause()
        third_id = table.visible_states[2].id
        assert table.selected_id == third_id
        assert third_id in {s1.id, s2.id, s3.id}

        await pilot.press("1")
        await pilot.pause()
        first_id = table.visible_states[0].id
        assert table.selected_id == first_id

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_jump_key_out_of_range_is_noop(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="only"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one(WorkspaceList)
        await pilot.press("9")  # only one row, 9 is out of range
        await pilot.pause()
        assert table.selected_id == state.id  # cursor unchanged
        await pilot.press("q")
        await pilot.pause()


# ─── footer ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_footer_dims_selection_keys_when_nothing_selected(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = footer.render()
        # Selection keys (p, R, k, enter/a) should appear with dim markup.
        assert "[dim]" in rendered
        # Global keys render in clay accent (`$primary` = #d97757), bold.
        assert "[bold #d97757]q[/]" in rendered
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_footer_brightens_selection_keys_when_row_selected(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="x"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = footer.render()
        # All keys are bold-clay; no dim markup with a selection.
        assert "[bold #d97757]p[/]" in rendered or "[bold #d97757]k[/]" in rendered
        await pilot.press("q")
        await pilot.pause()


# ─── empty state ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_state_shows_when_no_workspaces(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.has_class("-empty")
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_empty_state_clears_when_workspace_added(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.screen.has_class("-empty")
        manager.create(CreateWorkspaceRequest(agent_name="claude", title="now"))
        await pilot.pause(delay=0.1)
        assert not app.screen.has_class("-empty")
        await pilot.press("q")
        await pilot.pause()
