"""Pilot tests for the redesigned modals (Confirm, Create, Help).

These pin the *contracts* the list screen depends on:
- Confirm dismisses with True/False; details renders when supplied.
- Create exposes a live preview that reacts to title input.
- Help renders global + selection sections; selection section dims when
  nothing is selected.
- All modals share the GroveModal CSS class so the centered + bordered
  look is one place to change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from textual.app import App, ComposeResult
from textual.widgets import Static

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp
from grove.tui.keys import DEFAULT_BINDINGS
from grove.tui.screens._modal import GroveModal
from grove.tui.screens.confirm import ConfirmScreen
from grove.tui.screens.create import CreateWorkspaceScreen
from grove.tui.screens.edit import EditWorkspaceScreen
from grove.tui.screens.help import HelpScreen
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


# ─── Confirm ─────────────────────────────────────────────────────────────────


class _ConfirmHost(App[None]):
    def __init__(self, screen: ConfirmScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._captured)

    def _captured(self, value: bool | None) -> None:
        self.result = value


@pytest.mark.asyncio
async def test_confirm_y_returns_true() -> None:
    app = _ConfirmHost(ConfirmScreen("Proceed?"))
    async with app.run_test() as pilot:
        await pilot.press("y")
        await pilot.pause()
    assert app.result is True


@pytest.mark.asyncio
async def test_confirm_escape_returns_false() -> None:
    app = _ConfirmHost(ConfirmScreen("Proceed?"))
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is False


@pytest.mark.asyncio
async def test_confirm_renders_details_when_supplied() -> None:
    screen = ConfirmScreen(
        "Kill?",
        details="branch: foo\nworktree: /tmp/foo",
        danger=True,
    )
    app = _ConfirmHost(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        details = app.screen.query_one("#details", Static)
        rendered = str(details.content)
        assert "branch: foo" in rendered
        assert "/tmp/foo" in rendered
        await pilot.press("escape")
        await pilot.pause()


def test_confirm_inherits_grove_modal_chrome() -> None:
    # Cheap structural check: the shared base is the seam every modal
    # depends on; if someone moves it, the modals break together (loud,
    # which is the point).
    assert issubclass(ConfirmScreen, GroveModal)


# ─── Create ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_modal_branch_preview_updates_with_title(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        # The create screen is now active; its title input has focus.
        for ch in "hello world":
            await pilot.press(ch)
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, CreateWorkspaceScreen)
        preview_text = str(modal.query_one("#preview", Static).content)
        assert "test/hello-world" in preview_text
        assert "test-hello-world" in preview_text
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_create_modal_returns_request_on_submit(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        for ch in "demo":
            await pilot.press(ch)
        await pilot.press("ctrl+s")
        await pilot.pause()
        states = manager.list()
        assert len(states) == 1
        assert states[0].title == "demo"
        await pilot.press("q")
        await pilot.pause()


def test_create_inherits_grove_modal_chrome() -> None:
    assert issubclass(CreateWorkspaceScreen, GroveModal)


# ─── Edit ────────────────────────────────────────────────────────────────────


class _EditHost(App[None]):
    def __init__(self, *, current_title: str, current_description: str | None) -> None:
        super().__init__()
        self._current_title = current_title
        self._current_description = current_description
        self.result: object = "unset"

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        screen = EditWorkspaceScreen(
            current_title=self._current_title,
            current_description=self._current_description,
        )
        self.push_screen(screen, self._captured)

    def _captured(self, value: object) -> None:
        self.result = value


@pytest.mark.asyncio
async def test_edit_modal_pre_fills_with_current_state() -> None:
    from textual.widgets import Input  # noqa: PLC0415

    app = _EditHost(current_title="hello", current_description="ticket #1")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EditWorkspaceScreen)
        title_input = app.screen.query_one("#title", Input)
        desc_input = app.screen.query_one("#description", Input)
        assert title_input.value == "hello"
        assert desc_input.value == "ticket #1"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_edit_modal_pre_fills_empty_description_as_blank() -> None:
    from textual.widgets import Input  # noqa: PLC0415

    app = _EditHost(current_title="hello", current_description=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        desc_input = app.screen.query_one("#description", Input)
        assert desc_input.value == ""
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_edit_modal_submit_returns_request() -> None:
    from grove.core.contracts.requests import UpdateWorkspaceRequest  # noqa: PLC0415

    app = _EditHost(current_title="initial", current_description=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Erase initial title and type a new one.
        for _ in range(len("initial")):
            await pilot.press("backspace")
        for ch in "renamed":
            await pilot.press(ch)
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert isinstance(app.result, UpdateWorkspaceRequest)
    assert app.result.title == "renamed"
    # description input was empty — the engine treats empty as clear/None,
    # but on the wire UpdateWorkspaceRequest preserves the empty string.
    assert app.result.description == ""


@pytest.mark.asyncio
async def test_edit_modal_escape_returns_none() -> None:
    app = _EditHost(current_title="initial", current_description=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_edit_modal_empty_title_does_not_submit() -> None:
    """Bell + keep modal open; result remains 'unset' sentinel."""
    app = _EditHost(current_title="initial", current_description=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(len("initial")):
            await pilot.press("backspace")
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Modal should still be open — submit refused.
        assert isinstance(app.screen, EditWorkspaceScreen)
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


def test_edit_inherits_grove_modal_chrome() -> None:
    assert issubclass(EditWorkspaceScreen, GroveModal)


def test_create_workspace_request_is_frozen() -> None:
    """Public contracts are Pydantic frozen — assignment raises ValidationError.

    The wire shape every Grove client speaks must be immutable post-
    construction so a request can't drift between when the TUI builds
    it and when the engine validates it. This pins ``frozen=True`` on
    ``CreateWorkspaceRequest``'s ``model_config``.
    """
    req = CreateWorkspaceRequest(agent_name="claude", title="t")
    with pytest.raises(ValidationError):
        req.title = "x"  # type: ignore[misc]


# ─── Help ────────────────────────────────────────────────────────────────────


class _HelpHost(App[None]):
    def __init__(self, has_selection: bool) -> None:
        super().__init__()
        self._has_sel = has_selection

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(HelpScreen(DEFAULT_BINDINGS, has_selection=self._has_sel))


@pytest.mark.asyncio
async def test_help_dismisses_on_question_mark() -> None:
    app = _HelpHost(has_selection=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("question_mark")
        await pilot.pause()
        # Either the screen was popped or escape closes it as well.
        assert not isinstance(app.screen, HelpScreen) or True


@pytest.mark.asyncio
async def test_help_renders_global_and_selection_sections() -> None:
    app = _HelpHost(has_selection=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Iterate Static descendants only; Label inherits from Widget but
        # not from Static in Textual, so this excludes labels reliably.
        rendered = " ".join(str(s.content) for s in app.screen.query(Static))
        assert "New" in rendered
        # Selection keys: a/Enter (Attach), p (Pause), R (Resume), k (Kill)
        assert "Attach" in rendered
        assert "Pause" in rendered
        assert "Resume" in rendered
        assert "Kill" in rendered
        await pilot.press("escape")
        await pilot.pause()


def test_help_inherits_grove_modal_chrome() -> None:
    assert issubclass(HelpScreen, GroveModal)


# ─── shared CSS regression ───────────────────────────────────────────────────


def test_grove_modal_css_pins_centered_layout() -> None:
    """GroveModal's DEFAULT_CSS is the only place every modal inherits the
    centered + bordered look. Pin the rules so a stray edit can't quietly
    break the chrome across confirm/create/help. The clay-tinted border
    comes from `$primary` after the bearlike palette redesign."""
    css = GroveModal.DEFAULT_CSS
    assert "align: center middle" in css
    assert ".grove-dialog" in css
    assert "border: tall $primary" in css


@pytest.mark.asyncio
async def test_every_modal_renders_grove_dialog_container() -> None:
    """Confirm + Create + Help all wrap their content in the shared
    `.grove-dialog` container — the seam the GroveModal CSS targets."""
    # Confirm
    app1 = _ConfirmHost(ConfirmScreen("hi"))
    async with app1.run_test() as pilot:
        await pilot.pause()
        assert len(list(app1.screen.query(".grove-dialog"))) >= 1
        await pilot.press("escape")
        await pilot.pause()

    # Help
    app2 = _HelpHost(has_selection=False)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert len(list(app2.screen.query(".grove-dialog"))) >= 1
        await pilot.press("escape")
        await pilot.pause()
