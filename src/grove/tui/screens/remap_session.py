"""RemapSessionScreen — manually pin a session as the workspace's primary.

The TUI counterpart to ``WorkspaceManager.remap_session``: a picker over
``SessionExplorer.candidates_for(workspace_id)`` — the UNGATED cwd-scoped
scan, never the gated ``for_workspace`` the sessions browser reads. The
whole point of remap is letting the operator hand-pick a session the
auto-adoption heuristic rejected (a dead-minted-pointer's pre-birth live
successor, a foreign session sharing a ROOT cwd), so the picker must offer
exactly the set the gate withholds — using ``for_workspace`` here would
hide the very sessions a user opens this screen to find.

Reuses ``SessionList``/``SessionRow`` (``screens/sessions.py``) verbatim —
zero new list-rendering code. Focus model follows the sessions browser
(the list owns focus directly), not the project-picker's command-palette
model: there is no filter input, and Textual's own ``ListView`` binds
``enter`` to ``action_select_cursor`` which posts ``Selected`` for us.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import Label, ListView, Static

from grove.core import SessionListing
from grove.tui.screens._modal import GroveModal
from grove.tui.screens.sessions import SessionList, SessionRow
from grove.tui.widgets.footer import ContextualFooter, FooterKey


class RemapSessionScreen(GroveModal[str | None]):
    """Session picker for manual remap. Dismisses with the chosen session id, or None."""

    DEFAULT_CSS = """
    RemapSessionScreen .grove-dialog {
        width: 80;
    }
    RemapSessionScreen SessionList {
        width: 1fr;
        height: auto;
        max-height: 20;
        margin-top: 1;
        margin-right: 0;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, candidates: tuple[SessionListing, ...], *, workspace_title: str) -> None:
        super().__init__()
        self._candidates = candidates
        self._workspace_title = workspace_title

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            # markup=False: the workspace title is user-authored text and
            # must render as literals, never be parsed as Rich markup.
            yield Label(
                f"Remap session — {self._workspace_title}",
                classes="grove-dialog-title",
                markup=False,
            )
            if self._candidates:
                yield SessionList()
            else:
                # Defensive only — action_remap_session flashes and never
                # pushes this screen when candidates_for() is empty.
                yield Static(
                    "no sessions found in this workspace's directories",
                    classes="grove-detail",
                )
        yield ContextualFooter()

    def on_mount(self) -> None:
        if self._candidates:
            self.query_one(SessionList).populate(self._candidates, dark=self.app.current_theme.dark)
            self.query_one(ContextualFooter).set_keys(
                [FooterKey("enter", "Pin"), FooterKey("escape", "Cancel")]
            )
            self.query_one(SessionList).focus()
        else:
            self.query_one(ContextualFooter).set_keys([FooterKey("escape", "Cancel")])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SessionRow):
            self.dismiss(item.listing.summary.session_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
