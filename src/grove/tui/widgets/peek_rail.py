"""PeekRail — right-side rail that renders one WorkspacePeek.

The rail is purely a renderer: callers compute a `WorkspacePeek` via
`WorkspaceManager.peek(id)` and hand it in. We never call git or tmux.
That keeps the test seam at `manager.peek` and lets the same data shape
serve any future client.

Layout: the **workspace card** (a `Static`) carries the metadata (stats,
agent session metrics, description, init failure, lifecycle affordances,
recent commits) — its border stays in `$secondary` because metadata
describes state, it doesn't *carry* attention. Below it, a bounded,
internally-scrollable **tickets panel** lists every attached `TicketRef`
one per line (hidden entirely when the workspace has none) — it lives
apart from the workspace card specifically because that card is one
`Static` with no scroll of its own, so an unbounded ticket count would
otherwise push the stats/description/affordance/commits blocks off
screen. Below that, a **tabbed preview** (`TabbedContent`) holds two
panes: the **transcript tab** (a digest of the primary session's recent
turns, tool runs grouped — the rail never lists individual calls) and
the **terminal tab** (the live tmux pane mirror). While the workspace is
RUNNING the container gains the `-live` class and its border switches to
`$primary` (the brand clay) so the eye can find "what's actually live"
at a glance. With nothing to preview (not live, no recorded transcript)
the container hides via `-hidden` and the workspace card carries the
affordance.

Default-tab policy: transcript whenever turns exist for the selection,
else terminal — re-derived per selection, but a tab the user picked by
hand is respected until the selection changes (never fight the user).

Paint cadences stay aligned (cf. CLAUDE.md): the fast pane tick (~4 Hz,
diff guarded) only repaints the terminal pane's Static; the slow stats
tick / selection debounce repaint workspace card + transcript. Each
surface is one `Static` written via Rich `Text` — no per-frame widget
churn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import humanize
from rich.markup import escape
from rich.style import Style
from rich.text import Span, Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static, TabbedContent, TabPane

from grove.core import CommitSummary, InitStatus, WorkspacePeek, WorkspaceState, WorkspaceStatus
from grove.core.agents import AgentActivity, SessionTurn
from grove.core.contracts.tickets import TicketRef
from grove.core.workspace import LIVE_STATUSES, ProvisionProgress
from grove.tui._status import (
    PR_GLYPH,
    agent_state_color,
    agent_state_label,
    chrome_color,
    init_status_color,
    pr_status_color,
    ref_color,
    status_color,
)
from grove.tui._turns import render_transcript_digest
from grove.tui.widgets.card import format_wait, ticket_pill
from grove.tui.widgets.dashboard_grid import _human_tokens

_SUBJECT_TRIM = 56
_PANE_TAIL_LINES = 30
# Description trim — keep the rail card legible on narrow terminals
# without truncating so aggressively that a one-sentence note loses
# its tail. 200 is comfortably more than one line at the rail's
# default 60% width.
_DESCRIPTION_TRIM = 200


class PeekRail(Vertical):
    """Right-hand rail rendering a `WorkspacePeek` as two bordered cards."""

    # Card chrome (background + border + border-title-color + padding) is
    # hoisted into GroveApp.CSS as `.grove-card`. Local rules below only
    # cover layout + the live border swap specific to this rail. The tab
    # bar itself keeps Textual's stock Tabs chrome: the active-tab
    # underline resolves to `$accent` (= the brand clay on Grove themes),
    # so no per-tab color overrides are needed — focus/hover chrome stays
    # TCSS-only, never glyphs in rendered text.
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
    PeekRail #card-tickets {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
        scrollbar-size-vertical: 1;
    }
    PeekRail #card-tickets.-hidden {
        display: none;
    }
    /* One row per ticket, cropped rather than wrapped. `Text.no_wrap` is
       NOT enough on its own: set as an attribute it is silently ignored on
       the way through the render pipeline (measured — the same string wraps
       with the attribute set and crops only when no-wrap is passed at print
       time), so the panel wrapped every title onto a second line and halved
       how many tickets fit. The style is the authority. */
    PeekRail #tickets-body {
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    PeekRail #peek-tabs {
        height: 1fr;
    }
    PeekRail #peek-tabs ContentSwitcher {
        height: 1fr;
    }
    PeekRail #peek-tabs TabPane {
        height: 1fr;
        padding: 0;
    }
    PeekRail #peek-tabs.-live {
        border: round $primary;
    }
    PeekRail #peek-tabs.-hidden {
        display: none;
    }
    PeekRail #transcript-scroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }
    PeekRail.-empty #card-workspace {
        color: $text-muted;
    }
    """

    _EMPTY_PLACEHOLDER: ClassVar[str] = "(no workspace selected)"
    _NO_TRANSCRIPT: ClassVar[str] = "[dim](no transcript)[/]"
    _TRANSCRIPT_TAB: ClassVar[str] = "tab-transcript"
    _TERMINAL_TAB: ClassVar[str] = "tab-terminal"

    def __init__(self) -> None:
        super().__init__()
        # Mirrors what each surface currently displays (plain text). Used
        # as the per-surface diff guard for `set_peek` so identical
        # successive frames coalesce, and as the test seam via `body_text`.
        self._workspace_text: str = self._EMPTY_PLACEHOLDER
        self._pane_text: str = ""
        self._transcript_text: str = ""
        self._tickets_text: str = ""
        # Default-tab bookkeeping: which selection the current tab choice
        # belongs to, and whether the user picked the tab by hand for it.
        self._tab_wid: str | None = None
        self._user_tab_choice = False
        # Pane ids of programmatic tab switches whose TabActivated echo we
        # have not yet consumed. TabbedContent posts the same message for
        # our own `.active` writes and for user clicks; without this set
        # the rail would mistake its own default-switch (or the framework's
        # first-tab activation at mount — pre-seeded below) for a user
        # choice and stop applying defaults.
        self._auto_switches: set[str] = {self._TRANSCRIPT_TAB}

    def compose(self) -> ComposeResult:
        yield Static(self._EMPTY_PLACEHOLDER, id="card-workspace", classes="grove-card")
        # Own bounded, scrollable panel — never inline in the summary card,
        # which is one Static with no scroll of its own. Hidden by default
        # (`-hidden`) until `_update_tickets` finds a workspace with any
        # `ticket_refs`; the border title carries the count (`tickets N`),
        # set dynamically there since it isn't known at compose time.
        with VerticalScroll(id="card-tickets", classes="grove-card -hidden"):
            yield Static("", id="tickets-body")
        with TabbedContent(id="peek-tabs", classes="grove-card -hidden"):
            # Scroll container so the newest exchange (the tail) stays
            # reachable when the digest outgrows the pane — same shape as
            # the sessions screen's history panel.
            with (
                TabPane("transcript", id=self._TRANSCRIPT_TAB),
                VerticalScroll(id="transcript-scroll"),
            ):
                yield Static(self._NO_TRANSCRIPT, id="card-transcript")
            with TabPane("terminal", id=self._TERMINAL_TAB):
                yield Static("", id="card-pane")

    def on_mount(self) -> None:
        self.add_class("-empty")
        # `border_title` is set after compose because Textual binds it on
        # the widget instance; doing it in compose() would race with mount.
        # Titles are deliberately distinct from the list panel's
        # "workspaces" — earlier revisions had "workspace" + "workspaces"
        # living one column apart, which the eye reads as a typo. "summary"
        # names the left card's role; "preview" names the tabbed container
        # (its tabs — transcript / terminal — name the two content shapes).
        self.query_one("#card-workspace", Static).border_title = "summary"
        self.query_one("#peek-tabs", TabbedContent).border_title = "preview"

    def set_peek(
        self,
        peek: WorkspacePeek | None,
        *,
        agent: AgentActivity | None = None,
        turns: tuple[SessionTurn, ...] = (),
        provision: ProvisionProgress | None = None,
    ) -> None:
        """Render the rail for `peek`, or show the empty placeholder if None.

        Each surface has its own diff guard: the terminal pane repaints at
        4 Hz on the fast tick and short-circuits when an idle agent emits
        the same frame; the workspace card and transcript digest repaint
        on selection change / slow tick and short-circuit when the
        rendered plain text is identical.

        ``agent`` is the selected row's primary ``AgentActivity`` from the
        list screen's activity-tick map (no extra transcript parse on this
        path). ``turns`` is the same session's recent-turns tail, fetched
        by the list screen's slow path; both are keyword-only with empty
        defaults so callers without those axes — and the pre-existing
        tests — stay untouched.

        ``provision`` is the in-flight container build's progress, read by the
        list screen only when the selection is PROVISIONING. It takes over the
        terminal tab: a provisioning workspace has no tmux pane to mirror yet,
        and the provisioner's output is the only thing there is to watch — so
        the tab that normally shows "what the agent's terminal is doing" shows
        "what the build is doing", with no second log widget invented for it.
        """
        ws_card = self.query_one("#card-workspace", Static)

        if peek is None:
            self._set_workspace(ws_card, self._EMPTY_PLACEHOLDER)
            self._update_tickets([])
            self._hide_tabs()
            self.add_class("-empty")
            return

        self.remove_class("-empty")
        self._set_workspace(
            ws_card,
            _render_workspace(
                peek, dark=self.app.current_theme.dark, agent=agent, provision=provision
            ),
        )
        self._update_tickets(peek.state.ticket_refs)
        live = peek.state.status in LIVE_STATUSES
        # Progress only counts while the status still says PROVISIONING: the
        # stamps and the log outlive the build, so a settled workspace would
        # otherwise keep showing the log of a container that came up long ago.
        building = provision if peek.state.status == WorkspaceStatus.PROVISIONING else None
        self._update_transcript(turns)
        if live:
            self._update_pane(_render_pane_body(peek))
        elif building is not None:
            self._update_pane(_render_provision_body(building))
        else:
            self._clear_pane()
        # A build in flight counts as live for the container: it gets the brand
        # border and stays visible, the one case where the tab has content but
        # no tmux session behind it.
        self._update_tabs(peek.state.id, live=live or building is not None, has_turns=bool(turns))

    @property
    def body_text(self) -> str:
        """Last rendered body as plain text. Stable seam for tests.

        Concatenates the workspace card, the tickets panel, the transcript
        digest, and the terminal pane so existing tests ("title in
        rail.body_text", "frame-two in rail.body_text") work unchanged
        across the structural split.
        """
        parts = [self._workspace_text]
        if self._tickets_text:
            parts.append(self._tickets_text)
        if self._transcript_text:
            parts.append(self._transcript_text)
        if self._pane_text:
            parts.append(self._pane_text)
        return "\n".join(parts)

    # ─── tab activation (user vs. programmatic) ───────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.id != "peek-tabs":
            return
        pane_id = event.pane.id or ""
        if pane_id in self._auto_switches:
            # Our own default-switch echoing back through the message pump.
            self._auto_switches.discard(pane_id)
            return
        # Any activation the rail didn't initiate is the user's choice —
        # respect it until the selection changes.
        self._user_tab_choice = True

    # ─── private: per-surface diff-guarded updates ────────────────────────

    def _set_workspace(self, card: Static, content: Text | str) -> None:
        plain = content.plain if isinstance(content, Text) else content
        if plain == self._workspace_text:
            return
        self._workspace_text = plain
        card.update(content)

    def _update_tickets(self, refs: list[TicketRef]) -> None:
        """Diff-guarded update of the tickets panel; hidden when `refs` is empty.

        Tickets live here rather than inline in the workspace card because
        that card is one `Static` with no scroll of its own — an unbounded
        ticket count would otherwise push the stats/description/affordance/
        commits blocks off screen (the defect this panel exists to fix).
        `-hidden` on an empty selection keeps a ticketless workspace's rail
        exactly as it was before this panel existed.
        """
        container = self.query_one("#card-tickets", VerticalScroll)
        if not refs:
            container.add_class("-hidden")
            if self._tickets_text:
                self._tickets_text = ""
                self.query_one("#tickets-body", Static).update("")
            return
        container.remove_class("-hidden")
        # One fixed plural noun regardless of count — the trailing number
        # already disambiguates "tickets 1" from "tickets 3", so there's no
        # singular-form branch to keep in sync with the other panel titles.
        container.border_title = f"tickets {len(refs)}"
        content = _render_tickets_panel(refs, dark=self.app.current_theme.dark)
        plain = content.plain
        if plain == self._tickets_text:
            return
        self._tickets_text = plain
        self.query_one("#tickets-body", Static).update(content)

    def _update_transcript(self, turns: tuple[SessionTurn, ...]) -> None:
        card = self.query_one("#card-transcript", Static)
        if not turns:
            if self._transcript_text:
                self._transcript_text = ""
                card.update(self._NO_TRANSCRIPT)
            return
        content = render_transcript_digest(turns, dark=self.app.current_theme.dark)
        plain = content.plain
        if plain == self._transcript_text:
            return
        # Stick to the tail ONLY when the viewer is already there. A busy
        # session's digest changes on every slow tick, and unconditionally
        # scrolling would yank a user who had scrolled up to read older turns
        # back to the bottom on each update. Read the position BEFORE the
        # update — the content swap moves max_scroll_y. After-refresh because the
        # Static's new height isn't laid out yet at update() time (same
        # lesson as the sessions screen).
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        at_tail = scroll.is_vertical_scroll_end
        self._transcript_text = plain
        card.update(content.renderable)
        if at_tail:
            self.call_after_refresh(lambda: scroll.scroll_end(animate=False))

    def _update_pane(self, content: Text) -> None:
        plain = content.plain
        if plain == self._pane_text:
            return
        self._pane_text = plain
        self.query_one("#card-pane", Static).update(content)

    def _clear_pane(self) -> None:
        # No live pane to mirror (paused/offline/orphaned). Clear rather
        # than keep a stale frame: the terminal tab stays reachable when a
        # transcript keeps the container visible, and stale output would
        # read as live.
        if self._pane_text:
            self._pane_text = ""
            self.query_one("#card-pane", Static).update(Text.from_markup("[dim](no output)[/]"))

    def _update_tabs(self, wid: str, *, live: bool, has_turns: bool) -> None:
        """Container visibility, `-live` chrome, and the default-tab policy."""
        if wid != self._tab_wid:
            self._tab_wid = wid
            self._user_tab_choice = False
        tabs = self.query_one("#peek-tabs", TabbedContent)
        if not (live or has_turns):
            # Nothing to preview: no live pane and no recorded transcript.
            tabs.add_class("-hidden")
            tabs.remove_class("-live")
            return
        tabs.remove_class("-hidden")
        tabs.set_class(live, "-live")
        if self._user_tab_choice:
            return
        desired = self._TRANSCRIPT_TAB if has_turns else self._TERMINAL_TAB
        if tabs.active != desired:
            self._auto_switches.add(desired)
            tabs.active = desired

    def _hide_tabs(self) -> None:
        tabs = self.query_one("#peek-tabs", TabbedContent)
        tabs.add_class("-hidden")
        tabs.remove_class("-live")
        self._clear_pane()
        if self._transcript_text:
            self._transcript_text = ""
            self.query_one("#card-transcript", Static).update(self._NO_TRANSCRIPT)
        self._tab_wid = None
        self._user_tab_choice = False


