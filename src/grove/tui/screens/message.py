"""SendMessageScreen — steer the selected workspace's agent with a follow-up.

Returns the message text on send, ``None`` on cancel. The modal is a dumb
single-field form: it never talks to the manager — the list screen owns
the ``send_message`` call so typed engine errors surface through the
screen's existing flash convention (same split as the edit modal). The
per-agent-kind dispatch (tmux inject vs remote API) is entirely
manager-side; this modal never reads ``agent_kind``.

The modal refuses to submit an empty message (bell, keeps focus on the
input) — same UX as the create/edit modals' empty-title refusal.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label

from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class SendMessageScreen(GroveModal[str | None]):
    """Single-line follow-up composer. Returns the message text or ``None``."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, workspace_title: str) -> None:
        super().__init__()
        self._workspace_title = workspace_title

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label("Send message", classes="grove-dialog-title")
            # markup=False: the workspace title is user-authored text and
            # must render as literals, never be parsed as Rich markup.
            yield Label(f"message to {self._workspace_title}:", markup=False)
            yield Input(placeholder="follow-up for the agent", id="message")
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Cancel", id="cancel", variant="default")
                yield Button("Send (Enter)", id="submit", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("escape", "Cancel"),
                FooterKey("enter", "Send"),
            ]
        )
        self.query_one("#message", Input).focus()

    # ─── actions ───────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        text = self.query_one("#message", Input).value.strip()
        if not text:
            self.app.bell()
            self.query_one("#message", Input).focus()
            return
        self.dismiss(text)
