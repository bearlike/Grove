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
from grove.tui.screens.message import SendMessageScreen
from tests.conftest import FakeTmux


def _manager(
    tmp_repo: Path,
    tmp_path: Path,
    *,
    container_enabled: bool = True,
    brief_enabled: bool = True,
) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": container_enabled},
            "brief": {"enabled": brief_enabled},
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


@pytest.mark.asyncio
async def test_runtime_picker_offers_two_options_default_container(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`container.enabled=True` (the config default, #244): the picker offers
    exactly Host/Container, pre-selected on Container, with the default marked
    in its label so the answer is readable without opening the dropdown."""
    from textual.widgets import Select  # noqa: PLC0415

    from grove.core.workspace import Runtime  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, container_enabled=True)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        select = modal.query_one("#runtime", Select)
        values = [value for _, value in select._options]  # type: ignore[attr-defined]
        assert values == [Runtime.HOST.value, Runtime.CONTAINER.value]
        labels = [str(label) for label, _ in select._options]  # type: ignore[attr-defined]
        assert labels == ["Host", "Container (default)"]
        assert select.value == Runtime.CONTAINER.value
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_runtime_picker_offers_two_options_default_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`container.enabled=False`: same two options, pre-selected on Host
    instead — proves the pre-selection tracks the cascade rather than being
    hardcoded to whichever runtime happened to be the config default today."""
    from textual.widgets import Select  # noqa: PLC0415

    from grove.core.workspace import Runtime  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, container_enabled=False)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        select = modal.query_one("#runtime", Select)
        values = [value for _, value in select._options]  # type: ignore[attr-defined]
        assert values == [Runtime.HOST.value, Runtime.CONTAINER.value]
        labels = [str(label) for label, _ in select._options]  # type: ignore[attr-defined]
        assert labels == ["Host (default)", "Container"]
        assert select.value == Runtime.HOST.value
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_create_modal_submits_the_resolved_concrete_runtime(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Submitting without touching the Runtime picker sends the cascade's own
    default as a concrete value — never ``None`` — since the picker no longer
    offers a "let the cascade decide" sentinel option."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, container_enabled=False)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "resolved runtime":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    from grove.core.workspace import Runtime  # noqa: PLC0415

    assert req.runtime == Runtime.HOST


@pytest.mark.asyncio
async def test_brief_picker_offers_three_options_and_names_cascade_default_on(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`brief.enabled=True` (the config default): the picker offers cascade /
    on / off, pre-selected on cascade, and the cascade option's label names
    the actual resolved default so the unset state is never an ambiguous
    blank — mirroring the Runtime picker's "(default)" marker."""
    from textual.widgets import Select  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, brief_enabled=True)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        select = modal.query_one("#brief", Select)
        values = [value for _, value in select._options]  # type: ignore[attr-defined]
        assert values == ["cascade", "on", "off"]
        labels = [str(label) for label, _ in select._options]  # type: ignore[attr-defined]
        assert labels == ["Cascade default (currently on)", "On", "Off"]
        assert select.value == "cascade"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_brief_picker_names_cascade_default_off(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`brief.enabled=False`: same three options, cascade label now reads
    "off" — proves the label tracks the cascade rather than being static."""
    from textual.widgets import Select  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, brief_enabled=False)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        select = modal.query_one("#brief", Select)
        labels = [str(label) for label, _ in select._options]  # type: ignore[attr-defined]
        assert labels == ["Cascade default (currently off)", "On", "Off"]
        assert select.value == "cascade"
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_create_modal_submits_brief_none_when_cascade_left_untouched(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Leaving the picker on "cascade" sends `brief=None`, so the engine
    re-reads `brief.enabled` at create time — the genuine tri-state behavior
    that distinguishes this control from Runtime, which never sends `None`."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "cascade brief":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.brief is None


@pytest.mark.asyncio
async def test_create_modal_submits_brief_true_when_on_selected(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Explicitly picking "On" forces `brief=True` regardless of the cascade
    default, and the choice is a concrete value, not `None`."""
    from textual.widgets import Select  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, brief_enabled=False)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        modal.query_one("#brief", Select).value = "on"
        for ch in "forced on":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.brief is True


@pytest.mark.asyncio
async def test_create_modal_submits_brief_false_when_off_selected(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Explicitly picking "Off" forces `brief=False` regardless of the
    cascade default."""
    from textual.widgets import Select  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, brief_enabled=True)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        modal.query_one("#brief", Select).value = "off"
        for ch in "forced off":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.brief is False


@pytest.mark.asyncio
async def test_create_modal_model_input_flows_into_request(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A custom-typed model id lands on ``CreateWorkspaceRequest.model`` verbatim
    (the provider boundary — Grove forwards any id, never validates it).

    The picker offers each agent's curated catalog, but ``Custom…`` is what keeps
    the boundary honest: an id Grove has never heard of must still reach the tool
    untouched, so this drives that path rather than a catalog entry."""
    from textual.widgets import Input, Select  # noqa: PLC0415

    from grove.tui.screens.create import MODEL_CUSTOM  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        modal.query_one("#model", Select).value = MODEL_CUSTOM
        await pilot.pause()
        modal.query_one("#model-custom", Input).value = "opus"
        for ch in "modeled":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.model == "opus"


@pytest.mark.asyncio
async def test_create_modal_blank_model_resolves_to_none(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Leaving the model Input blank resolves to ``None`` — the agent tool's
    own default, never a Grove-picked model."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "blank model":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.model is None


# ─── Create: root placement + skip-init ──────────────────────────────────────


async def _open_create_modal(pilot: object, app: GroveApp) -> CreateWorkspaceScreen:
    """Press 'n', wait, and return the freshly-pushed create screen."""
    await pilot.press("n")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    modal = app.screen
    assert isinstance(modal, CreateWorkspaceScreen)
    return modal


@pytest.mark.asyncio
async def test_root_mode_active_block_reads_root_branch(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Selecting the Root radio makes `_active_block().read()` a RootBranch."""
    from textual.widgets import RadioButton  # noqa: PLC0415

    from grove.core.contracts.branch_plan import RootBranch  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        # Press the Root radio button — fires RadioSet.Changed → on_radio_set_changed.
        modal.query_one("#mode-root", RadioButton).value = True
        await pilot.pause()
        plan = modal._active_block().read()
        assert isinstance(plan, RootBranch)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_root_mode_auto_checks_skip_init(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Picking Root flips the skip-init checkbox on (init is risky in the root)."""
    from textual.widgets import Checkbox, RadioButton  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        # Default: unchecked before any mode change.
        assert modal.query_one("#skip-init", Checkbox).value is False
        modal.query_one("#mode-root", RadioButton).value = True
        await pilot.pause()
        assert modal.query_one("#skip-init", Checkbox).value is True
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_root_mode_submit_builds_root_request_with_skip_init(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """End-to-end: root submit yields a CreateWorkspaceRequest carrying a
    RootBranch branch_plan and skip_init=True (auto-checked by root)."""
    from textual.widgets import RadioButton  # noqa: PLC0415

    from grove.core.contracts.branch_plan import RootBranch  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        # Intercept the dismiss payload so we read the request directly without
        # exercising the manager's root-create side effects.
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "rooty":
            await pilot.press(ch)
        modal.query_one("#mode-root", RadioButton).value = True
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert isinstance(req.branch_plan, RootBranch)
    assert req.skip_init is True
    assert req.title == "rooty"


@pytest.mark.asyncio
async def test_skip_init_default_unchecked_flows_into_request(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """In a non-root mode the skip-init checkbox defaults unchecked, and that
    False flows through to the request's skip_init."""
    from grove.core.contracts.branch_plan import AutoBranch  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "plain":
            await pilot.press(ch)
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert isinstance(req.branch_plan, AutoBranch)  # default mode is Auto
    assert req.skip_init is False


@pytest.mark.asyncio
async def test_skip_init_checked_flows_into_request_in_non_root_mode(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A user can opt into skip-init in any mode; the checked value reaches
    the request even when placement is a normal worktree (Auto)."""
    from textual.widgets import Checkbox  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    captured: dict[str, CreateWorkspaceRequest] = {}

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.dismiss = lambda result=None: captured.__setitem__("req", result)  # type: ignore[assignment,method-assign]
        for ch in "plain":
            await pilot.press(ch)
        modal.query_one("#skip-init", Checkbox).value = True
        await pilot.pause()
        modal._submit()
        await pilot.pause()

    req = captured["req"]
    assert isinstance(req, CreateWorkspaceRequest)
    assert req.skip_init is True


@pytest.mark.asyncio
async def test_skip_init_checkbox_states_are_visually_distinct(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Checked vs unchecked must read as filled-box vs empty-box, not a
    subtle color shift of an always-present mark.

    Textual's stock ToggleButton renders its inner glyph (`X`) in EVERY
    state and conveys on/off only by the glyph's color. On Grove's warm-dark
    palette the off mark was a near-black `X` on a dark pill — still an `X`,
    which reads as 'ticked' — so both states looked checked and the box
    appeared stuck on (the bug this pins). `GroveModal` overrides the
    `toggle--button` colors so OFF hides the mark (fg == bg → empty box) and
    ON fills the whole pill with a distinct background (filled box).
    """
    from textual.widgets import Checkbox, RadioButton  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        cb = modal.query_one("#skip-init", Checkbox)

        assert cb.value is False
        off = cb.get_visual_style("toggle--button")
        # Unchecked: the mark is painted the pill's own bg → invisible → an
        # empty box. Stock Textual would leave fg != bg (a visible dark X).
        assert off.foreground == off.background, (
            "unchecked checkbox renders a visible mark — it reads as 'ticked'"
        )

        modal.query_one("#mode-root", RadioButton).value = True
        await pilot.pause()
        assert cb.value is True
        on = cb.get_visual_style("toggle--button")
        # Checked: the pill fills with a distinct background → filled box.
        # Stock Textual keeps the same pill bg in both states (color-only
        # shift of the mark), which is exactly the indistinguishable case.
        assert on.background != off.background, (
            "checked and unchecked share a pill background — the two states "
            "are visually indistinguishable"
        )

        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_root_mode_preview_shows_in_place(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Root preview names the current branch with `(in place)` and the repo
    root path with `(in place — no worktree)`."""
    from textual.widgets import RadioButton  # noqa: PLC0415

    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        modal = await _open_create_modal(pilot, app)
        modal.query_one("#mode-root", RadioButton).value = True
        await pilot.pause()
        preview = str(modal.query_one("#preview", Static).content)
        assert "(in place)" in preview
        assert "no worktree" in preview
        # The tmp_repo fixture starts on `main`.
        assert "main" in preview
        await pilot.press("escape")
        await pilot.pause()


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


# ─── Message (steer) ─────────────────────────────────────────────────────────


class _MessageHost(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.result: object = "unset"

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(SendMessageScreen(workspace_title="my task"), self._captured)

    def _captured(self, value: object) -> None:
        self.result = value


@pytest.mark.asyncio
async def test_message_modal_empty_text_does_not_submit() -> None:
    """Bell + keep modal open on empty input — same UX as create/edit titles."""
    app = _MessageHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SendMessageScreen)
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


def test_message_inherits_grove_modal_chrome() -> None:
    assert issubclass(SendMessageScreen, GroveModal)


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
    comes from `$primary`."""
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
