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
from grove.tui.theme import (
    ACTIVE_PULSE_TINT_HEX,
    AGENT_STATE_HEX,
    CHROME_HEX,
    INIT_STATUS_HEX,
    REF_HEX,
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
