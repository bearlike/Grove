"""EditWorkspaceScreen — rename title and/or set description.

Returns a Pydantic ``UpdateWorkspaceRequest`` on submit, ``None`` on
cancel. The screen pre-fills both inputs with the current state so a
user can leave a field unchanged simply by not editing it.

Wire semantics on submit:
- title is always sent — UpdateWorkspaceRequest validates non-empty.
- description is sent as the current input value (which may be empty
  string to clear, or non-empty to set). The engine treats empty string
  equivalent to None.

The modal refuses to submit with an empty title (bells, keeps focus on
the title field) — same UX as the create modal. Tests pin both branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static

from grove.core.contracts.requests import UpdateWorkspaceRequest
from grove.core.workspace import Runtime
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


@dataclass(frozen=True, slots=True)
class RetryContainerization:
    """Dismiss sentinel for the edit modal's "Retry containerization" button.

    Runtime is a create-time fact, never editable — the ONE exception, under
    "parity of OPTIONS, not parity of MUTABILITY", is a VERB
    (`respawn`, when a fallback reason is set), not a field edit. A sibling
    return shape rather than widening ``UpdateWorkspaceRequest``, the same
    pattern ``KillDecision`` uses next to the plain-bool ``ConfirmScreen``.
    """


class EditWorkspaceScreen(GroveModal[UpdateWorkspaceRequest | RetryContainerization | None]):
    """Title + description editor, plus a read-only runtime row.

    Returns ``UpdateWorkspaceRequest`` (Save), ``RetryContainerization``
    (the fallback-only retry button), or ``None`` (Cancel).
    """

    DEFAULT_CSS = """
    EditWorkspaceScreen .grove-dialog {
        width: 80;
    }
    EditWorkspaceScreen .field-label {
        margin-top: 1;
    }
    EditWorkspaceScreen #runtime-row {
        margin-top: 1;
        color: $text-muted;
    }
    EditWorkspaceScreen #runtime-fallback {
        color: $warning;
    }
    EditWorkspaceScreen #runtime-no-tmux {
        color: $warning;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Save"),
    ]

    def __init__(
        self,
        *,
        current_title: str,
        current_description: str | None,
        current_runtime: Runtime = Runtime.HOST,
        current_runtime_fallback_reason: str | None = None,
        current_runtime_no_tmux: bool = False,
    ) -> None:
        super().__init__()
        self._current_title = current_title
        # Render an unset description as an empty string in the input;
        # submit-time semantics turn empty back into None on the wire.
        self._current_description = current_description or ""
        self._runtime = current_runtime
        self._runtime_fallback_reason = current_runtime_fallback_reason
        self._runtime_no_tmux = current_runtime_no_tmux

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label("Edit workspace", classes="grove-dialog-title")
            yield Label("Title:", classes="field-label")
            yield Input(value=self._current_title, id="title")
            yield Label("Description:", classes="field-label")
            yield Input(
                value=self._current_description,
                placeholder="(optional)",
                id="description",
            )
            yield Static(f"runtime: {self._runtime.value}", id="runtime-row")
            if self._runtime_fallback_reason:
                yield Static(
                    f"⚠ container fallback: {self._runtime_fallback_reason}",
                    id="runtime-fallback",
                )
            if self._runtime_no_tmux:
                yield Static(
                    "⚠ no in-container tmux: agent dies with your terminal — "
                    "install tmux in the image, or Grove has no bundle for "
                    "this architecture",
                    id="runtime-no-tmux",
                )
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Cancel", id="cancel", variant="default")
                if self._runtime_fallback_reason:
                    yield Button("Retry containerization", id="retry", variant="warning")
                yield Button("Save (Ctrl-S)", id="submit", variant="primary")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("escape", "Cancel"),
                FooterKey("ctrl+s", "Save"),
            ]
        )
        self.query_one("#title", Input).focus()

    # ─── actions ───────────────────────────────────────────────────────────

    def action_submit(self) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._submit()
        elif event.button.id == "retry":
            self.dismiss(RetryContainerization())
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        # Pressing Enter in either input submits the form.
        self._submit()

    def _submit(self) -> None:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.app.bell()
            self.query_one("#title", Input).focus()
            return
        description_raw = self.query_one("#description", Input).value
        try:
            request = UpdateWorkspaceRequest(
                title=title,
                description=description_raw,
            )
        except Exception:
            self.app.bell()
            return
        self.dismiss(request)
