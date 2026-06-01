"""Default keybindings for the Grove TUI.

Each binding is (key, action_id, description). The list screen wires
these into Textual's Binding objects. Custom UI overrides will land in
M6 — the cfg.ui.keybindings map will replace entries by `action_id`.

`LIST_GLOBAL_FOOTER_KEYS` / `LIST_SELECTION_FOOTER_KEYS` partition the
list-screen bindings into "always available" vs "needs a selection" so
both the contextual footer and the help modal stay in sync. Modal
screens declare their own footer keys inline (each modal is small
enough that a partition table would be over-abstraction).
"""

from __future__ import annotations

from typing import Final

DEFAULT_BINDINGS: Final[list[tuple[str, str, str]]] = [
    ("q", "quit", "Quit"),
    ("r", "refresh", "Refresh"),
    ("n", "new_workspace", "New"),
    ("e", "edit_workspace", "Edit"),
    ("p", "pause_workspace", "Pause"),
    ("R", "resume_workspace", "Resume"),
    ("o", "respawn_workspace", "Respawn"),
    ("k", "kill_workspace", "Kill"),
    ("enter,a", "attach_workspace", "Attach"),
    ("/", "focus_filter", "Filter"),
    ("?", "help", "Help"),
]

LIST_GLOBAL_FOOTER_KEYS: Final[tuple[str, ...]] = ("q", "n", "r", "/", "?")
# Order in the footer: attach (most common), edit (metadata), pause/resume
# (lifecycle pair), respawn (recovery for offline), kill (destructive —
# last). Each entry is dimmed by the screen when it's not currently
# applicable to the selection.
LIST_SELECTION_FOOTER_KEYS: Final[tuple[str, ...]] = ("enter,a", "e", "p", "R", "o", "k")
