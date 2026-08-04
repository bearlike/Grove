"""Render Grove TUI states into reproducible SVG screenshots.

This pipeline runs Grove against the shared synthetic demo fleet
(`tools/screenshots/_fleet.py`): two real on-disk repos with real git
worktrees and real tmux sessions, plus hand-planted Claude-Code-style
transcripts so the activity readouts, the transcript tab, and the
sessions browser render real recorded turns rather than empty history.

Run via:

    make docs-screenshots

or directly:

    uv run python -m tools.screenshots.capture

Output lands in ``docs/img/screenshots/``. Every SVG is captured at the
same terminal dimensions for visual consistency. The Textual screenshot
mechanism is the same one the in-app command palette invokes
(``App.export_screenshot``); driving it from a ``Pilot`` makes the run
deterministic. The whole run is sandboxed: ``XDG_CONFIG_HOME``,
``XDG_STATE_HOME`` and ``CLAUDE_CONFIG_DIR`` are pointed at a throwaway
temp tree, so nothing touches the user's real config or repos. Requires
`tmux`, `bash`, and `git` on PATH. Cleans up the demo tree on exit.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

# ── Sandbox the whole run BEFORE importing anything that resolves a config
#    dir. platformdirs reads these env vars at call time, so setting them
#    here keeps every resolved path inside the throwaway tree.
DEMO_ROOT = Path("/tmp/grove-screenshots")
_SANDBOX = DEMO_ROOT / "sandbox"
os.environ["XDG_CONFIG_HOME"] = str(_SANDBOX / "config")
os.environ["XDG_STATE_HOME"] = str(_SANDBOX / "state")
os.environ["CLAUDE_CONFIG_DIR"] = str(_SANDBOX / "claude")

from tools.screenshots import _fleet  # noqa: E402

from grove.core.auth import PairingChallenge  # noqa: E402
from grove.core.store import JsonWorkspaceStore  # noqa: E402
from grove.tui.app import GroveApp  # noqa: E402
from grove.tui.screens.pairing import PairingModal  # noqa: E402

# Fixed terminal dimensions. 132 columns by 36 rows produces an SVG with
# roughly 16:9 visual aspect once the monospace cell ratio (~0.5:1) is
# applied. Picked so every screenshot embeds at the same size on the docs
# site and the TUI never wraps unexpectedly.
TERMINAL_SIZE = (132, 36)

OUT_DIR = _fleet.REPO_ROOT / "docs" / "img" / "screenshots"


# ─── pilot runner ────────────────────────────────────────────────────────────

# Textual writes `<svg class="rich-terminal" viewBox="0 0 W H">` with no width
# or height, which leaves the file with an aspect ratio but NO intrinsic size.
# Inline that is invisible, because the docs CSS sizes the image anyway. It
# stops being invisible the moment something has to size the image on its own:
# the docs theme's full-screen viewer read 263x150 for a 1629x928 capture and
# opened it THREE TIMES SMALLER than it renders in the page. Stamping the
# viewBox onto width/height costs nothing and makes the asset self-describing.
_SVG_OPEN = '<svg class="rich-terminal" viewBox="0 0 '


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


async def _shoot(
    manager: Any,
    name: str,
    title: str,
    actions: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    app = GroveApp(manager)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        # Settle past the slow stats tick (3s) so the list cards pick up the
        # per-row agent-activity segment (working / waiting) and the peek rail
        # repaints with real pane + transcript content, not just lifecycle
        # status. The fast pane tick (0.25s) has fired many times by then.
        await pilot.pause(3.4)
        if actions is not None:
            await actions(pilot)
            await pilot.pause(0.4)
        svg = _with_intrinsic_size(app.export_screenshot(title=title))
        (OUT_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")


# ─── entry point ─────────────────────────────────────────────────────────────


async def main() -> None:
    # Drop loguru's default DEBUG sink; the run is otherwise noisy.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _fleet.install_quiet_tmux()

    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True)

    fleet = _fleet.seed_fleet(
        DEMO_ROOT / "fleet", JsonWorkspaceStore(path=DEMO_ROOT / "fleet" / "state.json")
    )
    api, web = fleet.managers()
    try:
        # ── the populated list view (header, list, peek rail) ───────────
        await _shoot(api, "tui-list", "Grove")

        # ── create / edit modals ────────────────────────────────────────
        await _shoot(api, "tui-create-modal", "Grove · new workspace", _press("n"))
        await _shoot(api, "tui-edit-modal", "Grove · edit workspace", _press("e"))

        # ── steer modal (m): type a follow-up to the selected agent ─────
        async def _steer(pilot: Any) -> None:
            await pilot.press("m")
            await pilot.pause()
            for ch in "fix the failing oauth callback test too":
                await pilot.press("space" if ch == " " else ch)

        await _shoot(api, "tui-steer", "Grove · send message", _steer)

        # ── sessions browser (s) ────────────────────────────────────────
        await _shoot(api, "tui-sessions", "Grove · sessions", _press("s"))

        # ── project switcher (P) ────────────────────────────────────────
        await _shoot(api, "tui-project-switcher", "Grove · switch project", _press("P"))

        # ── activity dashboard (d) ──────────────────────────────────────
        await _shoot(api, "tui-dashboard", "Grove · dashboard", _press("d"))

        # ── help (?) ────────────────────────────────────────────────────
        await _shoot(api, "tui-help", "Grove · help", _press("question_mark"))

        # ── filter bar (/auth) ──────────────────────────────────────────
        async def _filter(pilot: Any) -> None:
            await pilot.press("slash")
            await pilot.pause()
            for ch in "auth":
                await pilot.press(ch)

        await _shoot(api, "tui-filter", "Grove · filter", _filter)

        # ── kill / pause confirm ────────────────────────────────────────
        await _shoot(api, "tui-kill-confirm", "Grove · kill confirm", _press("k"))
        await _shoot(api, "tui-pause-confirm", "Grove · pause confirm", _press("p"))

        # ── pairing approve modal (event-driven; pushed directly) ───────
        async def _pair(pilot: Any) -> None:
            challenge = PairingChallenge.fresh(
                label="Pixel 8 (Chrome)",
                code="QH7K2M",
                now=datetime.now(tz=UTC),
                ttl=timedelta(minutes=5),
            )
            await pilot.app.push_screen(PairingModal(challenge))
            await pilot.pause()

        await _shoot(api, "tui-pair-approve", "Grove · pair device", _pair)
    finally:
        _fleet.teardown(api, web)

    # The empty state needs a repo with no workspaces of its own.
    empty = _fleet._make_manager(
        _fleet.make_repo(DEMO_ROOT / "empty", "myproject"),
        JsonWorkspaceStore(path=DEMO_ROOT / "empty" / "state.json"),
    )
    try:
        await _shoot(empty, "tui-empty", "Grove · empty state")
    finally:
        _fleet.teardown(empty)

    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    print(f"wrote SVG screenshots to {OUT_DIR}", file=sys.stderr)


def _press(key: str) -> Callable[[Any], Awaitable[None]]:
    """A pilot action that presses one key then settles."""

    async def _action(pilot: Any) -> None:
        await pilot.press(key)
        await pilot.pause()

    return _action


if __name__ == "__main__":
    asyncio.run(main())
