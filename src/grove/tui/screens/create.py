"""CreateWorkspaceScreen — agent + title + variant-aware branch picker.

Returns a Pydantic ``CreateWorkspaceRequest`` — the same wire shape every
Grove client (TUI today, web/API tomorrow) submits to
``WorkspaceManager.create()``. The screen is one client of that
contract; nothing here is TUI-specific to the engine.

Four atomic ``_BranchBlock`` subclasses own the variant-specific input
widgets and a ``read()`` that produces the matching ``BranchPlan``.
A ``RadioSet`` at the top picks which block is visible; the others are
``display: none``-d, which preserves their values when the user toggles
back. The live preview at the bottom mirrors the resolved branch +
worktree path + session name the manager will actually use, so a typo
is visible before submit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Select, Static

from grove.core import AgentSpec, GroveConfig
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.branch_plan import (
    AutoBranch,
    BranchPlan,
    ExistingLocalBranch,
    NewNamedBranch,
    TrackRemoteBranch,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.workspace import slug
from grove.tui._status import ref_color
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey

_MODE_AUTO = "auto"
_MODE_NEW = "new"
_MODE_EXISTING = "existing"
_MODE_REMOTE = "remote"

_MODES = (_MODE_AUTO, _MODE_NEW, _MODE_EXISTING, _MODE_REMOTE)


# ─── variant blocks (atomic widgets, each owns its inputs + read()) ─────────


class _BranchBlock(Vertical):
    """Base for the four variant input blocks.

    Subclasses each own:
      * a ``compose()`` that yields just their variant's widgets,
      * a ``read()`` that builds the matching ``BranchPlan`` from current
        widget state (raises on incomplete input),
      * an optional ``seed_title()`` that suggests a workspace title from
        the picked branch — the screen uses it to pre-fill the title
        field when the user is in Existing/Remote mode and the title
        field is still empty.

    Visibility toggling is the parent screen's job (``-hidden`` class),
    not the block's. The class form is the code-as-poem principle in
    miniature: each variant owns its slice of state and the methods that
    operate on it; no free helpers in the screen module reach inside.
    """

    DEFAULT_CSS = """
    _BranchBlock {
        height: auto;
        padding: 0;
    }
    _BranchBlock.-hidden {
        display: none;
    }
    """

    def read(self) -> BranchPlan:
        """Build the BranchPlan variant from this block's widget values.

        Raises ``ValueError`` (or Pydantic ``ValidationError``) if the
        user hasn't filled in enough; the screen's ``_submit()`` catches
        and bells without dismissing.
        """
        raise NotImplementedError

    def seed_title(self) -> str | None:
        """Suggested workspace title derived from the picked branch.

        Returns ``None`` when this variant has nothing useful to seed
        (Auto / NewNamed both leave the title to the user)."""
        return None


class _AutoBlock(_BranchBlock):
    """``base_ref`` only — Grove generates the branch name from title + ts."""

    def __init__(self, base_options: list[tuple[str, str]], default_base: str) -> None:
        super().__init__()
        self._base_options = base_options
        self._default_base = default_base

    def compose(self) -> ComposeResult:
        yield Label("Base ref:")
        yield Select(
            self._base_options,
            value=self._default_base,
            id="auto-base",
            allow_blank=False,
        )

    def read(self) -> AutoBranch:
        value = self.query_one("#auto-base", Select).value
        return AutoBranch(base_ref=str(value) if value is not None else "HEAD")


class _NewNamedBlock(_BranchBlock):
    """User-supplied name + base ref."""

    def __init__(self, base_options: list[tuple[str, str]], default_base: str) -> None:
        super().__init__()
        self._base_options = base_options
        self._default_base = default_base

    def compose(self) -> ComposeResult:
        yield Label("Branch name:")
        yield Input(placeholder="feature/payment-v2", id="new-name")
        yield Label("Base ref:")
        yield Select(
            self._base_options,
            value=self._default_base,
            id="new-base",
            allow_blank=False,
        )

    def read(self) -> NewNamedBranch:
        name = self.query_one("#new-name", Input).value.strip()
        base_value = self.query_one("#new-base", Select).value
        # Pydantic validates the name pattern; raise from there if invalid.
        return NewNamedBranch(
            name=name,
            base_ref=str(base_value) if base_value is not None else "HEAD",
        )


class _ExistingLocalBlock(_BranchBlock):
    """Pick an existing local branch — no new branch is created."""

    def __init__(self, local_options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._local_options = local_options

    def compose(self) -> ComposeResult:
        yield Label("Local branch:")
        if self._local_options:
            yield Select(self._local_options, id="existing-name", allow_blank=False)
        else:
            yield Static(
                "(no local branches available)",
                id="existing-empty",
            )

    def read(self) -> ExistingLocalBranch:
        try:
            sel = self.query_one("#existing-name", Select)
        except Exception as exc:
            raise ValueError("no local branches to pick") from exc
        value = sel.value
        if value is None:
            raise ValueError("pick a local branch")
        return ExistingLocalBranch(name=str(value))

    def seed_title(self) -> str | None:
        try:
            sel = self.query_one("#existing-name", Select)
        except Exception:
            return None
        v = sel.value
        if v is None:
            return None
        return str(v).rsplit("/", 1)[-1]


class _TrackRemoteBlock(_BranchBlock):
    """Pick a remote branch; Grove creates a fresh local tracking branch."""

    def __init__(self, remote_options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._remote_options = remote_options

    def compose(self) -> ComposeResult:
        yield Label("Remote branch:")
        if self._remote_options:
            yield Select(self._remote_options, id="remote-ref", allow_blank=False)
        else:
            yield Static(
                "(no remote branches found — `git fetch` first?)",
                id="remote-empty",
            )
        yield Label("Local name (optional):")
        yield Input(placeholder="<derived from remote>", id="remote-local")

    def read(self) -> TrackRemoteBranch:
        try:
            sel = self.query_one("#remote-ref", Select)
        except Exception as exc:
            raise ValueError("no remote branches to pick") from exc
        ref = sel.value
        if ref is None:
            raise ValueError("pick a remote branch")
        local = self.query_one("#remote-local", Input).value.strip() or None
        return TrackRemoteBranch(remote_ref=str(ref), local_name=local)

    def seed_title(self) -> str | None:
        try:
            sel = self.query_one("#remote-ref", Select)
        except Exception:
            return None
        v = sel.value
        if v is None:
            return None
        s = str(v)
        # origin/feature/x → feature/x → x
        without_remote = s.split("/", 1)[1] if "/" in s else s
        return without_remote.rsplit("/", 1)[-1]


# ─── screen ─────────────────────────────────────────────────────────────────


class CreateWorkspaceScreen(GroveModal[CreateWorkspaceRequest | None]):
    """Pick agent + title + branch plan. Returns ``CreateWorkspaceRequest``."""

    DEFAULT_CSS = """
    CreateWorkspaceScreen .grove-dialog {
        width: 90;
    }
    CreateWorkspaceScreen .field-label {
        margin-top: 1;
    }
    CreateWorkspaceScreen #branch-blocks {
        margin-top: 1;
        margin-bottom: 1;
        height: auto;
    }
    CreateWorkspaceScreen #preview {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Create"),
    ]

    _PREVIEW_TITLE_PLACEHOLDER: ClassVar[str] = "<title>"
    _PREVIEW_BRANCH_PLACEHOLDER: ClassVar[str] = "<branch>"
    _PREVIEW_REMOTE_PLACEHOLDER: ClassVar[str] = "<remote>"

    def __init__(
        self,
        agents: Sequence[AgentSpec],
        *,
        cfg: GroveConfig,
        local_branches: Sequence[BranchInfo] | None = None,
        remote_branches: Sequence[BranchInfo] | None = None,
        default_base: str = "HEAD",
    ) -> None:
        super().__init__()
        if not agents:
            raise ValueError("at least one agent must be configured")
        self._agents = list(agents)
        self._cfg = cfg
        self._local_branches: list[BranchInfo] = list(local_branches or ())
        self._remote_branches: list[BranchInfo] = list(remote_branches or ())
        self._mode: str = _MODE_AUTO
        # Whether the user has typed (or seeded) something into the title
        # field. Drives the "seed title from picked branch" behavior — only
        # seed when the field is still empty, never overwrite typed text.
        self._title_user_edited: bool = False

        base_opts = self._build_base_options()
        local_opts = self._build_local_options()
        remote_opts = self._build_remote_options()
        default_base_value = (
            default_base if any(v == default_base for _, v in base_opts) else "HEAD"
        )

        self._auto_block = _AutoBlock(base_opts, default_base_value)
        self._new_block = _NewNamedBlock(base_opts, default_base_value)
        self._existing_block = _ExistingLocalBlock(local_opts)
        self._remote_block = _TrackRemoteBlock(remote_opts)

    def _build_base_options(self) -> list[tuple[str, str]]:
        """Base-ref selector options: HEAD plus every local branch."""
        opts: list[tuple[str, str]] = [("HEAD", "HEAD")]
        seen = {"HEAD"}
        for b in self._local_branches:
            if b.name in seen:
                continue
            seen.add(b.name)
            label = b.name + (" (current)" if b.is_current else "")
            opts.append((label, b.name))
        return opts

    def _build_local_options(self) -> list[tuple[str, str]]:
        """Existing-local options: every local branch with checkout markers."""
        opts: list[tuple[str, str]] = []
        for b in self._local_branches:
            label = b.name
            if b.is_current:
                label += " (current)"
            if b.checked_out_in is not None:
                label += f" (in {b.checked_out_in.name})"
            opts.append((label, b.name))
        return opts

    def _build_remote_options(self) -> list[tuple[str, str]]:
        return [(b.name, b.name) for b in self._remote_branches]

    def compose(self) -> ComposeResult:
        agent_options = [(a.name, a.name) for a in self._agents]
        with Vertical(classes="grove-dialog"):
            yield Label("New workspace", classes="grove-dialog-title")
            yield Label("Agent:", classes="field-label")
            yield Select(
                agent_options,
                value=agent_options[0][1],
                id="agent",
                allow_blank=False,
            )
            yield Label("Title:", classes="field-label")
            yield Input(placeholder="my-task", id="title")
            yield Label("Branch:", classes="field-label")
            with RadioSet(id="branch-mode"):
                yield RadioButton(
                    "Auto — Grove generates from title + timestamp",
                    value=True,
                    id="mode-auto",
                )
                yield RadioButton("New — pick a name and base branch", id="mode-new")
                yield RadioButton(
                    "Existing — check out a local branch",
                    id="mode-existing",
                )
                yield RadioButton("Remote — track a remote branch", id="mode-remote")
            with Vertical(id="branch-blocks"):
                yield self._auto_block
                self._new_block.add_class("-hidden")
                yield self._new_block
                self._existing_block.add_class("-hidden")
                yield self._existing_block
                self._remote_block.add_class("-hidden")
                yield self._remote_block
            yield Static(self._render_preview(""), id="preview")
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Cancel", id="cancel", variant="default")
                yield Button("Create (Ctrl-S)", id="submit", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("escape", "Cancel"),
                FooterKey("ctrl+s", "Create"),
            ]
        )
        self.query_one("#title", Input).focus()

    # ─── live preview / mode toggling ──────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "title":
            # Empty value never marks "user-edited" — that lets seeding
            # still work after the user clears the field.
            self._title_user_edited = bool(event.value.strip())
        self._refresh_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        del event
        if not self._title_user_edited:
            self._maybe_seed_title()
        self._refresh_preview()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        idx = event.radio_set.pressed_index
        if idx is None or idx < 0 or idx >= len(_MODES):
            return
        self._mode = _MODES[idx]
        self._sync_block_visibility()
        if not self._title_user_edited:
            self._maybe_seed_title()
        self._refresh_preview()

    def _sync_block_visibility(self) -> None:
        blocks: dict[str, _BranchBlock] = {
            _MODE_AUTO: self._auto_block,
            _MODE_NEW: self._new_block,
            _MODE_EXISTING: self._existing_block,
            _MODE_REMOTE: self._remote_block,
        }
        for mode, block in blocks.items():
            if mode == self._mode:
                block.remove_class("-hidden")
            else:
                block.add_class("-hidden")

    def _active_block(self) -> _BranchBlock:
        return {
            _MODE_AUTO: self._auto_block,
            _MODE_NEW: self._new_block,
            _MODE_EXISTING: self._existing_block,
            _MODE_REMOTE: self._remote_block,
        }[self._mode]

    def _maybe_seed_title(self) -> None:
        seed = self._active_block().seed_title()
        if seed:
            self.query_one("#title", Input).value = seed

    def _refresh_preview(self) -> None:
        try:
            title_input = self.query_one("#title", Input)
            preview = self.query_one("#preview", Static)
        except Exception:
            return
        preview.update(self._render_preview(title_input.value))

    def _render_preview(self, title: str) -> str:
        slug_text = slug(title) if title.strip() else self._PREVIEW_TITLE_PLACEHOLDER
        cfg = self._cfg
        ref = ref_color("branch", dark=self.app.current_theme.dark)
        branch_line = self._render_branch_line(slug_text, ref)
        return (
            f"branch:   {branch_line}\n"
            f"worktree: {cfg.worktree.root_template}/{slug_text}-<ts>\n"
            f"session:  [{ref}]{cfg.tmux.session_prefix}{slug_text}[/]-<ts>"
        )

    def _render_branch_line(self, slug_text: str, ref_hex: str) -> str:
        cfg = self._cfg
        if self._mode == _MODE_AUTO:
            base = self._read_select_value("#auto-base", self._auto_block) or "HEAD"
            return f"[{ref_hex}]{cfg.worktree.branch_prefix}{slug_text}[/]-<ts>  (new → {base})"
        if self._mode == _MODE_NEW:
            try:
                name = (
                    self._new_block.query_one("#new-name", Input).value.strip()
                    or self._PREVIEW_BRANCH_PLACEHOLDER
                )
            except Exception:
                name = self._PREVIEW_BRANCH_PLACEHOLDER
            base = self._read_select_value("#new-base", self._new_block) or "HEAD"
            return f"[{ref_hex}]{name}[/]  (new → {base})"
        if self._mode == _MODE_EXISTING:
            name = (
                self._read_select_value("#existing-name", self._existing_block)
                or self._PREVIEW_BRANCH_PLACEHOLDER
            )
            return f"[{ref_hex}]{name}[/]  (checkout)"
        if self._mode == _MODE_REMOTE:
            remote = (
                self._read_select_value("#remote-ref", self._remote_block)
                or self._PREVIEW_REMOTE_PLACEHOLDER
            )
            try:
                local_typed = self._remote_block.query_one("#remote-local", Input).value.strip()
            except Exception:
                local_typed = ""
            local = local_typed or (remote.split("/", 1)[1] if "/" in remote else remote)
            return f"[{ref_hex}]{local}[/]  (new → tracks {remote})"
        return self._PREVIEW_BRANCH_PLACEHOLDER

    @staticmethod
    def _read_select_value(selector: str, root: Vertical) -> str | None:
        try:
            value = root.query_one(selector, Select).value
        except Exception:
            return None
        if value is None:
            return None
        return str(value)

    # ─── actions ───────────────────────────────────────────────────────────

    def action_submit(self) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        # Pressing Enter in any input submits the form.
        self._submit()

    def _submit(self) -> None:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.app.bell()
            self.query_one("#title", Input).focus()
            return
        try:
            plan = self._active_block().read()
        except Exception:
            self.app.bell()
            return
        try:
            request = CreateWorkspaceRequest(
                agent_name=str(self.query_one("#agent", Select).value),
                title=title,
                branch_plan=plan,
            )
        except Exception:
            self.app.bell()
            return
        self.dismiss(request)
