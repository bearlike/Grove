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
    ("d", "open_dashboard", "Dashboard"),
    ("u", "open_usage", "Usage"),
    ("P", "switch_project", "Project"),
    ("m", "send_message", "Message"),
    ("e", "edit_workspace", "Edit"),
    ("s", "open_sessions", "Sessions"),
    ("x", "remap_session", "Remap"),
    ("p", "pause_workspace", "Pause"),
    ("R", "resume_workspace", "Resume"),
    ("o", "respawn_workspace", "Respawn"),
    ("k", "kill_workspace", "Kill"),
    ("enter,a", "attach_workspace", "Attach"),
    ("/", "focus_filter", "Filter"),
    ("?", "help", "Help"),
]

LIST_GLOBAL_FOOTER_KEYS: Final[tuple[str, ...]] = ("q", "n", "d", "u", "P", "r", "/", "?")
# Order in the footer: attach (most common), message (steer without
# attaching), edit (metadata), sessions (read-only history), remap (fix
# session tracking — a recovery verb, sits with sessions), pause/resume
# (lifecycle pair), respawn (recovery for offline), kill (destructive —
# last). Each entry is dimmed by the screen when it's not currently
# applicable to the selection.
LIST_SELECTION_FOOTER_KEYS: Final[tuple[str, ...]] = (
    "enter,a",
    "m",
    "e",
    "s",
    "x",
    "p",
    "R",
    "o",
    "k",
)
