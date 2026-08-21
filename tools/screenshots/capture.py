"""Capture the Grove TUI documentation screenshots.

Runs Grove against the shared synthetic demo world
(`tools/screenshots/fixture/demo.json`): two real on-disk repos with real git
worktrees and real tmux sessions, plus hand-planted provider-shaped transcripts,
so the activity readouts, the transcript tab and the sessions browser render
real recorded turns rather than empty history.

Run via:

    make docs-screenshots

or directly:

    uv run python -m tools.screenshots.capture

Output lands in ``docs/img/screenshots/``. Requires `tmux`, `bash` and `git` on
PATH. The whole run is sandboxed and the demo tree is removed on exit.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger
from tools.screenshots.driver import Sandbox

# ── Sandbox the whole run BEFORE importing anything that resolves a config
#    dir. See `driver/sandbox.py` for why this cannot move into a package
#    `__init__`.
SANDBOX = Sandbox(root=Path("/tmp/grove-screenshots"))
SANDBOX.activate()

from PIL import Image  # noqa: E402
from tools.screenshots.driver.raster import SvgRaster  # noqa: E402
from tools.screenshots.driver.tui import TuiCapture  # noqa: E402
from tools.screenshots.fixture import DemoWorld  # noqa: E402
from tools.screenshots.frame import WindowFramer, save_framed  # noqa: E402
from tools.screenshots.planter import DemoTmux, FleetPlanter, GitTree  # noqa: E402

from grove.core.store import JsonWorkspaceStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "img" / "screenshots"


def _publish(svg_dir: Path) -> int:
    """Rasterize every capture in `svg_dir` and frame it into `OUT_DIR`.

    THE SVG IS AN INTERMEDIATE, NOT AN ARTIFACT. It stays in the throwaway
    sandbox because publishing both forms would leave thirteen files no page
    references, and an unreferenced asset in this repo has a habit of drifting
    until nobody can say whether it still works.
    """
    framer = WindowFramer.build()
    jobs = {svg: OUT_DIR / f"{svg.stem}.png" for svg in sorted(svg_dir.glob("*.svg"))}
    SvgRaster(REPO_ROOT).rasterize(jobs)
    for png in jobs.values():
        with Image.open(png) as handle:
            # RGBA in: the raster is transparent outside the terminal's own
            # rounded corners. `convert` drops that alpha to black, which the
            # framer's larger corner radius then cuts away entirely, so the
            # transparency never survives into the published pixel.
            source = handle.convert("RGB")
        save_framed(framer.frame(source), png)
    return len(jobs)


async def main() -> None:
    # Drop loguru's default DEBUG sink; the run is otherwise noisy.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raster = SvgRaster(REPO_ROOT)
    blocked = raster.preflight()
    if blocked is not None:
        # Fail BEFORE planting a fleet. Discovering that node is missing after
        # the twelve captures have run wastes the whole run and leaves tmux
        # sessions to drain.
        print(blocked, file=sys.stderr)
        raise SystemExit(2)

    DemoTmux.install_quiet_sessions()
    # Drain whatever a previous run left behind BEFORE the reset destroys the
    # store that names it — see `DemoTmux.teardown_recorded`. This run tears its
    # own fleet down in a `finally`, so this only ever fires after a crash.
    stranded = DemoTmux.teardown_recorded(JsonWorkspaceStore(path=SANDBOX.fleet / "state.json"))
    if stranded:
        logger.warning("killed {} tmux session(s) left by a previous run", stranded)
    SANDBOX.reset()

    world = DemoWorld.load()
    svg_dir = SANDBOX.path("tui-svg")
    svg_dir.mkdir(parents=True, exist_ok=True)
    capture = TuiCapture(svg_dir)

    store = JsonWorkspaceStore(path=SANDBOX.fleet / "state.json")
    planter = FleetPlanter(world, store=store)
    fleet = planter.plant(SANDBOX.fleet)
    try:
        await capture.capture_fleet(fleet.primary)
    finally:
        fleet.teardown()

    # The empty state needs a repo with no workspaces of its own.
    empty_root = SANDBOX.path("empty")
    empty_root.mkdir(parents=True, exist_ok=True)
    empty = FleetPlanter(world, store=JsonWorkspaceStore(path=empty_root / "state.json")).manager(
        GitTree.init_repo(empty_root, "myproject").path
    )
    try:
        await capture.capture_empty(empty)
    finally:
        DemoTmux.teardown(empty)

    # Rasterize and frame BEFORE the sandbox goes, since the SVGs live in it.
    count = _publish(svg_dir)

    SANDBOX.discard()
    print(f"wrote {count} framed TUI screenshots to {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
