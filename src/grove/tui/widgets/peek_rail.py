"""PeekRail — right-side rail that renders one WorkspacePeek.

The rail is purely a renderer: callers compute a `WorkspacePeek` via
`WorkspaceManager.peek(id)` and hand it in. We never call git or tmux.
That keeps the test seam at `manager.peek` and lets the same data shape
serve any future client.

Layout: two stacked `Static` cards. The **workspace card** carries the
metadata (title, status, branch, stats, init failure, paused affordance,
recent commits) — its border stays in `$secondary` because metadata
describes state, it doesn't *carry* attention. The **pane card** mirrors
the live agent pane; while the workspace is RUNNING it gains the `-live`
class and its border switches to `$primary` (the brand clay) so the eye
can find "what's actually live" at a glance. When not running, the pane
card hides via `-hidden` and the workspace card carries the affordance.

Why two Statics rather than one (cf. CLAUDE.md): the rule against many
child widgets was about `dozens` of nested widgets churning Textual's
reactive layout per frame. Two cards have aligned paint cadences — the
fast pane tick (~4 Hz, diff guarded) only repaints the pane card; the
selection-driven tick (debounced 80 ms) repaints both. So the split
*reduces* per-frame work on the hot path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import humanize
from rich.style import Style
from rich.text import Span, Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from grove.core import InitStatus, WorkspacePeek, WorkspaceStatus
from grove.core.workspace import LIVE_STATUSES
from grove.tui._status import (
    chrome_color,
    init_status_color,
    ref_color,
    status_color,
)

_SUBJECT_TRIM = 56
_PANE_TAIL_LINES = 30


class PeekRail(Vertical):
    """Right-hand rail rendering a `WorkspacePeek` as two bordered cards."""

    # Card chrome (background + border + border-title-color + padding) is
    # hoisted into GroveApp.CSS as `.grove-card`. Local rules below only
    # cover layout + the live-pane border swap specific to this rail.
    DEFAULT_CSS = """
    PeekRail {
        width: 60%;
        min-width: 36;
        padding: 0 1;
    }
    PeekRail #card-workspace {
        height: auto;
        margin-bottom: 1;
    }
    PeekRail #card-pane {
        height: 1fr;
    }
    PeekRail #card-pane.-live {
        border: round $primary;
    }
    PeekRail #card-pane.-hidden {
        display: none;
    }
    PeekRail.-empty #card-workspace {
        color: $text-muted;
    }
    """

    _EMPTY_PLACEHOLDER: ClassVar[str] = "(no workspace selected)"

    def __init__(self) -> None:
        super().__init__()
        # Mirrors what each card currently displays (plain text). Used as
        # the per-card diff guard for `set_peek` so identical successive
        # frames coalesce, and as the test seam via `body_text`.
        self._workspace_text: str = self._EMPTY_PLACEHOLDER
        self._pane_text: str = ""

    def compose(self) -> ComposeResult:
        yield Static(self._EMPTY_PLACEHOLDER, id="card-workspace", classes="grove-card")
        yield Static("", id="card-pane", classes="grove-card -hidden")

    def on_mount(self) -> None:
        self.add_class("-empty")
        # `border_title` is set after compose because Textual binds it on
        # the Static instance; doing it in compose() would race with mount.
        # Titles are deliberately distinct from the list panel's "workspaces"
        # — earlier revisions had "workspace" + "workspaces" living one
        # column apart, which the eye reads as a typo. "summary" names the
        # left card's role; "Live Workspace Preview" names the right card
        # explicitly — earlier "agent" was inaccurate because the captured
        # window can host any process the user runs (shell, htop, lazygit,
        # etc.), not strictly an LLM agent.
        self.query_one("#card-workspace", Static).border_title = "summary"
        self.query_one("#card-pane", Static).border_title = "Live Workspace Preview"

    def set_peek(self, peek: WorkspacePeek | None) -> None:
        """Render the rail for `peek`, or show the empty placeholder if None.

        Each card has its own diff guard: the pane card repaints at 4 Hz
        on the fast tick and short-circuits when an idle agent emits the
        same frame; the workspace card only repaints on selection change
        and short-circuits when `peek` is structurally identical (same
        rendered metadata).
        """
        ws_card = self.query_one("#card-workspace", Static)
        pane_card = self.query_one("#card-pane", Static)

        if peek is None:
            self._set_workspace(ws_card, self._EMPTY_PLACEHOLDER)
            self._hide_pane(pane_card)
            self.add_class("-empty")
            return

        self.remove_class("-empty")
        self._set_workspace(ws_card, _render_workspace(peek, dark=self.app.current_theme.dark))
        if peek.state.status in LIVE_STATUSES:
            self._show_pane(pane_card, _render_pane_body(peek))
        else:
            self._hide_pane(pane_card)

    @property
    def body_text(self) -> str:
        """Last rendered body as plain text. Stable seam for tests.

        Concatenates the workspace card and pane card so existing tests
        ("title in rail.body_text", "frame-two in rail.body_text") work
        unchanged across the structural split.
        """
        if self._pane_text:
            return f"{self._workspace_text}\n{self._pane_text}"
        return self._workspace_text

    # ─── private: per-card diff-guarded updates ───────────────────────────

    def _set_workspace(self, card: Static, content: Text | str) -> None:
        plain = content.plain if isinstance(content, Text) else content
        if plain == self._workspace_text:
            return
        self._workspace_text = plain
        card.update(content)

    def _show_pane(self, card: Static, content: Text) -> None:
        if card.has_class("-hidden"):
            card.remove_class("-hidden")
        if not card.has_class("-live"):
            card.add_class("-live")
        plain = content.plain
        if plain == self._pane_text:
            return
        self._pane_text = plain
        card.update(content)

    def _hide_pane(self, card: Static) -> None:
        if not card.has_class("-hidden"):
            card.add_class("-hidden")
        if card.has_class("-live"):
            card.remove_class("-live")
        # Hidden card stays whatever it was; clearing tracked text lets
        # body_text fall back to the workspace card alone for tests.
        self._pane_text = ""


def _render_peek(peek: WorkspacePeek, *, dark: bool = True) -> Text:
    """Concatenate workspace + pane into one Text. Test seam preserved.

    Keeps existing pure-render tests (`_render_peek(peek).plain`) working
    after the split — we still produce the same rendered content; the
    rail just lays it across two cards instead of one body.
    """
    text = _render_workspace(peek, dark=dark)
    if peek.state.status in LIVE_STATUSES:
        text.append("\n")
        text.append_text(_render_pane_body(peek))
    return text


def _render_workspace(peek: WorkspacePeek, *, dark: bool = True) -> Text:  # noqa: PLR0915
    """Workspace card body: live stats / init-failure / lifecycle affordances / commits.

    Pure function — easy to unit-test. `dark` selects the active theme's
    hex palette; widgets pass `app.current_theme.dark` so colors track
    runtime theme switches.

    The header (title + status + branch + base) lived here in earlier
    revisions; it moved to the card list once each row became its own
    `WorkspaceCard`. The rail now carries only what the card cannot:
    live git counts, init-failure log path, paused / offline / orphaned
    affordances with their action keys, and recent commits.

    Typography here intentionally tiers content into three weights so the
    card reads at a glance:

    * **bold + colored** — values the eye lands on first (diff counts,
      commit shas, affordance keys). Color comes from the semantic
      palette (``ref_add`` / ``ref_remove`` / ``branch`` / ``status_*``)
      so the same accent shows up wherever a user expects it.
    * **muted** — connectives and zero-state stats (`·`, `log:`, commit
      timestamps, `ahead 0`). Muted comes from ``chrome_color('muted')``
      so it tracks the active theme rather than the terminal's own dim
      interpretation.

    Stats colors are **polarity-aware**: `ahead`, `behind`, `dirty`
    render in muted hex while their value is zero (no signal) and
    promote to a semantic color (green for ahead, amber for behind /
    dirty) the moment the value is nonzero. The label *and* value share
    the polarity — pairing them as one chunk preserves scan-ability:
    "is there work to push? something to pull? something to clean up?"
    becomes a glance check, not a multi-token reading task.
    """
    s = peek.state
    add_hex = ref_color("diff_add", dark=dark)
    rem_hex = ref_color("diff_remove", dark=dark)
    branch_hex = ref_color("branch", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    paused_hex = status_color(WorkspaceStatus.PAUSED, dark=dark)
    offline_hex = status_color(WorkspaceStatus.OFFLINE, dark=dark)
    orphaned_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
    # ORPHANED's amber doubles as the "behind / dirty" warning hue —
    # both surfaces describe "work that needs attention" without rising
    # to the destructive-red tier reserved for ERROR / init-failed.
    warn_hex = orphaned_hex
    fail_hex = init_status_color(InitStatus.FAILED, dark=dark)

    text = Text()
    sep = f"  [{muted_hex}]·[/]  "
    # Description trim — keep the rail card legible on narrow terminals
    # without truncating so aggressively that a one-sentence note loses
    # its tail. 200 is comfortably more than one line at the rail's
    # default 60% width.
    _DESCRIPTION_TRIM = 200

    def _markup(line: str) -> None:
        text.append_text(Text.from_markup(line))
        text.append("\n")

    def _stat(label: str, value: int, active_hex: str) -> str:
        """Polarity-aware stat chunk: muted at zero, semantic when nonzero."""
        color = active_hex if value > 0 else muted_hex
        weight = "bold " if value > 0 else ""
        return f"[{color}]{label}[/] [{weight}{color}]{value}[/]"

    # Stats — first line of the rail card now that the workspace card
    # list carries title / status / branch / base. Each group reads as
    # `<label> <value>` in matching polarity color, separated by muted
    # dots so the row scans like a status bar.
    diff = (
        f"[bold {add_hex}]+{peek.diff_added}[/]"
        f" [{muted_hex}]/[/]"
        f" [bold {rem_hex}]-{peek.diff_removed}[/]"
    )
    _markup(
        f"{diff}"
        f"{sep}{_stat('ahead', peek.base_ahead, add_hex)}"
        f"{sep}{_stat('behind', peek.base_behind, warn_hex)}"
        f"{sep}{_stat('dirty', peek.dirty_files, warn_hex)}"
    )

    # User-supplied description (optional). Default fg, no special color
    # — it's a free-form note, not a status signal. Trimmed at
    # _DESCRIPTION_TRIM with an ellipsis so a long paste doesn't hijack
    # the rail's vertical budget. Skipped entirely when empty so we don't
    # ship a "(no description)" placeholder on every workspace.
    if s.description:
        text.append("\n")
        desc = (
            s.description
            if len(s.description) <= _DESCRIPTION_TRIM
            else s.description[: _DESCRIPTION_TRIM - 1] + "…"
        )
        # Plain Text.append (not _markup) so any `[...]` in the user's
        # description is treated as literal characters, not Rich markup.
        text.append(desc)
        text.append("\n")

    # Init failure surfaces a path the user can follow.
    if s.init_status == InitStatus.FAILED:
        text.append("\n")
        _markup(f"[bold {fail_hex}]✗ init failed[/]")
        if s.init_log_path:
            _markup(f"[{muted_hex}]log:[/] {s.init_log_path}")

    # Paused affordance — the worktree is gone; tell the user how to bring it back.
    # Coloured with the (neutral gray) paused token, not amber: pause is
    # deliberate, not a warning.
    if s.status == WorkspaceStatus.PAUSED:
        text.append("\n")
        _markup(
            f"[{paused_hex}]‖ paused[/]  [{muted_hex}]press[/]"
            f" [bold {paused_hex}]R[/] [{muted_hex}]to resume[/]"
        )

    # Offline affordance — tmux session vanished but worktree is intact.
    # Different from pause: respawn doesn't recreate the worktree.
    if s.status == WorkspaceStatus.OFFLINE:
        text.append("\n")
        _markup(
            f"[{offline_hex}]○ offline[/]  [{muted_hex}]press[/]"
            f" [bold {offline_hex}]o[/] [{muted_hex}]to respawn[/]"
        )

    # Orphaned: worktree directory is gone (user deleted it externally, or
    # disk failed). Cannot recover automatically; the only safe action is
    # to clean up the stranded record via kill.
    if s.status == WorkspaceStatus.ORPHANED:
        text.append("\n")
        _markup(
            f"[bold {orphaned_hex}]⊘ worktree missing on disk[/]  "
            f"[{muted_hex}]press[/] [bold {orphaned_hex}]k[/] [{muted_hex}]to clean up[/]"
        )

    # Error: persisted error_detail tells the user what went wrong.
    if s.status == WorkspaceStatus.ERROR and s.error_detail:
        text.append("\n")
        _markup(f"[{muted_hex}]error:[/] {s.error_detail}")

    # Recent commits (newest first). Section heading takes the branch
    # accent (teal) so the title and the SHA column below share the
    # same color — the eye groups them as one unit. Subject stays
    # default fg, age muted.
    if peek.recent_commits:
        text.append("\n")
        _markup(f"[bold {branch_hex}]recent[/]")
        now = datetime.now(tz=UTC)
        for c in peek.recent_commits:
            ago = humanize.naturaltime(now - c.committed_at)
            subject = (
                c.subject
                if len(c.subject) <= _SUBJECT_TRIM
                else c.subject[: _SUBJECT_TRIM - 1] + "…"
            )
            _markup(f"  [bold {branch_hex}]{c.sha[:8]:>8}[/]  {subject}  [{muted_hex}]{ago}[/]")

    return text


def _render_pane_body(peek: WorkspacePeek) -> Text:
    """Pane card body. Caller guarantees `peek.state.status == RUNNING`.

    Returns the SGR-decoded snapshot tail, or a `(no output)` placeholder
    when the capture is empty (tmux missing, pane has no output yet, etc.).

    `no_wrap = True` so lines wider than the card crop instead of wrapping.
    The source tmux pane is never resized to match the card — that would
    mutate a session the user might be attached to elsewhere; clipping
    locally is the right place to absorb the width mismatch.

    Background SGR codes from the captured snapshot are stripped before
    rendering: the agent's own terminal bg (`#1e1e1e`-ish in most setups)
    paints a cell-by-cell rectangle behind every glyph, which fights the
    card's own `$surface` bg and reads as a dark slab inside an already-
    dark panel. Stripping bg lets the host surface show through.
    """
    if peek.agent_snapshot:
        snap_lines = peek.agent_snapshot.splitlines()[-_PANE_TAIL_LINES:]
        text = _strip_pane_bgcolors(Text.from_ansi("\n".join(snap_lines)))
    else:
        text = Text.from_markup("[dim](no output)[/]")
    text.no_wrap = True
    return text


def _strip_pane_bgcolors(text: Text) -> Text:
    """Clear `bgcolor` from every styled span on `text` (in place).

    `tmux capture-pane -e` carries the source terminal's bg color in its
    SGR cells. Rendering those into a `Static` on the card's `$surface`
    paints a darker rectangle over every glyph, so the panel's own bg
    never reads. Replace each span's `Style` with one that mirrors all fg
    attributes (color, bold, italic, underline, etc.) but omits bgcolor.
    `Style` is immutable, so we construct a fresh one per span.
    """
    text.spans[:] = [
        Span(s.start, s.end, _without_bg(s.style)) if isinstance(s.style, Style) else s
        for s in text.spans
    ]
    return text


def _without_bg(s: Style) -> Style:
    """Return a copy of `s` with bgcolor unset; preserves common fg/style attrs."""
    return Style(
        color=s.color,
        bold=s.bold,
        dim=s.dim,
        italic=s.italic,
        underline=s.underline,
        blink=s.blink,
        reverse=s.reverse,
        strike=s.strike,
        link=s.link,
    )
