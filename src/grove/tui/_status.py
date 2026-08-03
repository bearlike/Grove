"""Glyph + color accessors for workspace state in the TUI.

Both the table and the peek rail render `WorkspaceStatus`, init outcomes,
and ref accents identically — keeping the maps duplicated invites them to
drift. One module, thin lookup functions, a single source of hex truth
(`grove.tui.theme`). (CLAUDE.md: define logic once; call everywhere.)

Theme-awareness is by `dark: bool` argument rather than a runtime app
lookup so that pure rendering helpers stay testable without a Pilot.
Callers in widgets resolve `dark` from `app.current_theme.dark` once per
render and forward it.
"""

from __future__ import annotations

from typing import Final

from grove.core import InitStatus, WorkspaceStatus
from grove.core.agents import AgentActivityState
from grove.core.contracts.runtime_palette import RUNTIME_GLYPH, RUNTIME_LABEL
from grove.core.phase import TaskPhase
from grove.core.workspace import Runtime
from grove.tui.theme import (
    ACTIVE_PULSE_TINT_HEX,
    AGENT_STATE_HEX,
    CHROME_HEX,
    INIT_STATUS_HEX,
    PHASE_HEX,
    REF_HEX,
    RUNTIME_HEX,
    STATUS_HEX,
    ChromeKind,
    RefKind,
)

STATUS_GLYPH: dict[WorkspaceStatus, str] = {
    # Persisted intents — RUNNING is rare in user-facing renders (reconciler
    # promotes it) but mapped so a debug renderer doesn't fall through to '?'.
    WorkspaceStatus.RUNNING: "●",
    WorkspaceStatus.PAUSED: "‖",
    WorkspaceStatus.ERROR: "✗",
    # Computed (what the user actually sees in the table / rail).
    WorkspaceStatus.ACTIVE: "●",  # filled — live signal
    WorkspaceStatus.IDLE: "◐",  # half — alive but quiet
    WorkspaceStatus.OFFLINE: "○",  # empty — no live signal
    WorkspaceStatus.ORPHANED: "⊘",  # circle-slash — stranded record
    # Circle with a vertical fill (U+25CD) — a circle mid-way through being
    # filled in, which is what a provision is. It stays in the status axis's
    # circle family (so it can't be mistaken for the agent, phase, runtime or
    # PR axes) while being free of every one of them AND of the status bar's
    # chrome. It is deliberately a FILLED mark: a terminal missing a glyph
    # draws a hollow box, so a hollow choice is indistinguishable from tofu —
    # `fc-list ":charset=25cd"` confirms the mono families this UI runs in
    # (Noto Sans Mono, MesloLGS NF) cover it. The hourglass `⧗` reads better
    # semantically but only serif/fallback families carry it, and `⌛` is
    # double-width, which would shift line 1 by a column.
    WorkspaceStatus.PROVISIONING: "◍",
}

# User-facing label per status. Mostly redundant with `status.value`, but
# pins what the table/rail render so a future enum rename doesn't change the
# UI silently. Also normalizes the rare RUNNING (intent) leak to "active".
STATUS_LABEL: dict[WorkspaceStatus, str] = {
    WorkspaceStatus.RUNNING: "active",
    WorkspaceStatus.ACTIVE: "active",
    WorkspaceStatus.IDLE: "idle",
    WorkspaceStatus.OFFLINE: "offline",
    WorkspaceStatus.PAUSED: "paused",
    WorkspaceStatus.ORPHANED: "orphaned",
    WorkspaceStatus.ERROR: "error",
    # A present participle, unlike every other label: the others name a
    # settled condition the user acts on, this one names an action in flight
    # that they wait out. The word doing that work is the whole point of the
    # status — "offline" is what it used to read as, and that word is what
    # sent people to a destructive verb.
    WorkspaceStatus.PROVISIONING: "provisioning",
}


def status_glyph(status: WorkspaceStatus) -> str:
    """Return the one-char glyph for `status`. Unknown → '?'."""
    return STATUS_GLYPH.get(status, "?")


# Two-frame pulse for the ACTIVE live signal. Frame 0 is the resting state
# (filled disc, full active green — same as the static lookup, single
# source of truth). Frame 1 is the swelled state (ringed disc, mint-tinted).
# A screen-level clock alternates them at 4 Hz so the eye reads "live"
# without losing the row's identity. Other statuses are intentionally NOT
# animated — IDLE means "alive but quiet" and a pulsing IDLE would
# contradict the semantic.
ACTIVE_PULSE_FRAMES: Final[int] = 2
_ACTIVE_PULSE_GLYPH: Final[tuple[str, str]] = ("●", "◉")


