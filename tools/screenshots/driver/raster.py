"""Turn the Textual SVG captures into framed PNGs.

Textual exports a terminal as SVG, which is the right capture format: it is
deterministic, it is text, and it re-renders at any size. It is the wrong
PUBLISH format here for one reason that has nothing to do with fidelity — the
published shot is supposed to sit on the same wallpaper the web-dashboard shots
do, and that composite is a pixel operation (`tools/screenshots/frame.py`). An
SVG can only join that family by being rasterized first.

Baking to PNG also drops a dependency the SVGs carry into the reader's browser:
Rich writes an `@font-face` pointing at a CDN copy of Fira Code, so every visitor
fetches a webfont to read a screenshot, and a blocked CDN silently reshapes the
capture in the page. Rasterizing resolves that font once, here, at build time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

RASTER_MJS: Final[Path] = Path(__file__).with_name("svg_raster.mjs")

SUPERSAMPLE: Final[int] = 2
"""Rasterize at twice the SVG's intrinsic size.

The framer CONTAINS its source in a 2112x1212 box, and these captures are
1629x928 — so a 1x raster would be upscaled by 1.3 and land soft. At 2x the
framer downsamples instead, which is the direction that stays sharp.
"""


class SvgRaster:
    """Render SVG captures to PNG through the browser, not through ImageMagick.

    The renderer choice is the whole point of this class. ImageMagick's rsvg
    delegate drops the cell background fills that carry most of the TUI's
    meaning (the peek rail, every status colour, the selected row), so the only
    trustworthy rasterizer is the same engine the docs site renders these files
    in. `node` and the webapp's Playwright install are the cost of that.
    """

    def __init__(self, worktree: Path, scale: int = SUPERSAMPLE) -> None:
        self._worktree = worktree
        self._scale = scale

    def preflight(self) -> str | None:
        """Say why rasterizing cannot run, or `None` if it can."""
        if shutil.which("node") is None:
            return "node not found on PATH"
        playwright = self._worktree / "webapp" / "node_modules" / "@playwright" / "test"
        if not playwright.exists():
            return (
                f"Playwright not installed: {playwright} is missing (run `npm install` in webapp/)"
            )
        return None

    def rasterize(self, jobs: dict[Path, Path]) -> None:
        """Render each `svg -> png` pair, raising if the browser run fails."""
        if not jobs:
            return
        payload = [{"src": str(src), "out": str(out)} for src, out in sorted(jobs.items())]
        node = shutil.which("node")
        assert node is not None  # preflight() proved this
        subprocess.run(
            [node, str(RASTER_MJS)],
            check=True,
            # Inherited, not constructed: Playwright resolves its browser build
            # out of the real HOME cache, and a hand-built env that forgets one
            # such variable fails as a browser launch error rather than as a
            # missing-variable one.
            env={
                **os.environ,
                "WORKTREE": str(self._worktree),
                "SOURCES": json.dumps(payload),
                "SCALE": str(self._scale),
            },
        )
