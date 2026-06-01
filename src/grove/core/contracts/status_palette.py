"""Canonical status → hex mapping for cross-client visual coherence.

A wire-level concept: every Grove client (TUI today, web tomorrow) renders
the same workspace status in the same color. Clients read the dark variant
from this module so none is "the source"; they all consume one canonical
mapping. The TUI then supplements with a TUI-only light palette in
``grove.tui.theme``.

The mapping lives under ``grove.core.contracts`` rather than ``grove.tui``
because (a) it crosses the client boundary — sibling clients are not
allowed to import from ``grove.tui`` (see import-linter contracts) — and (b) status
colors are part of the user-facing semantic contract, not an internal
TUI implementation detail. Adding a status that displays in a new client
is a one-line edit here, propagated to every consumer.
"""

from __future__ import annotations

from typing import Final

from grove.core.workspace import WorkspaceStatus

# Hex atoms sourced from bearlike/Assistant's `:root` palette. See the
# ``grove.tui.theme`` module-level docstring for the full provenance and
# for the rationale behind each color (live signal vs. neutral teardown
# vs. warning amber, etc.). Keep these literals here — the TUI's
# in-module ``_DARK_*`` aliases compose them by reference, so changing a
# value in one place propagates to the Theme variables and the Rich-side
# lookup dicts in lockstep.
_DARK_ACTIVE: Final = "#84cc16"  # vibrant lime — live signal
_DARK_RUNNING: Final = _DARK_ACTIVE  # persisted intent shares ACTIVE color
_DARK_IDLE: Final = "#c2dcf7"  # info cyan — alive but quiet
_DARK_OFFLINE: Final = "#96938c"  # Assistant --muted-foreground
_DARK_PAUSED: Final = "#96938c"  # neutral gray — deliberate teardown
_DARK_ORPHANED: Final = "#b8860b"  # warning amber — stranded record
_DARK_ERROR: Final = "#e64c4c"  # Assistant --destructive

DARK_STATUS_HEX: Final[dict[WorkspaceStatus, str]] = {
    WorkspaceStatus.RUNNING: _DARK_RUNNING,
    WorkspaceStatus.ACTIVE: _DARK_ACTIVE,
    WorkspaceStatus.IDLE: _DARK_IDLE,
    WorkspaceStatus.OFFLINE: _DARK_OFFLINE,
    WorkspaceStatus.PAUSED: _DARK_PAUSED,
    WorkspaceStatus.ORPHANED: _DARK_ORPHANED,
    WorkspaceStatus.ERROR: _DARK_ERROR,
}
