"""SaveDefaultsScreen — choose the config layer for create-form defaults."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, RadioButton, RadioSet, Static

from grove.core import DefaultsScope
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class SaveDefaultsScreen(GroveModal[DefaultsScope | None]):
    """Ask which config layer a "save as defaults" write lands in."""

    DEFAULT_CSS = """
    SaveDefaultsScreen .grove-dialog {
        width: 90;
    }
    SaveDefaultsScreen #shadowed-warning {
        color: $text-muted;
        margin-top: 1;
    }
    SaveDefaultsScreen #shadowed-warning.-hidden {
        display: none;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    _SCOPES: ClassVar[tuple[DefaultsScope, ...]] = (
        DefaultsScope.USER,
        DefaultsScope.PROJECT,
        DefaultsScope.PROJECT_LOCAL,
    )

    def __init__(
        self,
        *,
        repo_root: Path | None,
        shadowed: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._repo_root = repo_root
        self._shadowed = tuple(shadowed)

    def compose(self) -> ComposeResult:
        user_path = DefaultsScope.USER.path(None)
        project_path = DefaultsScope.PROJECT.path(self._repo_root) if self._repo_root else None
        local_path = DefaultsScope.PROJECT_LOCAL.path(self._repo_root) if self._repo_root else None
        with Vertical(classes="grove-dialog"):
            yield Label("Save workspace defaults", classes="grove-dialog-title")
            yield Static("Choose where future create forms should start from.")
            with RadioSet(id="defaults-scope"):
                yield RadioButton(
                    f"User — every project on this machine ({user_path})",
                    value=self._repo_root is None,
                    id="scope-user",
                )
                yield RadioButton(
                    "Project — shared with the team, committed "
                    f"({project_path or '.grove/config.json'})",
                    value=self._repo_root is not None,
                    id="scope-project",
                    disabled=self._repo_root is None,
                )
                yield RadioButton(
                    "Project (local) — this machine only, gitignored "
                    f"({local_path or '.grove/config.local.json'})",
                    id="scope-project-local",
                    disabled=self._repo_root is None,
                )
            yield Static(
                self._warning_text(),
                id="shadowed-warning",
                classes="-hidden",
                markup=False,
            )
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Cancel", id="cancel", variant="default")
                yield Button("Save (Ctrl-S)", id="save", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("escape", "Cancel"),
                FooterKey("ctrl+s", "Save"),
            ]
        )
        self._show_shadowed_warning(self.query_one("#defaults-scope", RadioSet).pressed_index)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._show_shadowed_warning(event.radio_set.pressed_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        scope = self._selected_scope()
        if scope is not None:
            self.dismiss(scope)

    def _selected_scope(self) -> DefaultsScope | None:
        index = self.query_one("#defaults-scope", RadioSet).pressed_index
        if index is None or not 0 <= index < len(self._SCOPES):
            return None
        scope = self._SCOPES[index]
        if scope is not DefaultsScope.USER and self._repo_root is None:
            return None
        return scope

    def _show_shadowed_warning(self, index: int | None) -> None:
        warning = self.query_one("#shadowed-warning", Static)
        selected_project_scope = index in (1, 2)
        warning.set_class(not (selected_project_scope and self._shadowed), "-hidden")

    def _warning_text(self) -> str:
        if not self._shadowed:
            return ""
        fields = ", ".join(self._shadowed)
        return f"User-level defaults win, so these fields will not take effect: {fields}."
