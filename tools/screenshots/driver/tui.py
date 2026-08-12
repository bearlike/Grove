"""Render Grove TUI states into reproducible SVG screenshots.

The Textual screenshot mechanism is the same one the in-app command palette
invokes (``App.export_screenshot``); driving it from a ``Pilot`` makes the run
deterministic. This module owns the shot list, the keystrokes and the terminal
geometry, and nothing about what the fleet underneath it contains.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from textual.pilot import Pilot

from grove.core.auth import PairingChallenge
from grove.core.manager import WorkspaceManager
from grove.tui.app import GroveApp
from grove.tui.screens.pairing import PairingModal

PilotAction = Callable[[Pilot[Any]], Awaitable[None]]

TERMINAL_SIZE: Final[tuple[int, int]] = (132, 36)
"""132 columns by 36 rows produces an SVG with roughly 16:9 visual aspect once
the monospace cell ratio (~0.5:1) is applied. Picked so every screenshot embeds
at the same size on the docs site and the TUI never wraps unexpectedly."""

_SETTLE_SECONDS: Final[float] = 3.4
"""Past the slow stats tick (3s) so the list cards pick up the per-row
agent-activity segment (working / waiting) and the peek rail repaints with real
pane + transcript content, not just lifecycle status. The fast pane tick (0.25s)
has fired many times by then."""

# Textual writes `<svg class="rich-terminal" viewBox="0 0 W H">` with no width or
# height, which leaves the file with an aspect ratio but NO intrinsic size.
# Inline that is invisible, because the docs CSS sizes the image anyway. It stops
# being invisible the moment something has to size the image on its own: the docs
# theme's full-screen viewer read 263x150 for a 1629x928 capture and opened it
# THREE TIMES SMALLER than it renders in the page. Stamping the viewBox onto
# width/height costs nothing and makes the asset self-describing.
_SVG_OPEN: Final[str] = '<svg class="rich-terminal" viewBox="0 0 '


class TuiCapture:
    """Every TUI shot the docs site publishes, in one place."""

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir

    async def capture_fleet(self, manager: WorkspaceManager) -> None:
        """The populated surfaces — list, modals, browsers, overlays."""
        await self.shoot(manager, "tui-list", "Grove")
        await self.shoot(manager, "tui-create-modal", "Grove · new workspace", self.press("n"))
        await self.shoot(manager, "tui-edit-modal", "Grove · edit workspace", self.press("e"))
        await self.shoot(manager, "tui-steer", "Grove · send message", self._steer)
        await self.shoot(manager, "tui-sessions", "Grove · sessions", self.press("s"))
        await self.shoot(manager, "tui-project-switcher", "Grove · switch project", self.press("P"))
        await self.shoot(manager, "tui-dashboard", "Grove · dashboard", self.press("d"))
        await self.shoot(manager, "tui-help", "Grove · help", self.press("question_mark"))
        await self.shoot(manager, "tui-filter", "Grove · filter", self._filter)
        await self.shoot(manager, "tui-kill-confirm", "Grove · kill confirm", self.press("k"))
        await self.shoot(manager, "tui-pause-confirm", "Grove · pause confirm", self.press("p"))
        await self.shoot(manager, "tui-pair-approve", "Grove · pair device", self._pair)

    async def capture_empty(self, manager: WorkspaceManager) -> None:
        """The empty state, which needs a repo with no workspaces of its own."""
        await self.shoot(manager, "tui-empty", "Grove · empty state")

    async def shoot(
        self,
        manager: WorkspaceManager,
        name: str,
        title: str,
        actions: PilotAction | None = None,
    ) -> None:
        app = GroveApp(manager)
        async with app.run_test(size=TERMINAL_SIZE) as pilot:
            await pilot.pause(_SETTLE_SECONDS)
            if actions is not None:
                await actions(pilot)
                await pilot.pause(0.4)
            svg = self._with_intrinsic_size(app.export_screenshot(title=title))
            (self._out_dir / f"{name}.svg").write_text(svg, encoding="utf-8")

    @staticmethod
    def press(key: str) -> PilotAction:
        """A pilot action that presses one key then settles."""

        async def _action(pilot: Pilot[Any]) -> None:
            await pilot.press(key)
            await pilot.pause()

        return _action

    @staticmethod
    async def _type(pilot: Pilot[Any], text: str) -> None:
        for char in text:
            await pilot.press("space" if char == " " else char)

    @classmethod
    async def _steer(cls, pilot: Pilot[Any]) -> None:
        await pilot.press("m")
        await pilot.pause()
        await cls._type(pilot, "fix the failing oauth callback test too")

    @classmethod
    async def _filter(cls, pilot: Pilot[Any]) -> None:
        await pilot.press("slash")
        await pilot.pause()
        await cls._type(pilot, "auth")

    @staticmethod
    async def _pair(pilot: Pilot[Any]) -> None:
        """The pairing modal is event-driven, so it is pushed directly."""
        challenge = PairingChallenge.fresh(
            label="Pixel 8 (Chrome)",
            code="QH7K2M",
            now=datetime.now(tz=UTC),
            ttl=timedelta(minutes=5),
        )
        await pilot.app.push_screen(PairingModal(challenge))
        await pilot.pause()

    @staticmethod
    def _with_intrinsic_size(svg: str) -> str:
        if not svg.startswith(_SVG_OPEN):
            return svg
        box = svg[len(_SVG_OPEN) : svg.index('"', len(_SVG_OPEN))]
        width, height = box.split(" ")
        return svg.replace(
            '<svg class="rich-terminal" ',
            f'<svg class="rich-terminal" width="{width}" height="{height}" ',
            1,
        )
