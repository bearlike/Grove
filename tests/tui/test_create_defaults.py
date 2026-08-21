"""Pilot coverage for persisted create-form defaults and model selection."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Input, RadioButton, RadioSet, Select

from grove.core.config import AgentSpec, DefaultsScope, GroveConfig, WorkspaceDefaults
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.tui.screens.create import MODEL_CUSTOM, MODEL_DEFAULT, CreateWorkspaceScreen
from grove.tui.screens.save_defaults import SaveDefaultsScreen


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


class _SaveDefaultsHost(App[None]):
    def __init__(self, screen: SaveDefaultsScreen) -> None:
        super().__init__()
        self._save_screen = screen
        self.result: DefaultsScope | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(self._save_screen, self._captured)

    def _captured(self, value: DefaultsScope | None) -> None:
        self.result = value


def _agents() -> list[AgentSpec]:
    return [
        AgentSpec(
            name="alpha",
            command="alpha",
            models=("alpha-fast", "shared"),
        ),
        AgentSpec(
            name="beta",
            command="beta",
            models=("beta-pro", "shared"),
        ),
    ]


def _config(defaults: WorkspaceDefaults | None = None) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "agents": [agent.model_dump() for agent in _agents()],
            "container": {"enabled": True},
            "brief": {"enabled": True},
            "defaults": (defaults or WorkspaceDefaults()).model_dump(exclude_none=True),
        }
    )


def _screen(cfg: GroveConfig, repo_root: Path) -> CreateWorkspaceScreen:
    return CreateWorkspaceScreen(
        cfg.agents,
        cfg=cfg,
        repo_root=repo_root,
        local_branches=(
            BranchInfo(name="main", kind="local", is_current=True),
            BranchInfo(name="release", kind="local"),
        ),
    )


def _option_values(select: Select) -> list[object]:
    return [value for _, value in select._options]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_create_defaults_pre_fill_every_owned_widget(tmp_path: Path) -> None:
    defaults = WorkspaceDefaults(
        agent="alpha",
        runtime="host",
        brief=False,
        model="alpha-fast",
        branch_mode="new",
        base_ref="main",
        skip_init=True,
    )
    app = _CreateHost(_screen(_config(defaults), tmp_path))

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        assert screen.query_one("#agent", Select).value == "alpha"
        assert screen.query_one("#runtime", Select).value == "host"
        assert screen.query_one("#brief", Select).value == "off"
        assert screen.query_one("#model", Select).value == "alpha-fast"
        assert screen.query_one("#skip-init", Checkbox).value is True
        assert screen.query_one("#branch-mode", RadioSet).pressed_index == 1
        assert screen.query_one("#new-base", Select).value == "main"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_stale_default_agent_falls_back_to_first_roster_entry(tmp_path: Path) -> None:
    app = _CreateHost(_screen(_config(WorkspaceDefaults(agent="removed-agent")), tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#agent", Select).value == "alpha"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_read_defaults_round_trips_current_form_without_writing(tmp_path: Path) -> None:
    expected = WorkspaceDefaults(
        agent="beta",
        runtime="host",
        brief=False,
        model="beta-pro",
        branch_mode="new",
        base_ref="main",
        skip_init=True,
    )
    cfg = _config(expected)
    before = set(tmp_path.rglob("*"))
    app = _CreateHost(_screen(cfg, tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        saved = screen.read_defaults()
        assert saved == expected
        assert set(tmp_path.rglob("*")) == before
        await pilot.press("escape")
        await pilot.pause()

    round_trip_cfg = cfg.model_copy(update={"defaults": saved})
    round_trip_app = _CreateHost(_screen(round_trip_cfg, tmp_path))
    async with round_trip_app.run_test() as pilot:
        await pilot.pause()
        screen = round_trip_app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        assert screen.read_defaults() == saved
        assert screen.query_one("#new-base", Select).value == "main"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_model_picker_tracks_the_selected_agent_catalog(tmp_path: Path) -> None:
    defaults = WorkspaceDefaults(agent="alpha", model="shared")
    app = _CreateHost(_screen(_config(defaults), tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        agent = screen.query_one("#agent", Select)
        model = screen.query_one("#model", Select)
        assert _option_values(model)[1:-1] == ["alpha-fast", "shared"]

        agent.value = "beta"
        await pilot.pause()
        assert _option_values(model)[1:-1] == ["beta-pro", "shared"]
        assert model.value == "shared"

        agent.value = "alpha"
        await pilot.pause()
        model.value = "alpha-fast"
        await pilot.pause()
        agent.value = "beta"
        await pilot.pause()
        assert model.value is MODEL_DEFAULT
        assert screen.read_defaults().model is None
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_custom_model_override_submits_an_unlisted_model_verbatim(
    tmp_path: Path,
) -> None:
    app = _CreateHost(_screen(_config(), tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        model = screen.query_one("#model", Select)
        model.value = MODEL_CUSTOM
        await pilot.pause()
        custom = screen.query_one("#model-custom", Input)
        assert not custom.has_class("-hidden")
        custom.value = "gateway/unlisted-v9"
        title = screen.query_one("#title", Input)
        title.value = "custom model"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert app.result is not None
    assert app.result.model == "gateway/unlisted-v9"


@pytest.mark.asyncio
async def test_agent_default_model_submits_none(tmp_path: Path) -> None:
    app = _CreateHost(_screen(_config(), tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CreateWorkspaceScreen)
        assert screen.query_one("#model", Select).value is MODEL_DEFAULT
        screen.query_one("#title", Input).value = "agent selected"
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert app.result is not None
    assert app.result.model is None


@pytest.mark.asyncio
async def test_save_defaults_returns_selected_scope_and_prefills_project(
    tmp_path: Path,
) -> None:
    app = _SaveDefaultsHost(SaveDefaultsScreen(repo_root=tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SaveDefaultsScreen)
        assert screen.query_one("#defaults-scope", RadioSet).pressed_index == 1
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert app.result is DefaultsScope.PROJECT

    local_app = _SaveDefaultsHost(SaveDefaultsScreen(repo_root=tmp_path))
    async with local_app.run_test() as pilot:
        await pilot.pause()
        screen = local_app.screen
        assert isinstance(screen, SaveDefaultsScreen)
        screen.query_one("#scope-project-local", RadioButton).value = True
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert local_app.result is DefaultsScope.PROJECT_LOCAL


@pytest.mark.asyncio
async def test_save_defaults_cancel_and_no_repo_scope_guards() -> None:
    app = _SaveDefaultsHost(SaveDefaultsScreen(repo_root=None))

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SaveDefaultsScreen)
        assert screen.query_one("#scope-project", RadioButton).disabled is True
        assert screen.query_one("#scope-project-local", RadioButton).disabled is True
        await pilot.press("escape")
        await pilot.pause()

    assert app.result is None
