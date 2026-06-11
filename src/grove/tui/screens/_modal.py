"""GroveModal — shared base class for every modal screen in the TUI.

Defines the centered + bordered-dialog + dimmed-backdrop look so all
Grove modals share the same chrome. Subclasses populate `compose_dialog()`
with their inner widgets; the base wraps them in the standard dialog
Vertical and applies CSS by class selector.

Why a base class instead of a CSS string constant: Textual cascades
DEFAULT_CSS through the inheritance chain, so subclasses can override
or extend without re-stating the framing rules. The result is ~25 lines
of CSS in one place instead of duplicated across every modal.
"""

from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

_T = TypeVar("_T")


class GroveModal(ModalScreen[_T]):
    """Centered, bordered, button-padded modal. Subclass and override compose."""

    DEFAULT_CSS = """
    GroveModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.55);
    }
    GroveModal .grove-dialog {
        background: $surface;
        border: tall $primary;
        padding: 1 2;
        height: auto;
        width: 70;
    }
    GroveModal .grove-dialog-title {
        text-style: bold;
        margin-bottom: 1;
    }
    GroveModal .grove-dialog-section {
        margin-top: 1;
    }
    GroveModal .grove-dialog-buttons {
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    GroveModal Button {
        margin: 0 1;
    }
    GroveModal .grove-detail {
        color: $text-muted;
    }
    GroveModal .grove-danger {
        color: $error;
        text-style: bold;
    }
    /* Checkbox state must read as empty-box vs filled-box, NOT as a
     * color shift of an always-present mark. Textual's stock ToggleButton
     * renders its inner glyph in EVERY state and conveys on/off only by
     * the glyph's color ($panel-darken-2 off → $text-success on). On
     * Grove's warm-dark palette the "off" mark is a near-black X on a dark
     * pill: still an "X" (which universally reads as "ticked"), so both
     * states look checked and the box appears stuck on. Fix: hide the mark
     * when off (paint it the pill's own background → a plain empty box) and
     * fill the whole pill with $success when on (an unmistakable filled
     * box). Lives here so BOTH modal checkboxes — #skip-init (create) and
     * #delete-branch (kill confirm) — inherit one source of truth. */
    GroveModal Checkbox > .toggle--button {
        color: $panel;
        background: $panel;
    }
    GroveModal Checkbox.-on > .toggle--button {
        color: $panel;
        background: $success;
    }
    """
