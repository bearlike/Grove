"""ProjectPickerScreen — switch between registered repos without leaving the TUI (#59).

``WorkspaceListScreen`` is repo-scoped: it binds one ``WorkspaceManager`` to
the launch CWD. The cross-project ``DashboardScreen`` (``d``) is read-only. This
modal is the write-capable cross-project *navigator*: ``P`` opens a searchable
overlay of every repo the store knows, and selecting one dismisses with that
repo's root so the list screen can re-point at it (a fresh ``WorkspaceManager``
from the shared ``RepoRegistry``).

The data source is the same registry the daemon and the dashboard use — but the
picker reads it the *cheap* way. Per-repo counts come from one whole-file
``store.load_all()`` grouped by ``repo_root`` (``RepoChoice.group``), with **no**
git/tmux reconciliation: the picker is a navigation chooser, not an activity
view, so it must open instantly even with many repos. The heavy live-status
view already exists — it's the dashboard.

Keyboard-first, mirroring a command palette: the filter ``Input`` holds focus so
the user types to narrow; ``↑``/``↓`` drive the list under it; ``enter`` (or a
click) picks; ``escape`` cancels. The current repo floats to the top, tagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from grove.core.workspace import WorkspaceState
from grove.tui._status import chrome_color
from grove.tui.screens._modal import GroveModal
from grove.tui.widgets.footer import ContextualFooter, FooterKey


@dataclass(frozen=True, slots=True)
class RepoChoice:
    """One selectable repo: its root, display name, and workspace count.

    In-process TUI state (never crosses a wire) → plain frozen dataclass, not
    Pydantic. ``is_current`` marks the repo the user is already in so the modal
    can tag it and float it to the top.
    """

    repo_root: Path
    name: str
    count: int
    is_current: bool

    @classmethod
    def group(cls, states: list[WorkspaceState], *, current: Path) -> list[RepoChoice]:
        """Group persisted states by ``repo_root`` into per-repo choices.

        Pure: the caller does the (cheap) ``store.load_all()`` read and hands the
        states in. ``repo_root`` is already a resolved string on every record, so
        ``Path`` of it is canonical; ``current`` is resolved here to match. The
        current repo is always present even with zero workspaces, so the user can
        always see — and stay on — where they are. Sorted current-first, then by
        name, so the chooser reads predictably.
        """
        current_resolved = current.resolve()
        counts: dict[Path, int] = {}
        for state in states:
            root = Path(state.repo_root)
            counts[root] = counts.get(root, 0) + 1
        counts.setdefault(current_resolved, 0)
        choices = [
            cls(repo_root=root, name=root.name, count=count, is_current=root == current_resolved)
            for root, count in counts.items()
        ]
        choices.sort(key=lambda c: (not c.is_current, c.name.lower()))
        return choices


class RepoRow(ListItem):
    """One repo as a ListItem; body is a single Static rendered via Rich Text.

    Focus chrome is TCSS-only, same contract as ``SessionRow`` / ``WorkspaceCard``:
    a transparent ``round $surface`` border by default, gray on hover, clay when
    the parent list is focused and this row is highlighted. Fixed height keeps
    the highlight swap layout-stable.
    """

    DEFAULT_CSS = """
    RepoRow {
        height: 3;
        padding: 0 1;
        border: round $surface;
    }
    RepoRow:hover {
        border: round $secondary;
    }
    """

    def __init__(self, choice: RepoChoice, *, dark: bool) -> None:
        super().__init__()
        self.choice = choice
        self._dark = dark

    def compose(self) -> ComposeResult:
        yield Static(_render_repo_row(self.choice, dark=self._dark))

    @property
    def body_text(self) -> str:
        """Plain rendered body — the test seam."""
        content = self.query_one(Static).content
        return content.plain if isinstance(content, Text) else str(content)


class RepoList(ListView):
    """Keyboard-navigable list of repos, newest selection floats from filtering."""

    DEFAULT_CSS = """
    RepoList {
        height: auto;
        max-height: 20;
        background: $surface;
        border: round $secondary;
        margin-top: 1;
    }
    /* The Input keeps focus (palette model), so the highlight must read
     * without the list being focused — unlike SessionList/WorkspaceList where
     * the list owns focus and gates the clay rule on :focus. */
    RepoList > RepoRow.-highlight {
        background: $panel;
        border: round $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._choices: tuple[RepoChoice, ...] = ()

    def populate(self, choices: list[RepoChoice], *, dark: bool) -> None:
        """Rebuild one RepoRow per choice; cursor lands on the first row."""
        self._choices = tuple(choices)
        self.clear()
        for choice in self._choices:
            self.append(RepoRow(choice, dark=dark))
        if self._choices:
            self.index = 0

    @property
    def selected_choice(self) -> RepoChoice | None:
        idx = self.index
        if idx is None or idx < 0 or idx >= len(self._choices):
            return None
        return self._choices[idx]


class ProjectPickerScreen(GroveModal[Path | None]):
    """Searchable repo overlay. Dismisses with the chosen repo_root, or None."""

    DEFAULT_CSS = """
    ProjectPickerScreen #project-filter {
        margin-bottom: 0;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, choices: list[RepoChoice]) -> None:
        super().__init__()
        self._choices = list(choices)

    def compose(self) -> ComposeResult:
        with Vertical(classes="grove-dialog"):
            yield Label("Switch project", classes="grove-dialog-title")
            yield Input(placeholder="filter projects…", id="project-filter")
            yield RepoList()
        yield ContextualFooter()

    def on_mount(self) -> None:
        self._populate(self._choices)
        self.query_one(ContextualFooter).set_keys(
            [FooterKey("enter", "Switch"), FooterKey("escape", "Cancel")]
        )
        # The filter holds focus so the user types-to-narrow immediately; the
        # list under it is driven via the forwarded arrows in on_key. This is
        # the command-palette focus model, not the list-screen one (where the
        # table is focused and the hidden filter must NOT steal hotkeys).
        self.query_one(Input).focus()

    def _populate(self, choices: list[RepoChoice]) -> None:
        self.query_one(RepoList).populate(choices, dark=self.app.current_theme.dark)

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        filtered = [c for c in self._choices if query in c.name.lower()] if query else self._choices
        self._populate(list(filtered))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        del event
        self._select_highlighted()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # A mouse click on a row focuses the list and fires Selected; read the
        # clicked row directly rather than the highlight (they coincide, but the
        # event item is the source of truth for a click).
        item = event.item
        if isinstance(item, RepoRow):
            self.dismiss(item.choice.repo_root)

    def on_key(self, event: events.Key) -> None:
        # Forward arrows to the list while the Input keeps focus. Input is
        # single-line, so it never binds up/down — they bubble here. Letters are
        # consumed by Input (text entry), so they never reach this handler.
        if event.key == "down":
            self.query_one(RepoList).action_cursor_down()
            event.stop()
        elif event.key == "up":
            self.query_one(RepoList).action_cursor_up()
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _select_highlighted(self) -> None:
        choice = self.query_one(RepoList).selected_choice
        if choice is not None:
            self.dismiss(choice.repo_root)


def _render_repo_row(choice: RepoChoice, *, dark: bool) -> Text:
    """Repo name (bold) · workspace count (muted) · a quiet ``current`` tag.

    Same typographic tiers as the dashboard project header and the session row:
    bold default-fg for the value the eye lands on, ``chrome_color('muted')`` for
    the count and the provenance-style ``current`` qualifier (absence is the
    default — non-current rows carry no tag).
    """
    muted = chrome_color("muted", dark=dark)
    text = Text()
    text.append(choice.name, style="bold")
    text.append(f"  ({choice.count})", style=muted)
    if choice.is_current:
        text.append("  · current", style=muted)
    return text
