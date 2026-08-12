"""WorkspaceCard — one row in the workspace list, rendered as a card.

Two lines of content per card, framed by a `round` border:

* line 1 — colored status glyph + bold-underlined title + muted age
* line 2 — the runtime mark (a bare square glyph, `■` host / `▣`
  container, always present), then branch (bold $ref teal) + agent
  (bold $ref-info cyan), an
  optional agent-activity segment (`<glyph> <label>`, bold + agent-state
  color — pushed by the list screen's slow tick, absent by default), an
  optional task-phase segment (`<glyph> <label> N/M`, bold + phase color
  — pushed the same way, a THIRD axis orthogonal to both: how far through
  the task the agent says it is, not what it's doing right now) + status
  label (bold + status color), any associated issue pills (bold $ref-info
  cyan — the ticket's own `TicketRef.kind == "issue"`, the default), then
  any associated pull-request pill(s) (`⇒ <pill> <status>`, bold +
  `pr_status_color` — `kind == "pull_request"`; issues are the INPUT, a PR
  is the OUTCOME, and the `⇒` glyph is what signals that direction without
  a second row — see `_render_card`'s docstring for the full rationale), an
  optional muted `root` tag when the workspace runs in the repo root
  (placement ROOT), then an optional bold-error `init failed` badge when
  init_status is FAILED.

Focus is carried by **TCSS chrome**, not glyphs in the rendered text:

* every card carries `border: round $surface` by default. `$surface`
  matches the parent list's background, so the border is read as
  transparent breathing room — *every* card has the same outer shape.
* `WorkspaceList:focus > WorkspaceCard.-highlight` swaps the border to
  `round $primary` (clay) so the focused row becomes a fully framed
  panel — same lazygit-style "active panel keeps the brand color" cue,
  applied per-row instead of per-panel.
* the highlighted card's background also swaps to `$panel` (configured
  on the parent `WorkspaceList`) so the focus cue isn't carried by color
  alone (a11y).

Pure render lives in `_render_card`; the `WorkspaceCard(ListItem)` widget
is a thin Static-wrapping shell that re-renders when its `state` reactive
changes. We do NOT re-render on `highlighted` — the visual change is
purely CSS (border swap + bg swap), so the body Text stays identical
regardless of focus and skipping the watcher avoids a per-cursor-move
repaint.

Card height is fixed at 4 rows (1 border-top + 2 content + 1 border-bottom).
Fixed height keeps scrolling jitter-free and means highlighting never
reflows the layout — only the two border colors swap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import humanize
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import ListItem, Static

from grove.core import InitStatus, WorkspaceState, WorkspaceStatus
from grove.core.agents import AgentActivityState
from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.workspace import Placement
from grove.tui._status import (
    BLOCKED_GLYPH,
    PR_GLYPH,
    active_pulse,
    agent_state_color,
    agent_state_glyph,
    agent_state_label,
    blocked_color,
    chrome_color,
    init_status_color,
    phase_color,
    phase_glyph,
    phase_label,
    pr_status_color,
    ref_color,
    runtime_color,
    runtime_glyph,
    status_color,
    status_glyph,
    status_label,
)

# Title is trimmed if it exceeds this — keeps line 1 from wrapping on
# narrow terminals. Matches the trim PeekRail uses for commit subjects.
_TITLE_TRIM = 48

# Per-provider compact-pill prefix. Linear ids already carry their team
# prefix (ENG-123) so they read as-is; GitHub / Gitea issues are bare
# numbers and need a provider tag to disambiguate. Defined once here so the
# card row and the peek-rail detail render the exact same pill text (DRY).
_TICKET_PILL_PREFIX: dict[str, str] = {"github": "GH#", "gitea": "GTEA#"}


def ticket_pill(ref: TicketRef) -> str:
    """Compact per-provider pill text for one ticket — the shared formatter.

    Linear → the id verbatim (``ENG-123``); GitHub → ``GH#<id>``; Gitea →
    ``GTEA#<id>``. An ``ambiguous`` ref (a branch-inferred match that more
    than one provider/key claimed) gets a trailing ``?`` so it reads as
    tentative. Both the card row and the peek-rail detail call this so the
    two surfaces can never drift in how a ticket reads.
    """
    text = f"{_TICKET_PILL_PREFIX.get(ref.provider, '')}{ref.id}"
    return f"{text}?" if ref.ambiguous else text


class WorkspaceCard(ListItem):
    """One workspace as a ListItem; body is a single Static rendered via Rich Text.

    The card never holds focusable children (no Input, no Button) because
    Textual's focus chain will steal the cursor from the parent ListView
    if a child becomes focused — see CLAUDE.md note on FilterBar focus.

    Focus chrome is TCSS-only: every card has `border: round $surface`
    (read as transparent against the list bg). When the parent
    `WorkspaceList:focus` AND this card has the `-highlight` class, the
    border swaps to `round $primary` (clay). The selector lives on
    `WorkspaceList` so the rule short-circuits when the list isn't
    focused (e.g. while the FilterBar input has focus, no card pretends
    to be the active selection).
    """

    DEFAULT_CSS = """
    WorkspaceCard {
        height: 4;
        padding: 0 1;
        border: round $surface;
    }
    /* Mouse hover preview — subtle $secondary outline so the user
     * sees mouse position even when a different row is keyboard-
     * highlighted. Textual's default ListItem :hover uses $boost,
     * which is always transparent on Grove themes (CLAUDE.md $boost
     * lesson) — so explicit hover chrome is required for any visible
     * feedback. The list-scoped highlight rule
     * `WorkspaceList:focus > WorkspaceCard.-highlight` is more
     * specific and out-ranks this, so hovering the selected row keeps
     * its clay chrome rather than degrading to gray. */
    WorkspaceCard:hover {
        border: round $secondary;
    }
    """

    state: reactive[WorkspaceState | None] = reactive(None, layout=False)
    # Pulse clock pushed by the parent screen at 4 Hz. Default 0 = resting
    # frame (filled ●, full active green) so cards mounted between ticks
    # — or in tests with no clock at all — render the canonical static
    # appearance. Watcher is gated on ACTIVE so non-live cards skip the
    # repaint entirely. layout=False because the glyph swap never reflows.
    pulse_frame: reactive[int] = reactive(0, layout=False)

    def __init__(self, state: WorkspaceState) -> None:
        super().__init__()
        # Stash on the instance so the body refresh after mount has access
        # without going through the reactive (which fires the watcher and
        # would double-render before mount completes).
        self._state = state
        # Agent-activity axis (what the session is *doing*), pushed by the
        # parent screen's slow stats tick via `set_agent_state`. A plain
        # attribute, NOT a reactive: the slow tick is the only writer, so a
        # watcher buys nothing — `_refresh_body`'s plain-text diff guard
        # already absorbs the no-op pushes. None (the default) renders no
        # agent-state segment at all.
        self._agent_state: AgentActivityState | None = None
        # Task-phase axis (how far through the task the agent SAYS it is) —
        # orthogonal to `_agent_state` (what it's doing right now). Same shape
        # as `_agent_state`: a plain attribute pushed by the parent screen's
        # slow tick, not a reactive, for the identical reason (the tick is the
        # only writer; the diff guard absorbs no-op pushes). `None` (no phase
        # ever reported) renders no phase segment at all.
        self._phase: PhaseReport | None = None
        self._last_plain: str = ""

    def compose(self) -> ComposeResult:
        yield Static(self._initial_body(), id="card-body")

    def on_mount(self) -> None:
        # Reactive set drives the watcher chain so subsequent populate()
        # calls in the parent list refresh the body identically to mount.
        self.state = self._state

    def watch_state(self, new_state: WorkspaceState | None) -> None:
        if new_state is None:
            return
        self._state = new_state
        self._refresh_body()

    def watch_pulse_frame(self, _frame: int) -> None:
        # ACTIVE is the only status that reads the pulse — every other row
        # renders identical bytes per frame and the existing diff guard
        # would short-circuit the repaint anyway. Gating here saves the
        # render call itself for the common case of an idle/paused fleet.
        if self._state is not None and self._state.status == WorkspaceStatus.ACTIVE:
            self._refresh_body()

    def set_agent_state(self, state: AgentActivityState | None) -> None:
        """Set (or clear, with ``None``) the agent-activity state segment.

        Pushed by the list screen's slow stats tick (~3 s) for visible rows.
        The diff guard in ``_refresh_body`` short-circuits when nothing
        visible changed, so re-pushing the same state per tick is cheap.
        """
        self._agent_state = state
        self._refresh_body()

    def set_phase(self, phase: PhaseReport | None) -> None:
        """Set (or clear, with ``None``) the task-phase segment.

        Pushed by the list screen's slow stats tick, mirroring
        ``set_agent_state`` — same injection shape, third axis. ``None``
        (the default, and what every pre-phase workspace has) renders
        nothing: absence is the default, same convention as the agent-state
        segment and the ``root`` tag.
        """
        self._phase = phase
        self._refresh_body()

    @property
    def workspace_id(self) -> str:
        return self._state.id

    @property
    def body_text(self) -> str:
        """Plain text of the rendered card body. Stable seam for tests."""
        return self._last_plain

    # ─── private ──────────────────────────────────────────────────────────

    def _initial_body(self) -> Text:
        text = _render_card(
            self._state,
            dark=self._dark(),
            pulse_frame=self.pulse_frame,
            agent_state=self._agent_state,
            phase=self._phase,
        )
        self._last_plain = text.plain
        return text

    def _refresh_body(self) -> None:
        try:
            body = self.query_one("#card-body", Static)
        except Exception:
            return  # not mounted yet
        text = _render_card(
            self._state,
            dark=self._dark(),
            pulse_frame=self.pulse_frame,
            agent_state=self._agent_state,
            phase=self._phase,
        )
        # Diff guard on plain text alone misses the pulse: frame 0 and frame
        # 1 of an ACTIVE card share `●…active`'s plain form (only the glyph
        # and a span color differ, and `◉` ≠ `●` so plain DOES change). For
        # non-ACTIVE rows the bytes are identical across frames and the
        # short-circuit fires as intended.
        if text.plain == self._last_plain:
            return
        self._last_plain = text.plain
        body.update(text)

    def _dark(self) -> bool:
        # `app` is None during pure-construction tests; fall back to dark
        # which matches the default registered theme.
        try:
            return bool(self.app.current_theme.dark)
        except Exception:
            return True


def _append_phase_segment(
    text: Text, phase: PhaseReport | None, *, muted_hex: str, dark: bool
) -> None:
    """Append the `· <glyph> <label> N/M [‼]` task-phase segment, or nothing.

    `N/M` (1-based) rides alongside the glyph/color ramp so progress reads as
    a number too, not just a hue — cheap given `PhaseReport.index` is already
    computed. `phase=None` (no report yet) is the default and appends nothing.

    `blocked` is a FLAG beside the phase, not a replacement for it: appended
    as a trailing `‼` in its own hue (`blocked_color`) so the reader still sees
    *where it stopped* (the phase glyph/label/progress, untouched) alongside
    *that it stopped* — collapsing the two into one badge would lose the first
    fact. Absent by default, same convention as the phase segment itself.
    """
    if phase is None:
        return
    text.append("  ")
    text.append("· ", style=muted_hex)
    progress = f"{phase.index + 1}/{len(PHASE_ORDER)}"
    text.append(
        f"{phase_glyph(phase.phase)} {phase_label(phase.phase)} {progress}",
        style=f"bold {phase_color(phase.phase, dark=dark)}",
    )
    if phase.blocked:
        text.append(f" {BLOCKED_GLYPH}", style=f"bold {blocked_color(dark=dark)}")


def _append_runtime_warning_segments(
    text: Text, state: WorkspaceState, *, muted_hex: str, dark: bool
) -> None:
    """Append the runtime-degradation badges, or nothing.

    Both reuse the ORPHANED amber (the established "warning, not error" hue) —
    never init_status's red, which means something different (init actually
    failed, not "runtime downgraded but the workspace is otherwise fine").
    Mutually exclusive by construction (`runtime_no_tmux` is a CONTAINER-mode
    fact, `runtime_fallback_reason` only ever fires on a HOST workspace), but
    rendered as two independent checks rather than `elif` — nothing enforces
    the exclusion here, and a future arm that violates it should show both
    badges rather than silently drop one.

    Runtime fallback: a PERSISTENT warning — a workspace that degraded
    from container to host had its isolation contract voided, so this marker
    must be visible on the list, not just in the detail pane.

    No in-container tmux: a container that came up fine but shipped no
    tmux binary and has no Grove-bundled fallback for its architecture — the
    agent dies with the client that launched it, invisibly, until this badge.
    """
    if state.runtime_fallback_reason:
        warn_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("⚠ container fallback", style=f"bold {warn_hex}")
    if state.runtime_no_tmux:
        warn_hex = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("⚠ no in-container tmux", style=f"bold {warn_hex}")


def _append_ticket_segments(
    text: Text, refs: list[TicketRef], *, muted_hex: str, dark: bool
) -> None:
    """Append issue pills, then any pull-request pill(s), or nothing.

    Issues are the INPUT (what the workspace was asked to do); a PR is the
    OUTCOME (what it produced) — usually several issues resolve to ONE PR.
    Rendering both as an undifferentiated pill list would flatten that
    relationship, so the two kinds get different treatment even though they
    share one wire list (`TicketRef.kind`, default `"issue"`):

    * **issues** — unchanged from the pre-PR render: one `· <pill>` each,
      bold `ref_color('info')` (cyan, the established "auxiliary metadata"
      slot). A workspace with only issues (or none) renders byte-identical
      to before this segment existed.
    * **pull requests** — one `⇒ <pill> <status>` each. `⇒` (not `· `) is
      the connector: a plain dot separator would read as "one more item in
      the same list", but the arrow reads as "leads to", naming the
      issue→PR relationship without a second row. The WHOLE segment —
      glyph, pill, and status word — takes `pr_status_color(ref.status)`:
      a PR's state (open / merged / closed) is the single most informative
      token on the card once one exists, so it gets the loudest treatment
      here, not just the status word. `ref.status` is omitted (not
      blank-filled) when unset — same absence convention as every other
      optional piece on this line.

    Both lists render in `refs`' original order via one pass; empty `refs`
    (or a `refs` with neither kind present) appends nothing.
    """
    ticket_hex = ref_color("info", dark=dark)
    for ref in refs:
        if ref.kind == "pull_request":
            continue
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append(ticket_pill(ref), style=f"bold {ticket_hex}")
    for ref in refs:
        if ref.kind != "pull_request":
            continue
        pr_hex = pr_status_color(ref.status, dark=dark)
        text.append("  ")
        text.append(f"{PR_GLYPH} ", style=f"bold {pr_hex}")
        text.append(ticket_pill(ref), style=f"bold {pr_hex}")
        if ref.status:
            text.append(" ")
            text.append(ref.status, style=f"bold {pr_hex}")


def format_wait(seconds: int) -> str:
    """Compact elapsed label for a wait the user is actively enduring.

    Not `humanize.naturaltime` (which the card uses for age): "2 minutes ago"
    is the wrong register for a clock the user is watching tick, and it rounds
    a 49-second wait to "a minute". Sub-minute waits keep their seconds because
    that is the whole warm-start range; past an hour the seconds stop mattering
    and the minutes are the story.

    Shared with the peek rail — same reason `ticket_pill` lives here — so the
    fleet row and the detail card can never disagree about how long it has been.
    """
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def provision_wait(state: WorkspaceState, *, now: datetime | None = None) -> str | None:
    """How long this workspace's in-flight provision has been running, or None.

    Derived from the persisted start stamp rather than read through
    ``ProvisionProgress``, which tails the provisioner's log: this runs on the
    render path for every visible row, and a per-row file read per tick is
    exactly the cost the rail's single-selection read is bounded to avoid.
    ``None`` for a record with no (or an unparseable) stamp — an absent number
    is honest where a zero would read as "it just started".
    """
    if not state.provision_started_at:
        return None
    try:
        started = datetime.fromisoformat(state.provision_started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return format_wait(max(0, int(((now or datetime.now(tz=UTC)) - started).total_seconds())))


def _render_card(
    state: WorkspaceState,
    *,
    dark: bool,
    now: datetime | None = None,
    pulse_frame: int = 0,
    agent_state: AgentActivityState | None = None,
    phase: PhaseReport | None = None,
) -> Text:
    """Render one workspace row as Rich `Text`.

    Pure: identical for highlighted and unhighlighted cards. Focus is
    indicated entirely by CSS (full-border swap + bg swap on the parent
    `WorkspaceList:focus > .-highlight` rule), so this helper has no
    `focused` argument. Adding one would re-introduce the dual-source-of-
    truth we just retired (and force a repaint per cursor move).

    Color language inside the card body — every visible element earns its
    own slot from `grove.tui.theme`, never literal hex:

    * **status glyph** (line 1) — `status_color`, bold. The first thing
      the eye lands on; encodes lifecycle state at a glance.
    * **title** — bold + underline (default fg). Underlines mark this as
      the row's identity; same affordance as a hyperlink in IDE files
      lists, which trains the user's "this is the thing" reflex.
    * **age** — `chrome_color('muted')`. Recedes; only matters on scan.
    * **runtime mark** (line 2, leading) — bold + `runtime_color`. `■`
      host / `▣` container, the isolation boundary. The ONE axis with no
      absent state: both runtimes are marked, deliberately reversing the
      silence-is-the-signal rule `Placement`'s `root` tag follows, because
      "unmarked" must not be readable as "the mark failed to render" for
      the fact that says what the agent can touch.
    * **branch** (line 2) — bold + `ref_color('branch')` (teal). Primary
      anchor when scanning a list of workspaces by feature/task.
    * **agent** — bold + `ref_color('info')` (cyan). Distinct hue from
      branch so the eye separates "what" (branch) from "who" (agent)
      without re-reading the labels.
    * **agent state** (optional) — bold + `agent_state_color`. The agent
      axis ("what the session is doing"), rendered only when the screen's
      activity tick has resolved a session; `None` renders nothing.
    * **task phase** (optional) — bold + `phase_color`. A THIRD axis,
      orthogonal to both status and agent state: how far through the task
      the agent SAYS it is (reported, not derived — see `grove.core.phase`).
      `None` (no phase ever reported) renders nothing — same absence
      convention as agent state and the `root` tag. A blocked claim appends
      a trailing `‼` in `blocked_color` beside (never instead of) the phase
      — see `_append_phase_segment`.
    * **status label** — bold + `status_color`. Matches the line-1 glyph
      so the same color reads twice — reinforces the lifecycle cue. A
      PROVISIONING row appends its elapsed wait in the same hue (`◍
      provisioning 2m14s`): it is the only status whose remedy is to wait,
      and a wait without a clock is indistinguishable from a hang.
    * **issue pills** (optional) — bold + `ref_color('info')` (cyan), one
      per `TicketRef` with `kind == "issue"` (the default). The workspace's
      INPUT; unchanged from the pre-PR render.
    * **pull-request pill(s)** (optional) — bold + `pr_status_color`, one
      per `TicketRef` with `kind == "pull_request"`, led by `PR_GLYPH`
      (`⇒`) instead of the usual `· ` dot so the segment reads as "leads
      to" rather than "one more list item" — the workspace's OUTCOME.
      Color carries the PR's real state (open → live lime, merged → settled
      muted gray, closed → destructive red — see `_status.pr_status_color`);
      no separate color for the trailing status WORD, so the one hue is the
      single loudest signal on the row once a PR exists. See
      `_append_ticket_segments` for the full issue-vs-PR rationale.
    * **init failed** — bold + `init_status_color(FAILED)`. Bold red so
      a broken init reads as the most urgent thing on the row.
    * **`·` separators** — muted; recede so groups read as groups.

    `now` defaults to `datetime.now(tz=UTC)` and exists for testability —
    callers in tests pass a fixed `now` so age formatting is stable.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    # ACTIVE rows pulse: a screen-level clock pushes `pulse_frame` and the
    # leading glyph + its color swap between the resting state (●, full
    # active green) and the swelled state (◉, mint-tinted). All other
    # statuses ignore the pulse — `active_pulse` is a no-op for them.
    if state.status == WorkspaceStatus.ACTIVE:
        glyph, s_color = active_pulse(pulse_frame, dark=dark)
    else:
        glyph = status_glyph(state.status)
        s_color = status_color(state.status, dark=dark)
    branch_hex = ref_color("branch", dark=dark)
    agent_hex = ref_color("info", dark=dark)
    muted_hex = chrome_color("muted", dark=dark)
    fail_hex = init_status_color(InitStatus.FAILED, dark=dark)

    # no_wrap: the card's `height: 4` leaves exactly two content rows; a line 2
    # that soft-wraps (long branch + agent + the agent-state segment on a narrow
    # terminal) would push into a phantom third row the border clips. Crop with
    # an ellipsis instead — the same trade the peek rail makes for pane width.
    text = Text(no_wrap=True, overflow="ellipsis")

    # Line 1: status glyph (colored) + underlined title + muted "·" + muted age.
    text.append(f"{glyph} ", style=f"bold {s_color}")
    title = state.title if len(state.title) <= _TITLE_TRIM else state.title[: _TITLE_TRIM - 1] + "…"
    text.append(title, style="bold underline")
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(humanize.naturaltime(now - state.updated_at), style=muted_hex)
    text.append("\n")

    # Line 2 opens with the runtime mark, exactly as line 1 opens with the
    # status glyph: a bare colored glyph, no `· ` connector, because it is a
    # lead-in rather than one more item in the line's list. Leading is
    # load-bearing, not cosmetic — line 2 crops with an ellipsis on a narrow
    # terminal, so a mark placed among the trailing qualifiers would be the
    # first thing to vanish on exactly the workspaces someone is squinting at.
    # Both runtimes are marked; see `_status.runtime_glyph` for why this axis
    # reverses `Placement`'s silence-is-the-signal rule.
    text.append(
        f"{runtime_glyph(state.runtime)} ",
        style=f"bold {runtime_color(state.runtime, dark=dark)}",
    )
    # Then branch · agent · status. Each token gets its own semantic
    # color so the row reads as three separate facts in three separate
    # tiers, rather than a wall of identical-weight tokens.
    text.append(state.branch, style=f"bold {branch_hex}")
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(state.agent_name, style=f"bold {agent_hex}")
    # Agent-activity segment: what the session is *doing* (WORKING / WAITING /
    # BLOCKED / …) — a separate axis from the workspace lifecycle status, so
    # it earns its own glyph + hue right after the "who" (agent name). Bold +
    # agent-state color: same typography tier as the status label. None (no
    # session, or the activity tick hasn't run yet) renders nothing — absence
    # is the default, same convention as the `root` tag below.
    if agent_state is not None:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append(
            f"{agent_state_glyph(agent_state)} {agent_state_label(agent_state)}",
            style=f"bold {agent_state_color(agent_state, dark=dark)}",
        )
    # Task-phase segment: how far through the task the agent SAYS it is — a
    # third axis, orthogonal to both the agent-activity segment above (what
    # it's doing right now) and the status label below (workspace lifecycle).
    # Right after "who/what they're doing" so the agent-facing cluster reads
    # as one chunk before the lifecycle fact. `None` (no report yet) renders
    # nothing — absence is the default, same convention as the agent-state
    # segment. Factored into a helper (not inlined like its sibling above)
    # purely to keep `_render_card`'s statement count under the lint cap.
    _append_phase_segment(text, phase, muted_hex=muted_hex, dark=dark)
    text.append("  ")
    text.append("· ", style=muted_hex)
    text.append(status_label(state.status), style=f"bold {s_color}")
    # A provisioning row carries how long it has been provisioning, in the
    # status segment's own hue so label and clock read as one fact. It is the
    # only status whose remedy is "wait", and a wait with no clock is
    # indistinguishable from a hang — the elapsed time is what turns "nothing
    # is happening" into "it has been 40 seconds, which is normal". Every other
    # status renders nothing here (absence is the default, as everywhere else).
    if state.status == WorkspaceStatus.PROVISIONING:
        waited = provision_wait(state, now=now)
        if waited is not None:
            text.append(f" {waited}", style=f"bold {s_color}")
    _append_ticket_segments(text, state.ticket_refs, muted_hex=muted_hex, dark=dark)
    # Root tag: a muted "root" marks a workspace that runs in the repo root
    # (no dedicated worktree). Muted + lowercase keeps it as a quiet
    # qualifier — the lifecycle status stays the line's loudest token, the
    # tag just tells the user this one has no isolated worktree. Worktree
    # workspaces render nothing here, so the absence is the default.
    if state.placement is Placement.ROOT:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("root", style=muted_hex)
    if state.init_status == InitStatus.FAILED:
        text.append("  ")
        text.append("· ", style=muted_hex)
        text.append("! init failed", style=f"bold {fail_hex}")
    # Runtime warning badges (fallback + no-in-container-tmux): factored into a
    # helper for the same reason as `_append_phase_segment` — keep
    # `_render_card`'s statement count under the lint cap.
    _append_runtime_warning_segments(text, state, muted_hex=muted_hex, dark=dark)

    # No trailing newline — the card's `height: 4` is exactly
    # `border-top + 2 content rows + border-bottom`. A trailing `\n`
    # would push line 2 into a phantom third content row that the
    # border would clip.
    return text
