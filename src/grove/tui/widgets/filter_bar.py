"""FilterBar — single-line input that narrows the workspace table.

Hidden by default. Toggling adds/removes the `-active` class via the
list screen; CSS handles display. We don't manage focus from here —
the screen does, since it owns the table that needs to regain focus
when the bar is dismissed.
"""

from __future__ import annotations

from textual.widgets import Input


class FilterBar(Input):
    """Filter prompt for the workspace list. Empty value clears the filter."""

    DEFAULT_CSS = """
    FilterBar {
        display: none;
        height: 3;
        margin: 0 1;
    }
    FilterBar.-active {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__(placeholder="filter — substring of title / branch / agent (esc to clear)")