def _render_peek(
    peek: WorkspacePeek, *, dark: bool = True, agent: AgentActivity | None = None
) -> Text:
    """Concatenate workspace + pane into one Text. Test seam preserved.

    Keeps existing pure-render tests (`_render_peek(peek).plain`) working
    after the split — we still produce the same rendered content; the
    rail just lays it across two cards instead of one body.
    """
    text = _render_workspace(peek, dark=dark, agent=agent)
    if peek.state.status in LIVE_STATUSES:
        text.append("\n")
        text.append_text(_render_pane_body(peek))
    return text


def _render_workspace(
    peek: WorkspacePeek,
    *,
    dark: bool = True,
    agent: AgentActivity | None = None,
    provision: ProvisionProgress | None = None,
) -> Text:
    """Workspace card body: stats / agent metrics / description / affordances / commits.

    Pure function — easy to unit-test. `dark` selects the active theme's
    hex palette; widgets pass `app.current_theme.dark` so colors track
    runtime theme switches. The body is composed from the module-level
    block helpers below, in content order — each block is independently
    testable and returns a (possibly empty) ``Text`` fragment.

    ``agent`` is the selected row's primary ``AgentActivity`` (from the
    list screen's activity tick). ``None`` skips the agent line entirely
    and keeps the rendered output byte-identical to the pre-agent card.

    The header (title + status + branch + base) lived here in earlier
    revisions; it moved to the card list once each row became its own
    `WorkspaceCard`. The rail now carries only what the card cannot:
    live git counts, agent session metrics, init-failure log path,
    paused / offline / orphaned affordances with their action keys, and
    recent commits. Associated tickets are rendered in their own panel
    (`_render_tickets_panel`, wired by `PeekRail._update_tickets`), not
    here — an unbounded ticket count would otherwise grow this single
    `Static` past the rail's visible height with no way to scroll it.

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
    """
    s = peek.state
    text = Text()
    text.append_text(_stats_line(peek, dark=dark))
    if agent is not None:
        text.append_text(_agent_line(agent, dark=dark))
    text.append_text(_description_block(s))
    text.append_text(_affordance_block(s, dark=dark, provision=provision))
    text.append_text(_commits_block(peek.recent_commits, dark=dark))
    return text


