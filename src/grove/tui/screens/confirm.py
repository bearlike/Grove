"""Confirmation modals for destructive workspace actions.

``ConfirmScreen`` is the generic yes/no modal — pause / etc. confirms.
``KillConfirmScreen`` is the kill-specific variant: it carries the same
"yes/no" question but also a ``Checkbox`` for "Also delete the local
branch" with a smart default driven by ``BranchProvenance``. It dismisses
with a small ``KillDecision`` payload so the caller knows both whether
to proceed and what to do with the branch.

Why a sibling class instead of widening ``ConfirmScreen``: kill needs a
richer return type (``KillDecision`` rather than ``bool``), and the
checkbox is specific to the kill flow. Two small classes that each say
one thing beats one bigger class that flexes to both.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Label, Static

from grove.core.workspace import BranchProvenance
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class ConfirmScreen(GroveModal[bool]):
    """Yes/no modal. Optional details block under the prompt for context."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y,enter", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def __init__(
        self,
        message: str,
        *,
        title: str = "Confirm",
        details: str | None = None,
        danger: bool = False,
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._details = details
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label(self._title, classes="grove-dialog-title")
            yield Static(self._message)
            if self._details:
                yield Static(
                    self._details,
                    id="details",
                    classes="grove-detail grove-dialog-section",
                )
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button(
                    "Yes (y)",
                    id="yes",
                    variant="error" if self._danger else "primary",
                )
                yield Button("No (n)", id="no", variant="default")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("y", "Yes"),
                FooterKey("n,escape", "No"),
            ]
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ─── kill-specific confirm ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KillDecision:
    """The outcome of ``KillConfirmScreen``.

    ``confirmed=False`` means the user cancelled (escape / 'n' / clicked
    No); ``delete_branch`` is undefined in that case but kept on the
    structure for typing simplicity. When ``confirmed=True``, the
    caller forwards ``delete_branch`` directly to
    ``WorkspaceManager.kill(workspace_id, delete_branch=...)``.
    """

    confirmed: bool
    delete_branch: bool


class KillConfirmScreen(GroveModal[KillDecision | None]):
    """Kill confirm with a "Also delete local branch" checkbox.

    Default for the checkbox depends on ``branch_provenance`` (passed at
    construction time):
      * ``GROVE_CREATED`` → checked  (Grove made the branch; safe to drop)
      * ``USER_ATTACHED`` → unchecked (user's pre-existing branch stays)

    A fixed "Remote branches are never touched by Grove" sub-line under
    the checkbox removes ambiguity about scope. Dismisses with
    ``KillDecision(True, delete_branch)`` on confirm or ``None`` on
    cancel/escape.
    """

    DEFAULT_CSS = """
    KillConfirmScreen .grove-dialog {
        width: 80;
    }
    KillConfirmScreen #branch-row {
        margin-top: 1;
        height: auto;
    }
    KillConfirmScreen #branch-note {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y,enter", "confirm", "Kill"),
        Binding("n,escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        message: str,
        *,
        branch_name: str,
        branch_provenance: BranchProvenance,
        title: str = "Kill",
        details: str | None = None,
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._details = details
        self._branch_name = branch_name
        self._default_delete = branch_provenance == BranchProvenance.GROVE_CREATED
        if self._default_delete:
            self._checkbox_label = f"Delete local branch `{branch_name}`"
        else:
            self._checkbox_label = f"Also delete local branch `{branch_name}` (attached by user)"

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label(self._title, classes="grove-dialog-title")
            yield Static(self._message)
            if self._details:
                yield Static(
                    self._details,
                    id="details",
                    classes="grove-detail grove-dialog-section",
                )
            with Vertical(id="branch-row"):
                yield Checkbox(
                    self._checkbox_label,
                    value=self._default_delete,
                    id="delete-branch",
                )
                yield Static(
                    "Remote branches are never touched by Grove.",
                    id="branch-note",
                )
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Kill (y)", id="yes", variant="error")
                yield Button("Cancel (n)", id="no", variant="default")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [
                FooterKey("y", "Kill"),
                FooterKey("n,escape", "Cancel"),
            ]
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.dismiss(self._decision(confirmed=True))
        else:
            self.dismiss(None)

    def action_confirm(self) -> None:
        self.dismiss(self._decision(confirmed=True))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _decision(self, *, confirmed: bool) -> KillDecision:
        delete = self._default_delete
        with contextlib.suppress(Exception):
            delete = bool(self.query_one("#delete-branch", Checkbox).value)
        return KillDecision(confirmed=confirmed, delete_branch=delete)
