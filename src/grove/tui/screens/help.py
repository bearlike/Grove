"""HelpScreen — context-aware modal listing what each key does right now.

The list groups bindings into "global" and "selection" so the user sees
what's currently meaningful: a key like `p` is dimmed when nothing is
selected. Generated from the binding spec list passed in so the help
screen and the actual bindings can't drift.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import Button, Static

from grove.tui.keys import LIST_GLOBAL_FOOTER_KEYS, LIST_SELECTION_FOOTER_KEYS
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class HelpScreen(GroveModal[None]):
    """Lists bindings; selection-only entries dim when nothing is selected."""

    DEFAULT_CSS = """
    HelpScreen .grove-dialog {
        width: 70;
    }
    HelpScreen .help-section-title {
        text-style: bold;
        margin-top: 1;
    }
    HelpScreen .help-key {
        color: $accent;
    }
    HelpScreen .help-disabled {
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,?", "dismiss_screen", "Close"),
    ]

    def __init__(
        self,
        key_specs: list[tuple[str, str, str]],
        *,
        has_selection: bool,
    ) -> None:
        super().__init__()
        self._key_specs = key_specs
        self._has_selection = has_selection

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Static("Grove keys", classes="grove-dialog-title")
            yield Static("Global", classes="help-section-title")
            yield Static(self._format_group(LIST_GLOBAL_FOOTER_KEYS, dim_if_no_selection=False))
            yield Static("Selection", classes="help-section-title")
            yield Static(
                self._format_group(LIST_SELECTION_FOOTER_KEYS, dim_if_no_selection=True),
            )
            yield Static(
                "press [bold]?[/] or [bold]Esc[/] to close",
                classes="grove-detail grove-dialog-section",
            )
            with Vertical(classes="grove-dialog-buttons"):
                yield Button("Close", id="close", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [FooterKey("escape,q,?", "Close")],
        )

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    # ─── helpers ──────────────────────────────────────────────────────────

    def _format_group(self, keys: tuple[str, ...], *, dim_if_no_selection: bool) -> str:
        rows: list[str] = []
        dim = dim_if_no_selection and not self._has_selection
        cls = "help-disabled" if dim else "help-key"
        for key, _action, label in self._key_specs:
            if key not in keys:
                continue
            display_key = key.replace(",", "/")
            rows.append(f"  [{cls}]{display_key:<10}[/]  {label}")
        return "\n".join(rows) or "  (none)"
