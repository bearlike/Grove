"""ContextualFooter — replaces Textual's stock Footer with state-gated keys.

Why a custom footer: Textual's Footer renders every BINDINGS entry the
same way; we want selection-only keys (p, R, k, Enter/a) dimmed when
nothing is selected, so the user sees what's currently meaningful.

Format inside a group: `[bold]key[/] label  [bold]key[/] label …` joined
by a muted middle-dot. Multiple groups are joined by a muted vertical
bar — same idiom claude-squad's menu uses to separate logical groups
(globals on the left, selection-keys on the right). Modal screens that
only need one group call ``set_keys(...)`` and the widget renders a
single flat row with no divider.

One Static, one update per frame, no widget churn.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.widget import Widget

from grove.tui._status import chrome_color


@dataclass(frozen=True, slots=True)
class FooterKey:
    """One key spec for the footer."""

    key: str
    label: str
    available: bool = True


class ContextualFooter(Widget):
    """Single-line footer; call ``set_keys(...)`` or ``set_groups(...)`` to swap content."""

    DEFAULT_CSS = """
    ContextualFooter {
        height: 1;
        dock: bottom;
        background: $background;
        color: $text;
        padding: 0 2;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._groups: list[list[FooterKey]] = []

    def set_keys(self, keys: list[FooterKey]) -> None:
        """Render a single flat group. Modal screens use this."""
        self.set_groups([keys])

    def set_groups(self, groups: list[list[FooterKey]]) -> None:
        """Render multiple groups separated by a muted vertical bar.

        Empty groups are dropped so callers can pass conditional groups
        (e.g. selection-keys only when something is selected) without
        having to filter at the call site.
        """
        self._groups = [list(g) for g in groups if g]
        self.refresh()

    def render(self) -> str:
        if not self._groups:
            return ""
        dark = self.app.current_theme.dark
        accent = chrome_color("accent", dark=dark)
        muted = chrome_color("muted", dark=dark)
        dot = f" [{muted}]·[/] "
        bar = f"  [{muted}]│[/]  "
        rendered_groups: list[str] = []
        for group in self._groups:
            parts: list[str] = []
            for spec in group:
                display_key = spec.key.replace(",", "/")
                if spec.available:
                    parts.append(f"[bold {accent}]{display_key}[/] {spec.label}")
                else:
                    parts.append(f"[dim]{display_key} {spec.label}[/]")
            if parts:
                rendered_groups.append(dot.join(parts))
        return bar.join(rendered_groups)