def active_pulse(frame: int, *, dark: bool = True) -> tuple[str, str]:
    """Return ``(glyph, hex)`` for the ACTIVE pulse at `frame`.

    Frame wraps modulo ``ACTIVE_PULSE_FRAMES`` so callers can pass a
    monotonically incrementing tick count without bookkeeping. Frame 0
    reuses the canonical ACTIVE color/glyph; frame 1 swaps to the
    mint-tinted swelled variant.
    """
    idx = frame % ACTIVE_PULSE_FRAMES
    if idx == 0:
        return _ACTIVE_PULSE_GLYPH[0], STATUS_HEX[dark][WorkspaceStatus.ACTIVE]
    return _ACTIVE_PULSE_GLYPH[1], ACTIVE_PULSE_TINT_HEX[dark]


def status_label(status: WorkspaceStatus) -> str:
    """Return the user-facing label for `status` (defends against intent leaks)."""
    return STATUS_LABEL.get(status, status.value)


def status_color(status: WorkspaceStatus, *, dark: bool = True) -> str:
    """Return the theme hex for `status`. Unknown → fg fallback."""
    return STATUS_HEX[dark].get(status, "#ffffff" if dark else "#000000")


# Agent-activity-state glyph + label. The companion of STATUS_GLYPH/LABEL for
# the agent dimension (what the session is *doing*), consumed by the Activity
# Dashboard's tiles. Glyphs are picked from the same terminal-safe Unicode
# blocks as the workspace-status glyphs and stay visually distinct from them so
# a glance separates "the workspace is live" (●) from "the agent is working"
# (▶). One char each — no Nerd Font dependency.
AGENT_STATE_GLYPH: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "◌",  # dotted circle — spinning up
    AgentActivityState.WORKING: "▶",  # play triangle — in the loop
    AgentActivityState.WAITING: "◑",  # right-half circle — turn ended, wants the human
    AgentActivityState.BLOCKED: "⚠",  # warning — explicit prompt
    AgentActivityState.IDLE: "○",  # empty circle — alive but quiet
    AgentActivityState.ERROR: "✗",  # cross — failed run
    AgentActivityState.UNKNOWN: "·",  # mid-dot — no signal
}

AGENT_STATE_LABEL: dict[AgentActivityState, str] = {
    AgentActivityState.STARTING: "starting",
    AgentActivityState.WORKING: "working",
    AgentActivityState.WAITING: "waiting",
    AgentActivityState.BLOCKED: "blocked",
    AgentActivityState.IDLE: "idle",
    AgentActivityState.ERROR: "error",
    AgentActivityState.UNKNOWN: "unknown",
}


def agent_state_glyph(state: AgentActivityState) -> str:
    """Return the one-char glyph for an `AgentActivityState`. Unknown → '·'."""
    return AGENT_STATE_GLYPH.get(state, "·")


def agent_state_label(state: AgentActivityState) -> str:
    """Return the user-facing label for an `AgentActivityState`."""
    return AGENT_STATE_LABEL.get(state, state.value)


def agent_state_color(state: AgentActivityState, *, dark: bool = True) -> str:
    """Return the theme hex for an `AgentActivityState`. Unknown → fg fallback."""
    return AGENT_STATE_HEX[dark].get(state, "#ffffff" if dark else "#000000")


# Task-phase glyph + label — the third axis, sibling of STATUS_*/AGENT_STATE_*.
# `WorkspaceStatus` is the workspace/tmux lifecycle; `AgentActivityState` is
# what the session is doing moment-to-moment; `TaskPhase` is how far through
# the task the agent SAYS it is — three orthogonal facts a card can show at
# once (see grove.core.phase's module docstring). Glyphs are eighths of a
# filled block (U+2581-2588), read left-to-right as a growing progress bar —
# a shape neither STATUS_GLYPH nor AGENT_STATE_GLYPH uses (those are circles/
# shapes: ● ○ ◐ ◑ ◌ ‖ ⊘ ⚠ ✗ ·), so the phase segment can never be mistaken for
# either at a glance. `done` breaks from the bar to `✓` — it leaves the
# in-progress ramp the same way its hex leaves the lime ramp for muted gray
# (contracts.phase_palette), so glyph and color agree about "this one is
# different in kind, not just further along".
PHASE_GLYPH: dict[TaskPhase, str] = {
    "scoping": "▁",  # 1/8 — oriented, barely started
    "planning": "▂",  # 2/8
    "implementing": "▄",  # 4/8 — producing
    "verifying": "▆",  # 6/8
    "delivering": "█",  # 8/8 — full bar, converging
    "done": "✓",  # leaves the bar — converged, not "more full"
}

PHASE_LABEL: dict[TaskPhase, str] = {
    "scoping": "scoping",
    "planning": "planning",
    "implementing": "implementing",
    "verifying": "verifying",
    "delivering": "delivering",
    "done": "done",
}


def phase_glyph(phase: TaskPhase) -> str:
    """Return the one-char glyph for a `TaskPhase`. Unknown → '?'."""
    return PHASE_GLYPH.get(phase, "?")


def phase_label(phase: TaskPhase) -> str:
    """Return the user-facing label for a `TaskPhase`."""
    return PHASE_LABEL.get(phase, phase)


