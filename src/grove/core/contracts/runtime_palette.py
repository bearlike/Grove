"""Canonical runtime → glyph/label/hex mapping for cross-client coherence.

The fourth sibling of ``status_palette`` (workspace lifecycle), ``agent_palette``
(agent activity) and ``phase_palette`` (task progress), for the *isolation*
dimension — whether the agent runs on this host or inside a container.

**This palette pins the GLYPH as well as the hex, and that is the substantive
difference from its three siblings.** Their glyphs live in ``grove.tui._status``
and every other client mirrors them by hand; a runtime mark is a two-member
vocabulary that has to mean the same thing to a user who moves between the TUI
and the web console mid-task, so the character itself is contract rather than
convention. The TUI imports these dicts (drift is impossible by construction);
the web client mirrors them and a drift test reads *this file* to prove parity.

**Silence is NOT the signal here, and that reverses the rule ``Placement``
follows.** A worktree-vs-root placement is an implementation detail, so the
common case renders nothing. Runtime is the isolation boundary — whether the
agent can reach the host filesystem, the host network and the user's
credentials — so both states carry a permanent mark. Making only the
exceptional case visible leaves the common case indistinguishable from "this
component failed to render", which is the wrong ambiguity for the one axis that
answers "what can this agent touch".

Glyph family — **squares, read as "the work, and the work enclosed"**, disjoint
from every other axis by construction: circles and shapes carry workspace status
and agent state, growing block eighths carry task phase, an arrow carries a pull
request. The pair is literal rather than metaphorical — ``▣`` *is* ``■`` with a
boundary drawn around it, which is exactly what the axis measures:

  - HOST      : ``■`` the work, with nothing drawn around it
  - CONTAINER : ``▣`` the same work, inside a boundary

A house (``⌂``) was the obvious host mark and is wrong here: the TUI status bar
already spends that character on repo identity, and one glyph meaning two things
in one app is the collision this family exists to avoid. Check the chrome
glyphs, not only the axis maps, before picking the next one.

Color reuses existing atoms rather than minting a hue: the host mark takes the
muted gray that already means "ambient, no live signal" (the same atom the TUI's
``root`` tag uses), the container mark takes the info cyan that already means
"auxiliary metadata worth noticing, not a warning". Amber and red stay reserved
for the *degradations* (``runtime_fallback_reason``, ``runtime_no_tmux``) so a
workspace whose isolation contract was voided can never read as a healthy one.
"""

from __future__ import annotations

from typing import Final

from grove.core.workspace import Runtime

# Hex atoms shared with the sibling palettes — the muted gray is
# ``status_palette``'s OFFLINE/PAUSED value, the cyan is its IDLE value (and
# ``agent_palette``'s STARTING). Kept as literals here, with their rationale,
# so a non-Python client can read this file as a self-contained contract.
_DARK_HOST: Final = "#96938c"  # muted gray — the ambient default, no boundary
_DARK_CONTAINER: Final = "#c2dcf7"  # info cyan — noteworthy, never a warning

DARK_RUNTIME_HEX: Final[dict[Runtime, str]] = {
    Runtime.HOST: _DARK_HOST,
    Runtime.CONTAINER: _DARK_CONTAINER,
}

RUNTIME_GLYPH: Final[dict[Runtime, str]] = {
    Runtime.HOST: "■",  # U+25A0 BLACK SQUARE — the work, no boundary drawn
    Runtime.CONTAINER: "▣",  # U+25A3 — the same square, inside a boundary
}

RUNTIME_LABEL: Final[dict[Runtime, str]] = {
    Runtime.HOST: "host",
    Runtime.CONTAINER: "container",
}
