"""Canonical task-phase → hex mapping for cross-client coherence.

The third sibling of ``status_palette`` (workspace lifecycle) and
``agent_palette`` (agent activity), for the *task* dimension — how far through
its work the agent says it is. Same contract and same reason: every client
renders ``verifying`` the same color, and none of them is "the source".

**This palette is a SEQUENTIAL RAMP, and that is the substantive difference
from its two siblings.** Those map categorical states — ``BLOCKED`` is not
"more" than ``WAITING``, so each gets its own semantic hue. Phases are ordered
and converging, so distinct hues would encode a difference in *kind* that does
not exist and would leave the reader decoding a legend instead of seeing
progress. One hue deepening through the ramp reads as advancement at a glance,
across a grid of twenty cards, which is the whole use case.

The ramp is anchored on the brand lime that already means "live signal" on both
other axes, so a workspace deep in its task reads as vividly as an active one.
``done`` leaves the ramp for muted gray — the same atom ``IDLE``/``OFFLINE``
use — because a converged task is settled rather than intense, and a finished
workspace should recede from a fleet view rather than compete with live work.

**Amber and red are deliberately unused.** They mean "wants the human" and
"something failed" on the agent axis, and a phase means neither — an agent in
``verifying`` is not asking for anything. Reusing them would make the two
palettes contradict each other on the same card, which is precisely what having
one canonical map per axis exists to prevent.
"""

from __future__ import annotations

from typing import Final

from grove.core.phase import TaskPhase

# A single-hue ramp from the brand lime (``#84cc16``, the ACTIVE/WORKING atom).
# Lightness descends as the task advances, so saturation tracks progress and no
# member borrows another axis's semantic color.
_DARK_SCOPING: Final = "#e4f7c0"  # palest lime — oriented, not yet producing
_DARK_PLANNING: Final = "#cbeb8a"  # ramping — shape of the work is forming
_DARK_IMPLEMENTING: Final = "#a8dd47"  # producing
_DARK_VERIFYING: Final = "#84cc16"  # brand lime — peak, the work is real
_DARK_DELIVERING: Final = "#5f9c0f"  # deep lime — converging, handing off
_DARK_DONE: Final = "#96938c"  # muted gray — settled; recedes from the fleet view

DARK_PHASE_HEX: Final[dict[TaskPhase, str]] = {
    "scoping": _DARK_SCOPING,
    "planning": _DARK_PLANNING,
    "implementing": _DARK_IMPLEMENTING,
    "verifying": _DARK_VERIFYING,
    "delivering": _DARK_DELIVERING,
    "done": _DARK_DONE,
}
