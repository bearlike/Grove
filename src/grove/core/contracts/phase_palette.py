"""Canonical task-phase → hex mapping for cross-client coherence.

The third sibling of ``status_palette`` (workspace lifecycle) and
``agent_palette`` (agent activity), for the *task* dimension — how far through
its work the agent says it is. Same contract and same reason: every client
renders ``verify`` the same color, and none of them is "the source".

**This palette is a SEQUENTIAL RAMP, and that is the substantive difference
from its two siblings.** Those map categorical states — ``BLOCKED`` is not
"more" than ``WAITING``, so each gets its own semantic hue. Phases are ordered
and converging, so distinct hues would encode a difference in *kind* that does
not exist and would leave the reader decoding a legend instead of seeing
progress. One hue deepening through the ramp reads as advancement at a glance,
across a grid of twenty cards, which is the whole use case.

The ramp is anchored on the brand lime that already means "live signal" on both
other axes, so a workspace deep in its task reads as vividly as an active one.
``handoff`` leaves the ramp for muted gray — the same atom ``IDLE``/``OFFLINE``
use — because a converged task is settled rather than intense, and a finished
workspace should recede from a fleet view rather than compete with live work.

**Amber and red are deliberately unused IN THE RAMP.** They mean "wants the
human" and "something failed" on the agent axis, and a phase means neither —
an agent in ``verify`` is not asking for anything. Reusing them as a
*seventh rung* would make the two palettes contradict each other on the same
card, which is precisely what having one canonical map per axis exists to
prevent.

**``blocked`` is the one claim on this axis that genuinely does mean "wants
the human" — and that is answered by an OVERRIDE, not a rung.** ``PhaseClaim.
blocked`` (``grove.core.phase``) is a flag beside the phase, not a seventh
member of :data:`~grove.core.phase.PHASE_ORDER` — a stuck task still has a
position on the ramp, which is the number a reader wants most (see that
field's own docstring). The color has to follow the same shape: a client
still renders the ramp hue for progress and separately swaps in
:data:`DARK_BLOCKED_HEX` — the SAME amber ``agent_palette`` already spends on
"wants the human" — as a badge, border, or dot laid over the ramp colour,
never replacing it in :data:`DARK_PHASE_HEX`. That dict is typed
``dict[TaskPhase, str]``; ``blocked`` is not a ``TaskPhase`` and adding it as
a key would make every reader either special-case one entry or silently
accept a value the type never promised. Two palettes agreeing on what amber
means is the same coherence the ramp itself protects — the reconciliation is
real, not a contradiction: amber stays reserved for "wants the human"
everywhere, and here it rides ON TOP of the ramp instead of standing in for
one of its rungs.
"""

from __future__ import annotations

from typing import Final

from grove.core.phase import TaskPhase

# A single-hue ramp from the brand lime (``#84cc16``, the ACTIVE/WORKING atom).
# Lightness descends as the task advances, so saturation tracks progress and no
# member borrows another axis's semantic color.
_DARK_SCOPE: Final = "#e4f7c0"  # palest lime — oriented, not yet producing
_DARK_PLAN: Final = "#cbeb8a"  # ramping — shape of the work is forming
_DARK_BUILD: Final = "#a8dd47"  # producing
_DARK_VERIFY: Final = "#84cc16"  # brand lime — peak, the work is real
_DARK_DELIVER: Final = "#5f9c0f"  # deep lime — converging, handing off
_DARK_HANDOFF: Final = "#96938c"  # muted gray — work transferred and settled

DARK_PHASE_HEX: Final[dict[TaskPhase, str]] = {
    "scope": _DARK_SCOPE,
    "plan": _DARK_PLAN,
    "build": _DARK_BUILD,
    "verify": _DARK_VERIFY,
    "deliver": _DARK_DELIVER,
    "handoff": _DARK_HANDOFF,
}

DARK_BLOCKED_HEX: Final = "#b8860b"  # warning amber — the SAME atom
# ``agent_palette.DARK_AGENT_STATE_HEX`` spends on WAITING/BLOCKED, so the two
# axes cannot render "wants the human" as two different colours. A SEPARATE
# constant, deliberately not a seventh entry in ``DARK_PHASE_HEX`` — see the
# module docstring for why the ramp's dict is not the right shape for a value
# that overrides one of its rungs rather than sitting beside them.
