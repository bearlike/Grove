"""Canonical agent-activity-state → hex mapping for cross-client coherence.

The sibling of ``status_palette`` for the *agent* dimension. ``status_palette``
colors the workspace lifecycle (ACTIVE / IDLE / PAUSED / …); this colors what
the agent session inside that workspace is *doing* (WORKING / WAITING / BLOCKED
/ …). The Activity Dashboard surfaces both at once — a workspace can be ACTIVE
(tmux producing output) while its agent is WAITING (turn ended, wants the
human) — so the two palettes are deliberately separate maps over separate
enums.

A wire-level concept for the same reason as ``status_palette``: every Grove
client (TUI today, web tomorrow) must render WORKING the same green and BLOCKED
the same red. Clients read the dark variant from here so none is "the source";
the TUI supplements a light palette in ``grove.tui.theme``. Lives under
``grove.core.contracts`` because it crosses the client boundary (siblings may
not import from ``grove.tui``) and because the agent-state semantics are part of
the user-facing contract, not a TUI detail.

Hue rationale (reuses the existing brand atoms so the two palettes read as one
language):
  - WORKING  : vibrant lime  — live signal, same green as a live workspace
  - STARTING : info cyan      — spinning up, not yet producing
  - WAITING  : warning amber  — turn ended, wants the human (attention)
  - BLOCKED  : warning amber  — explicit prompt (attention); glyph distinguishes
  - IDLE     : muted gray     — alive but quiet, no signal
  - ERROR    : destructive red — a failed run / unreadable transcript
  - UNKNOWN  : muted gray     — transcript suppressed; treated as "no signal"
"""

from __future__ import annotations

from typing import Final

from grove.core.agents import AgentActivityState

# Hex atoms reused from the bearlike/Assistant palette via the workspace-status
# values they already anchor — WORKING shares ACTIVE's lime, WAITING/BLOCKED
# share ORPHANED's amber, ERROR shares the destructive red. Keeping the literals
# co-located with their rationale (rather than importing them) makes this map a
# self-contained contract a web client can read without pulling the TUI theme.
_DARK_WORKING: Final = "#84cc16"  # vibrant lime — live signal (matches ACTIVE)
_DARK_STARTING: Final = "#c2dcf7"  # info cyan — spinning up
_DARK_WAITING: Final = "#b8860b"  # warning amber — wants the human
_DARK_BLOCKED: Final = "#b8860b"  # warning amber — explicit prompt
_DARK_IDLE: Final = "#96938c"  # muted gray — alive but quiet
_DARK_ERROR: Final = "#e64c4c"  # destructive red — failed / unreadable
_DARK_UNKNOWN: Final = "#96938c"  # muted gray — no signal

DARK_AGENT_STATE_HEX: Final[dict[AgentActivityState, str]] = {
    AgentActivityState.STARTING: _DARK_STARTING,
    AgentActivityState.WORKING: _DARK_WORKING,
    AgentActivityState.WAITING: _DARK_WAITING,
    AgentActivityState.BLOCKED: _DARK_BLOCKED,
    AgentActivityState.IDLE: _DARK_IDLE,
    AgentActivityState.ERROR: _DARK_ERROR,
    AgentActivityState.UNKNOWN: _DARK_UNKNOWN,
}