def phase_color(phase: TaskPhase, *, dark: bool = True) -> str:
    """Return the theme hex for a `TaskPhase`. Unknown → fg fallback."""
    return PHASE_HEX[dark].get(phase, "#ffffff" if dark else "#000000")


# Runtime — the isolation axis, and the ONE axis whose glyph is not defined in
# this module. `RUNTIME_GLYPH` / `RUNTIME_LABEL` are imported from the wire
# contract (`grove.core.contracts.runtime_palette`) beside the hex, because a
# user moving between the TUI and the web console must not have to learn two
# marks for "can this agent reach my filesystem"; the web client mirrors that
# same file and a drift test reads it. The other three axes keep their glyphs
# here — they were mirrored by hand before this axis existed, and moving them
# would be churn for no new guarantee.
#
# The family is SQUARES (`■` the work, `▣` the same work inside a boundary),
# disjoint from status/agent-state circles, the phase block ramp and the PR
# arrow — pinned by the collision test, which also covers the chrome glyphs,
# because the obvious house `⌂` is already the status bar's repo mark. Both
# runtimes are marked: unlike
# `Placement`, whose default is a boring implementation detail worth staying
# silent about, runtime IS the isolation boundary, so an unmarked workspace
# would be indistinguishable from one whose mark failed to render.


def runtime_glyph(runtime: Runtime) -> str:
    """Return the one-char glyph for a `Runtime`. Unknown → '?'."""
    return RUNTIME_GLYPH.get(runtime, "?")


def runtime_label(runtime: Runtime) -> str:
    """Return the user-facing label for a `Runtime`."""
    return RUNTIME_LABEL.get(runtime, runtime.value)


def runtime_color(runtime: Runtime, *, dark: bool = True) -> str:
    """Return the theme hex for a `Runtime`. Unknown → the host (ambient) hue."""
    palette = RUNTIME_HEX[dark]
    return palette.get(runtime, palette[Runtime.HOST])


# Pull-request state — rendered on a `TicketRef` whose `kind == "pull_request"`.
# Deliberately NOT a fourth palette in `theme.py`: a PR's state maps cleanly
# onto axes Grove already paints elsewhere, so this composes `status_color` /
# `chrome_color` instead of adding new hex atoms (design-system.md's "one
# source of color truth" — reuse, don't invent a new color language). "open"
# takes the ACTIVE lime (work still in flight, same "live" semantic); "merged"
# takes the muted gray PAUSED/OFFLINE already use for "no live signal, nothing
# left to do" — the terminal/settled state; "closed" (or any other/unset
# status — a PR ref can exist before enrichment fills `status`) takes the
# ERROR red, since an unlanded PR skews toward "needs a look" rather than
# "quietly fine". `PR_GLYPH` is a rightwards double arrow (U+21D2, the Arrows
# block) — a fourth glyph family, disjoint from both the circles/shapes
# `STATUS_GLYPH`/`AGENT_STATE_GLYPH` use and the growing-block `PHASE_GLYPH`
# ramp, so it can never be mistaken for either at a glance. It reads as
# "leads to / resolves in": the same token both connects an issue chain to
# the PR it produced (the input→outcome relationship) and, colored by state,
# IS the PR's own status indicator — one glyph doing both jobs, the same way
# the status glyph doubles as connector-free lead-in on line 1.
PR_GLYPH: Final = "⇒"

_PR_OPEN_STATUS: Final = WorkspaceStatus.ACTIVE
_PR_CLOSED_STATUS: Final = WorkspaceStatus.ERROR


def pr_status_color(status: str | None, *, dark: bool = True) -> str:
    """Return the theme hex for a PR's `status` string ("open"/"merged"/"closed").

    `merged` and any unset/unrecognized status settle to muted gray — the
    same "no signal / not alarming" default `AGENT_STATE_UNKNOWN` uses,
    rather than defaulting to red for a PR ref whose enrichment hasn't
    landed yet.
    """
    if status == "open":
        return status_color(_PR_OPEN_STATUS, dark=dark)
    if status == "closed":
        return status_color(_PR_CLOSED_STATUS, dark=dark)
    return chrome_color("muted", dark=dark)


def init_status_color(status: InitStatus, *, dark: bool = True) -> str:
    """Return the theme hex for an `InitStatus`. Unknown → fg fallback."""
    return INIT_STATUS_HEX[dark].get(status, "#ffffff" if dark else "#000000")


def ref_color(kind: RefKind, *, dark: bool = True) -> str:
    """Return the theme hex for a ref accent (`branch`, `diff_add`, ...)."""
    return REF_HEX[dark][kind]


def chrome_color(kind: ChromeKind, *, dark: bool = True) -> str:
    """Return the theme hex for chrome accent/muted text in Rich markup.

    Used by the contextual footer to color keys (clay accent) and
    separators (muted gray) without hardcoding hex into widgets.
    """
    return CHROME_HEX[dark][kind]
