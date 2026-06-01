"""ContextualFooter rendering and content contract.

Pins what the user actually sees at the bottom of the screen:
visible height (regression guard for the height/border conflict
that hid the footer on every screen) and rendered key+label
content per screen. If a future CSS or compose change makes the
footer invisible or shows the wrong keys, one of these tests will
fail loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp
from grove.tui.keys import DEFAULT_BINDINGS
from grove.tui.screens.confirm import ConfirmScreen
from grove.tui.screens.help import HelpScreen
from grove.tui.widgets.footer import ContextualFooter
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


# ─── list screen ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_screen_footer_content_area_has_height_one(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Regression guard for the CSS bug: `height: 1` + `border-top` together
    pushed the border into the docked allocation, leaving the content area
    at height 0 (rendered text invisible). The widget's outer was 1 row
    but the content_size collapsed. After the fix, content_size.height
    must equal 1 so the rendered text actually shows."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        assert footer.content_size.height == 1, (
            f"footer content_size.height was {footer.content_size.height}; "
            "expected 1 — border-top is consuming the docked row"
        )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_renders_global_keys(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The five always-available keys appear with their labels."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        for key, label in [
            ("q", "Quit"),
            ("n", "New"),
            ("r", "Refresh"),
            ("/", "Filter"),
            ("?", "Help"),
        ]:
            assert key in rendered and label in rendered, (
                f"expected '{key}' and '{label}' in footer; got: {rendered!r}"
            )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_dims_selection_keys_when_empty(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """With no workspaces (no selection possible) the selection-only keys
    appear inside [dim] markup so the user sees they're inert."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        for key_label in ("p Pause", "R Resume", "k Kill"):
            assert f"[dim]{key_label}[/]" in rendered, (
                f"expected '[dim]{key_label}[/]' in footer; got: {rendered!r}"
            )
        # Attach uses the slash-display form ("enter/a Attach")
        assert "[dim]enter/a Attach[/]" in rendered, rendered
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_brightens_keys_applicable_to_active_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A live workspace is ACTIVE/IDLE → pause, attach and kill are
    applicable (rendered bold-clay). Resume and respawn don't apply
    (rendered dim) so the footer reads as 'what can I do right now'."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        for key, label in (("p", "Pause"), ("k", "Kill")):
            assert f"[bold #d97757]{key}[/] {label}" in rendered, (
                f"expected '[bold #d97757]{key}[/] {label}' in footer; got: {rendered!r}"
            )
        assert "[bold #d97757]enter/a[/] Attach" in rendered, rendered
        # Inapplicable: resume only after pause, respawn only when offline.
        for inert in ("R Resume", "o Respawn"):
            assert f"[dim]{inert}[/]" in rendered, (
                f"expected '[dim]{inert}[/]' in footer; got: {rendered!r}"
            )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_brightens_resume_when_workspace_paused(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """For a PAUSED workspace, resume + kill brighten and pause/attach dim."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    manager.pause(state.id)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "[bold #d97757]R[/] Resume" in rendered, rendered
        assert "[bold #d97757]k[/] Kill" in rendered, rendered
        for inert in ("p Pause", "enter/a Attach", "o Respawn"):
            assert f"[dim]{inert}[/]" in rendered, (
                f"expected '[dim]{inert}[/]' in footer; got: {rendered!r}"
            )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_brightens_respawn_when_workspace_offline(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """For an OFFLINE workspace, respawn + kill brighten."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    fake_tmux.sessions.discard(state.tmux_session)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "[bold #d97757]o[/] Respawn" in rendered, rendered
        assert "[bold #d97757]k[/] Kill" in rendered, rendered
        for inert in ("p Pause", "R Resume", "enter/a Attach"):
            assert f"[dim]{inert}[/]" in rendered, (
                f"expected '[dim]{inert}[/]' in footer; got: {rendered!r}"
            )
        await pilot.press("q")
        await pilot.pause()


# ─── Confirm modal ──────────────────────────────────────────────────────────


class _ConfirmHost(App[None]):
    def __init__(self, screen: ConfirmScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(self._screen)


@pytest.mark.asyncio
async def test_confirm_modal_footer_renders() -> None:
    """Confirm modal shows its own y/no hints at the bottom."""
    app = _ConfirmHost(ConfirmScreen("Proceed?"))
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "y" in rendered and "Yes" in rendered, rendered
        assert "n/escape" in rendered and "No" in rendered, rendered
        assert footer.content_size.height == 1
        await pilot.press("escape")
        await pilot.pause()


# ─── Create modal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_modal_footer_renders(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Create modal shows its own escape/ctrl+s hints at the bottom."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "escape" in rendered and "Cancel" in rendered, rendered
        assert "ctrl+s" in rendered and "Create" in rendered, rendered
        assert footer.content_size.height == 1
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


# ─── Help modal ─────────────────────────────────────────────────────────────


class _HelpHost(App[None]):
    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(HelpScreen(DEFAULT_BINDINGS, has_selection=False))


@pytest.mark.asyncio
async def test_help_modal_footer_renders() -> None:
    """Help modal shows its own dismiss hint at the bottom."""
    app = _HelpHost()
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "escape/q/?" in rendered and "Close" in rendered, rendered
        assert footer.content_size.height == 1
        await pilot.press("escape")
        await pilot.pause()


# ─── visual contract ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_footer_keys_render_in_clay_accent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Available keys render in the brand clay (`$primary` = #d97757),
    bold, with the label in default text. Pins both weight and color so
    a future theme split can't silently drop the brand from the footer.

    Active workspace → pause/attach/kill applicable (clay), resume/respawn
    inapplicable (dim). Globals always clay.
    """
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        for key in ("q", "n", "r", "/", "?"):
            assert f"[bold #d97757]{key}[/]" in rendered, (
                f"expected '[bold #d97757]{key}[/]' in footer; got: {rendered!r}"
            )
        assert "[bold #d97757]enter/a[/] Attach" in rendered, rendered
        # Pause + Kill apply to a live workspace; clay accent.
        for key, label in (("p", "Pause"), ("k", "Kill")):
            assert f"[bold #d97757]{key}[/] {label}" in rendered, rendered
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_footer_separator_renders_in_muted(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Hints are joined by ` · ` in $secondary (#96938c) — the dot recedes
    so the eye lands on keys + labels."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert " [#96938c]·[/] " in rendered, (
            f"expected muted middle-dot separator; got: {rendered!r}"
        )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_screen_footer_groups_globals_and_selection_with_bar(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """List screen renders two groups separated by a muted vertical bar.

    Globals (q/n/r/?/) on the left, selection-keys (enter/a, p, R, o, k) on
    the right, joined by ` │ ` in $secondary muted hex. Same idiom claude-squad
    uses to separate logical key groups."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "  [#96938c]│[/]  " in rendered, (
            f"expected muted vertical-bar group separator; got: {rendered!r}"
        )
        # The bar must appear *between* a global key and a selection key —
        # specifically between '? Help' and 'enter/a Attach'.
        bar_idx = rendered.find("│")
        help_idx = rendered.find("? Help")
        # `?` is the last global; attach is the first selection key.
        # When no row is selected attach is dim, so check both renderings.
        attach_idx = max(rendered.find("enter/a Attach"), rendered.find("[dim]enter/a"))
        assert help_idx < bar_idx < attach_idx, (
            f"bar should sit between globals and selection group; got: {rendered!r}"
        )
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_modal_footer_stays_flat_no_bar(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Modal screens use ``set_keys`` (single group) — no `│` divider."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")  # open create modal
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        assert "│" not in rendered, (
            f"modal footers are single-group; expected no bar separator; got: {rendered!r}"
        )
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_footer_dim_state_does_not_apply_clay(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Selection-only keys when nothing is selected: render must dim the
    whole pair, NOT color the key clay. 'Inert' reads as uniformly muted."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        footer = app.screen.query_one(ContextualFooter)
        rendered = str(footer.render())
        for key_label in ("p Pause", "R Resume", "k Kill", "enter/a Attach"):
            assert f"[dim]{key_label}[/]" in rendered, (
                f"expected '[dim]{key_label}[/]' in footer; got: {rendered!r}"
            )
            first_token = key_label.split()[0]
            assert f"[bold #d97757]{first_token}" not in rendered, (
                f"unavailable key '{first_token}' should not render in clay; got: {rendered!r}"
            )
        await pilot.press("q")
        await pilot.pause()
