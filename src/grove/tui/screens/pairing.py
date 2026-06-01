"""TUI surfaces for the auth domain — pairing-approval modal + sessions screen.

The TUI is the primary approval surface for browser pairing
requests on the daemon's host. It polls the engine's `SessionStore`
directly (no HTTP — TUI uses `LocalTransport`-style in-process access)
once a second; when a new pending challenge appears, the modal opens
with the human-readable code so the user can confirm it matches what
they see on the requesting device.

Approve / Deny call the engine `pair_approve` / `pair_deny` methods. The
token never reaches this UI — it flows out only through the daemon's
poll endpoint to the requesting client (browser). Same rule
as `grove auth approve` (see CLAUDE.md): a single exit path for the
secret prevents accidental leakage to logs or screenshots.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from grove.core.auth import PairingChallenge, SessionStore
from grove.core.errors import GroveError
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class PairingModal(GroveModal[bool]):
    """Approve / deny modal for a single pending pairing challenge.

    Renders the label + the code prominently (large monospaced text the
    user can match against the requesting device) plus an approve / deny
    pair. Dismisses with ``True`` for approve, ``False`` for deny / esc.
    The owning screen (``WorkspaceListScreen``) wires the engine call
    based on the dismissal value.
    """

    DEFAULT_CSS = """
    PairingModal .grove-dialog {
        width: 70;
    }
    PairingModal #pair-label {
        color: $text-muted;
        margin-bottom: 1;
    }
    PairingModal #pair-code {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-top: 1;
        margin-bottom: 1;
    }
    PairingModal #pair-help {
        color: $text-muted;
        text-align: center;
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a,enter", "approve", "Approve"),
        Binding("d,n,escape", "deny", "Deny"),
    ]

    def __init__(self, challenge: PairingChallenge) -> None:
        super().__init__()
        self._challenge = challenge

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label("Pair new device", classes="grove-dialog-title")
            yield Static(
                f"From: [b]{self._challenge.label}[/b]",
                id="pair-label",
                classes="grove-dialog-section",
                markup=True,
            )
            yield Static(self._challenge.code, id="pair-code")
            yield Static(
                "Approve only if the code on the requesting device matches.",
                id="pair-help",
            )
            with Horizontal(classes="grove-dialog-buttons"):
                yield Button("Approve (a)", id="yes", variant="primary")
                yield Button("Deny (d)", id="no", variant="default")
        yield ContextualFooter()

    def on_mount(self) -> None:
        self.query_one(ContextualFooter).set_keys(
            [FooterKey("a", "Approve"), FooterKey("d,escape", "Deny")]
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class PairingWatcher:
    """Single-instance helper that polls `SessionStore` and surfaces modals.

    Mounted by the app once. Each tick, queries `list_pending_challenges`
    and pushes a `PairingModal` for any newly-seen `PENDING` challenge.
    Tracks seen ids in-memory so a long-pending challenge doesn't re-prompt
    every second.

    Lives on the `GroveApp` (not the list screen) so the modal can fire
    even when the user is on a non-list screen — the modal appears on top
    regardless. The poll is cheap (one read of a small JSON file) so a
    1-second cadence is comfortable.
    """

    POLL_SECONDS: ClassVar[float] = 1.0

    def __init__(self, app_ref: object, *, store: SessionStore | None = None) -> None:
        # ``app_ref`` is the Textual ``App`` we push modals onto. Typed loose
        # so importing this module doesn't pull Textual until the TUI runs.
        self._app = app_ref
        self._store = store if store is not None else SessionStore()
        self._seen: set[UUID] = set()
        self._modal_open = False

    def tick(self) -> None:
        """Called by the app's `set_interval`. Best-effort — never raises."""
        try:
            pending = self._store.list_pending_challenges()
        except GroveError:
            return
        if self._modal_open:
            return
        for challenge in pending:
            if challenge.state.value != "pending":
                # Approved-but-not-yet-consumed: don't re-prompt.
                self._seen.add(challenge.challenge_id)
                continue
            if challenge.challenge_id in self._seen:
                continue
            self._show(challenge)
            return  # one modal at a time

    def _show(self, challenge: PairingChallenge) -> None:
        self._seen.add(challenge.challenge_id)
        self._modal_open = True

        def _on_dismiss(approved: bool | None) -> None:
            self._modal_open = False
            try:
                if approved is True:
                    self._store.pair_approve(challenge.challenge_id)
                elif approved is False:
                    self._store.pair_deny(challenge.challenge_id)
                # ``None`` means the modal was dismissed without a button —
                # treat as deferred (next tick re-evaluates).
            except GroveError:  # pragma: no cover — best-effort
                pass

        # Late import: PairingWatcher is constructed before the App type is
        # in scope at module load.
        push = getattr(self._app, "push_screen", None)
        if push is None:
            return
        push(PairingModal(challenge), _on_dismiss)