def _markup(text: Text, line: str) -> None:
    """Append one Rich-markup line + newline (shared by the block helpers).

    `line` is parsed as Rich markup, so any **external** value interpolated
    into it (a git commit subject, a persisted `error_detail`, a filesystem
    path) MUST be passed through `rich.markup.escape` first — otherwise a
    stray `[...]` in that text is read as a style tag and crashes the rail
    at paint time (rich `MissingStyle`, e.g. a subject containing `[mcp]`).
    Only code-controlled chrome (the hex colors, glyphs, labels below) is
    safe to embed raw. Same rule the `_description_block` plain-append note
    states from the other direction.
    """
    text.append_text(Text.from_markup(line))
    text.append("\n")


def _stats_line(peek: WorkspacePeek, *, dark: bool) -> Text:
    """Stats row: diff numstat plus polarity-aware ahead / behind / dirty.

    Each group reads as `<label> <value>` in matching polarity color,
    separated by muted dots so the row scans like a status bar. Colors are
    **polarity-aware**: `ahead`, `behind`, `dirty` render in muted hex
    while their value is zero (no signal) and promote to a semantic color
    (green for ahead, amber for behind / dirty) the moment the value is
    nonzero. The label *and* value share the polarity — pairing them as
    one chunk preserves scan-ability: "is there work to push? something
    to pull? something to clean up?" becomes a glance check, not a
    multi-token reading task.
    """
    add_hex = ref_color("diff_add", dark=dark)
    rem_hex = ref_color("diff_remove", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    # ORPHANED's amber doubles as the "behind / dirty" warning hue —
    # both surfaces describe "work that needs attention" without rising
    # to the destructive-red tier reserved for ERROR / init-failed.
    warn_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
    sep = f"  [{muted_hex}]·[/]  "

    def _stat(label: str, value: int, active_hex: str) -> str:
        """Polarity-aware stat chunk: muted at zero, semantic when nonzero."""
        color = active_hex if value > 0 else muted_hex
        weight = "bold " if value > 0 else ""
        return f"[{color}]{label}[/] [{weight}{color}]{value}[/]"

    diff = (
        f"[bold {add_hex}]+{peek.diff_added}[/]"
        f" [{muted_hex}]/[/]"
        f" [bold {rem_hex}]-{peek.diff_removed}[/]"
    )
    text = Text()
    _markup(
        text,
        f"{diff}"
        f"{sep}{_stat('ahead', peek.base_ahead, add_hex)}"
        f"{sep}{_stat('behind', peek.base_behind, warn_hex)}"
        f"{sep}{_stat('dirty', peek.dirty_files, warn_hex)}",
    )
    return text


def _agent_line(agent: AgentActivity, *, dark: bool) -> Text:
    """Compact agent-metrics row: model · turns/replies/tools · tokens · state.

    Sits directly under the stats line so the pair reads as one status
    block — git facts on row one, session facts on row two. Model takes
    the agent hue (`ref_color('info')`, the same "who" cyan the row card
    uses for the agent name); turns/replies/tools are bold default-fg
    counters (`12t/34r/87⚒` — neutral reference data, same tier as the
    card's ahead/behind counters); tokens are humanized (`412.0k↑ 38.0k↓`,
    via the dashboard's `_human_tokens` — one formatter, two surfaces) and
    muted; the state label takes its agent-state color, mirroring the
    list card's segment. Absent pieces (no model, zero tokens, zero
    in-flight bg subagents) are skipped, not blank-filled.
    """
    muted_hex = chrome_color("muted", dark=dark)
    segments: list[Text] = []
    if agent.model:
        segments.append(Text(agent.model, style=ref_color("info", dark=dark)))
    segments.append(
        Text(
            f"{agent.human_turns}t/{agent.assistant_replies}r/{agent.tool_calls}⚒",
            style="bold",
        )
    )
    if agent.active_subagents:
        # In-flight background subagents — a live signal, so it takes the
        # agent hue rather than the neutral-counter tier; hidden at zero.
        noun = "bg agent" if agent.active_subagents == 1 else "bg agents"
        segments.append(
            Text(
                f"{agent.active_subagents} {noun}",
                style=f"bold {ref_color('info', dark=dark)}",
            )
        )
    if agent.tokens_in or agent.tokens_out:
        segments.append(
            Text(
                f"{_human_tokens(agent.tokens_in)}↑ {_human_tokens(agent.tokens_out)}↓",
                style=muted_hex,
            )
        )
    segments.append(
        Text(
            agent_state_label(agent.state),
            style=f"bold {agent_state_color(agent.state, dark=dark)}",
        )
    )
    text = Text()
    for i, segment in enumerate(segments):
        if i:
            text.append("  ")
            text.append("·", style=muted_hex)
            text.append("  ")
        text.append_text(segment)
    text.append("\n")
    return text


def _render_tickets_panel(refs: list[TicketRef], *, dark: bool) -> Text:
    """Tickets panel body — one line per ref, or nothing when there are none.

    Lives in its own bounded, scrollable panel (`PeekRail._update_tickets`),
    never inline in the summary card: that card is one `Static` with no
    scroll of its own, so an unbounded ticket count used to push the stats,
    description, affordance and commits blocks off screen. Each line
    `no_wrap`s and `overflow="ellipsis"`s instead — a long title or a wide
    fleet's worth of refs crops per-row rather than growing the panel.

    Each line leads with the same compact pill the row card shows
    (``ticket_pill``). An issue ref (``kind == "issue"``, the default)
    keeps the pre-PR treatment verbatim: agent-info cyan, so the reference
    reads as auxiliary metadata. A pull-request ref (``kind ==
    "pull_request"``) instead leads with `PR_GLYPH` and takes
    `pr_status_color` on both the pill AND the ``status`` value — the same
    "PR state is the single most informative token" rule the row card's
    `_append_ticket_segments` applies, carried onto the rail's read-deeply
    surface so the two never disagree about what a PR's color means. Title
    (default fg, bold — the human-readable identity), ``status`` and
    ``assignee`` share the one-line budget with the pill; absent fields are
    skipped, never blank-filled — same convention as the agent line.
    ``url`` is deliberately dropped here: a full URL on every row was most
    of the original bloat, and it isn't clickable in a terminal anyway.
    Empty ``refs`` yields an empty ``Text`` so the panel stays hidden.
    """
    text = Text()
    if not refs:
        return text
    info_hex = ref_color("info", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    for i, ref in enumerate(refs):
        if i:
            text.append("\n")
        is_pr = ref.kind == "pull_request"
        pill_hex = pr_status_color(ref.status, dark=dark) if is_pr else info_hex
        if is_pr:
            text.append(f"{PR_GLYPH} ", style=f"bold {pill_hex}")
        text.append(ticket_pill(ref), style=f"bold {pill_hex}")
        if ref.title:
            text.append("  ")
            text.append(ref.title, style="bold")
        if ref.status:
            text.append("  ")
            text.append("· ", style=muted_hex)
            text.append("status ", style=muted_hex)
            text.append(ref.status, style=f"bold {pill_hex}" if is_pr else "bold")
        if ref.assignee:
            text.append("  ")
            text.append("· ", style=muted_hex)
            text.append("assignee ", style=muted_hex)
            text.append(ref.assignee, style="bold")
    # Cropping is the panel's own `text-wrap: nowrap` / `text-overflow`
    # style, not an attribute set here — see the comment on that rule.
    return text


def _description_block(s: WorkspaceState) -> Text:
    """User-supplied description (optional). Default fg, no special color
    — it's a free-form note, not a status signal. Trimmed at
    ``_DESCRIPTION_TRIM`` with an ellipsis so a long paste doesn't hijack
    the rail's vertical budget. Skipped entirely when empty so we don't
    ship a "(no description)" placeholder on every workspace.
    """
    text = Text()
    if not s.description:
        return text
    text.append("\n")
    desc = (
        s.description
        if len(s.description) <= _DESCRIPTION_TRIM
        else s.description[: _DESCRIPTION_TRIM - 1] + "…"
    )
    # Plain Text.append (not markup) so any `[...]` in the user's
    # description is treated as literal characters, not Rich markup.
    text.append(desc)
    text.append("\n")
    return text


def _affordance_block(
    s: WorkspaceState, *, dark: bool, provision: ProvisionProgress | None = None
) -> Text:
    """Init-failure badge + the lifecycle affordance lines, at most a few.

    Conditional, state-driven blocks: init failure surfaces a log path the
    user can follow; PAUSED / OFFLINE / ORPHANED each name the one key
    that recovers them; ERROR surfaces the persisted detail; PROVISIONING
    names the only "affordance" that is not a key — waiting — and hands over
    the two facts that make waiting bearable (how long, and what it just did).
    """
    muted_hex = chrome_color("muted", dark=dark)
    text = Text()

    # Runtime fallback: a PERSISTENT warning naming the reason + the
    # `grove respawn` remedy — never a create-time-only stderr line nobody
    # scrolls back to. ORPHANED's amber, same reasoning as the card badge.
    if s.runtime_fallback_reason:
        warn_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        text.append("\n")
        _markup(
            text,
            f"[bold {warn_hex}]⚠ container fallback:[/] {escape(s.runtime_fallback_reason)}  "
            f"[{muted_hex}]— fix the runtime, then respawn to promote[/]",
        )
    elif s.runtime_default_config:
        # Notice, not a warning — still containerized, just on the default image.
        _markup(
            text,
            f"\n[{muted_hex}]ⓘ default container — "
            "`grove init devcontainer` to graduate to a committed config[/]",
        )

    # No in-container tmux: independent of the two above — a container
    # can come up fine (on either config) and still ship no tmux to hold the
    # agent past a client detach. A PERSISTENT warning for the same reason as
    # the fallback mark.
    if s.runtime_no_tmux:
        warn_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        text.append("\n")
        _markup(
            text,
            f"[bold {warn_hex}]⚠ no in-container tmux:[/] agent dies with your "
            f"terminal  [{muted_hex}]— install tmux in the image, or Grove has "
            "no bundle for this architecture[/]",
        )

    # Init failure surfaces a path the user can follow.
    if s.init_status == InitStatus.FAILED:
        fail_hex = init_status_color(InitStatus.FAILED, dark=dark)
        text.append("\n")
        _markup(text, f"[bold {fail_hex}]✗ init failed[/]")
        if s.init_log_path:
            _markup(text, f"[{muted_hex}]log:[/] {escape(s.init_log_path)}")

    # Provisioning affordance — the one whose answer is "do nothing". It takes
    # the status's own info hue rather than the amber the warnings above use:
    # this is not something going wrong, and painting a normal build amber is
    # how a user learns to treat the colour as noise. The elapsed time answers
    # "should I worry", the headline answers "is it moving" (a timer alone
    # cannot), and the log path is for the user who wants the whole build.
    if s.status == WorkspaceStatus.PROVISIONING:
        prov_hex = status_color(WorkspaceStatus.PROVISIONING, dark=dark)
        waited = (
            ""
            if provision is None or provision.elapsed_ms is None
            else f" [bold {prov_hex}]{format_wait(provision.elapsed_ms // 1000)}[/]"
        )
        text.append("\n")
        _markup(
            text,
            f"[bold {prov_hex}]◍ building the container[/]{waited}  "
            f"[{muted_hex}]— it comes up on its own; nothing to press[/]",
        )
        if provision is not None and provision.headline:
            _markup(text, f"[{muted_hex}]last:[/] {escape(provision.headline)}")
        if s.provision_log_path:
            _markup(text, f"[{muted_hex}]log:[/] {escape(s.provision_log_path)}")

    # Paused affordance — the worktree is gone; tell the user how to bring it back.
    # Coloured with the (neutral gray) paused token, not amber: pause is
    # deliberate, not a warning.
    if s.status == WorkspaceStatus.PAUSED:
        paused_hex = status_color(WorkspaceStatus.PAUSED, dark=dark)
        text.append("\n")
        _markup(
            text,
            f"[{paused_hex}]‖ paused[/]  [{muted_hex}]press[/]"
            f" [bold {paused_hex}]R[/] [{muted_hex}]to resume[/]",
        )

    # Offline affordance — tmux session vanished but worktree is intact.
    # Different from pause: respawn doesn't recreate the worktree.
    if s.status == WorkspaceStatus.OFFLINE:
        offline_hex = status_color(WorkspaceStatus.OFFLINE, dark=dark)
        text.append("\n")
        _markup(
            text,
            f"[{offline_hex}]○ offline[/]  [{muted_hex}]press[/]"
            f" [bold {offline_hex}]o[/] [{muted_hex}]to respawn[/]",
        )

    # Orphaned: worktree directory is gone (user deleted it externally, or
    # disk failed). Cannot recover automatically; the only safe action is
    # to clean up the stranded record via kill.
    if s.status == WorkspaceStatus.ORPHANED:
        orphaned_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        text.append("\n")
        _markup(
            text,
            f"[bold {orphaned_hex}]⊘ worktree missing on disk[/]  "
            f"[{muted_hex}]press[/] [bold {orphaned_hex}]k[/] [{muted_hex}]to clean up[/]",
        )

    # Error: persisted error_detail tells the user what went wrong.
    if s.status == WorkspaceStatus.ERROR and s.error_detail:
        text.append("\n")
        _markup(text, f"[{muted_hex}]error:[/] {escape(s.error_detail)}")

    return text


def _commits_block(commits: tuple[CommitSummary, ...], *, dark: bool) -> Text:
    """Recent commits (newest first), or nothing when there are none.

    Section heading takes the branch accent (teal) so the title and the
    SHA column below share the same color — the eye groups them as one
    unit. Subject stays default fg, age muted.
    """
    text = Text()
    if not commits:
        return text
    branch_hex = ref_color("branch", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    text.append("\n")
    _markup(text, f"[bold {branch_hex}]recent[/]")
    now = datetime.now(tz=UTC)
    for c in commits:
        ago = humanize.naturaltime(now - c.committed_at)
        subject = (
            c.subject if len(c.subject) <= _SUBJECT_TRIM else c.subject[: _SUBJECT_TRIM - 1] + "…"
        )
        _markup(
            text,
            f"  [bold {branch_hex}]{c.sha[:8]:>8}[/]  {escape(subject)}  [{muted_hex}]{ago}[/]",
        )
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


def _render_provision_body(progress: ProvisionProgress) -> Text:
    """Terminal-tab body while the container is being built.

    Same shape as `_render_pane_body` and deliberately so — one `Static`, a
    tail of lines, `no_wrap` cropping — because it is the same job with a
    different producer: before the agent's tmux pane exists, the provisioner's
    stdout is what "the terminal" means for this workspace. The lines are the
    devcontainer CLI's and BuildKit's own output, appended verbatim (never
    markup — a build log is full of brackets) and never parsed into steps or a
    percentage: that format is somebody else's and has no contract.

    The rail tail-slices to its own viewport height rather than trusting the
    producer's cap, exactly as the pane body does.
    """
    lines = progress.lines[-_PANE_TAIL_LINES:]
    text = Text("\n".join(lines)) if lines else Text.from_markup("[dim](build starting…)[/]")
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
