"""The create modal's working-directory picker.

Follows the model picker's shape exactly — sentinel choices at each end, real
values in between, free text revealed only for the explicit "Other" — so these
tests pin the two things that are genuinely this picker's own: the VALUE that
reaches the request is a path (never a label), and the pre-selected row is the
repo's resolved default rather than a guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Select

from grove.core.config import AgentSpec, GroveConfig
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.tui.screens.create import CWD_CUSTOM, CWD_ROOT, CreateWorkspaceScreen


class _CreateHost(App[None]):
    def __init__(self, screen: CreateWorkspaceScreen) -> None:
        super().__init__()
        self._create_screen = screen
        self.result: CreateWorkspaceRequest | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(self._create_screen, self._captured)

    def _captured(self, value: CreateWorkspaceRequest | None) -> None:
        self.result = value


def _config(entries: dict[str, str] | None = None, default: str | None = None) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "agents": [AgentSpec(name="alpha", command="alpha").model_dump()],
            "agent_cwds": {"entries": entries or {}, "default": default},
        }
    )


def _screen(cfg: GroveConfig, repo_root: Path) -> CreateWorkspaceScreen:
    return CreateWorkspaceScreen(
        cfg.agents,
        cfg=cfg,
        repo_root=repo_root,
        local_branches=(BranchInfo(name="main", kind="local", is_current=True),),
    )


def _option_values(select: Select) -> list[object]:
    return [value for _, value in select._options]  # type: ignore[attr-defined]


async def test_options_are_root_then_each_declared_path_then_custom(tmp_path: Path) -> None:
    cfg = _config({"Web app": "webapp", "Engine": "src/grove"})
    app = _CreateHost(_screen(cfg, tmp_path))
    async with app.run_test() as pilot:
        select = app.screen.query_one("#cwd", Select)
        assert _option_values(select) == [CWD_ROOT, "webapp", "src/grove", CWD_CUSTOM]
        await pilot.pause()


async def test_the_configured_default_is_preselected(tmp_path: Path) -> None:
    """The engine applies this same default for an omitted field, so a form
    showing anything else would disagree with what the create is about to do."""
    cfg = _config({"Web app": "webapp", "Engine": "src/grove"}, default="Engine")
    app = _CreateHost(_screen(cfg, tmp_path))
    async with app.run_test() as pilot:
        assert app.screen.query_one("#cwd", Select).value == "src/grove"
        await pilot.pause()


async def test_a_repo_declaring_none_preselects_the_repo_root(tmp_path: Path) -> None:
    app = _CreateHost(_screen(_config(), tmp_path))
    async with app.run_test() as pilot:
        select = app.screen.query_one("#cwd", Select)
        assert select.value is CWD_ROOT
        assert _option_values(select) == [CWD_ROOT, CWD_CUSTOM]
        await pilot.pause()


async def test_submitting_a_labelled_entry_sends_its_PATH(tmp_path: Path) -> None:
    """The label is presentation only — renaming one must never invalidate a
    workspace already created from it, which is only true if the path crosses."""
    cfg = _config({"Web app": "webapp"})
    screen = _screen(cfg, tmp_path)
    app = _CreateHost(screen)
    async with app.run_test() as pilot:
        app.screen.query_one("#cwd", Select).value = "webapp"
        app.screen.query_one("#title", Input).value = "picked"
        await pilot.pause()
        screen.action_submit()
        await pilot.pause()

    assert app.result is not None
    assert app.result.project_cwd == Path("webapp")


async def test_the_repo_root_choice_sends_nothing(tmp_path: Path) -> None:
    """`None` is the historical shape and still means the worktree root."""
    screen = _screen(_config({"Web app": "webapp"}), tmp_path)
    app = _CreateHost(screen)
    async with app.run_test() as pilot:
        app.screen.query_one("#cwd", Select).value = CWD_ROOT
        app.screen.query_one("#title", Input).value = "rooted"
        await pilot.pause()
        screen.action_submit()
        await pilot.pause()

    assert app.result is not None
    assert app.result.project_cwd is None


async def test_free_text_is_hidden_until_other_is_chosen_and_survives_a_switch(
    tmp_path: Path,
) -> None:
    """Hidden rather than unmounted, so a typed path survives switching to a
    labelled entry and back — the branch blocks' rule applied here."""
    screen = _screen(_config({"Web app": "webapp"}), tmp_path)
    app = _CreateHost(screen)
    async with app.run_test() as pilot:
        custom = app.screen.query_one("#cwd-custom", Input)
        assert custom.has_class("-hidden")

        app.screen.query_one("#cwd", Select).value = CWD_CUSTOM
        await pilot.pause()
        assert not custom.has_class("-hidden")

        custom.value = "services/api"
        app.screen.query_one("#cwd", Select).value = "webapp"
        await pilot.pause()
        assert custom.has_class("-hidden")

        app.screen.query_one("#cwd", Select).value = CWD_CUSTOM
        await pilot.pause()
        assert custom.value == "services/api"

        app.screen.query_one("#title", Input).value = "typed"
        screen.action_submit()
        await pilot.pause()

    assert app.result is not None
    assert app.result.project_cwd == Path("services/api")


@pytest.mark.parametrize("typed", ["", "   "])
async def test_other_with_nothing_typed_falls_back_to_the_root(tmp_path: Path, typed: str) -> None:
    screen = _screen(_config(), tmp_path)
    app = _CreateHost(screen)
    async with app.run_test() as pilot:
        app.screen.query_one("#cwd", Select).value = CWD_CUSTOM
        await pilot.pause()
        app.screen.query_one("#cwd-custom", Input).value = typed
        app.screen.query_one("#title", Input).value = "blank"
        screen.action_submit()
        await pilot.pause()

    assert app.result is not None
    assert app.result.project_cwd is None
