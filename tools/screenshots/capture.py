"""Capture the Grove documentation screenshots — the TUI and the web dashboard.

Both halves run against ONE seeded copy of the shared synthetic demo world
(`tools/screenshots/fixture/demo.json`): real git repos and worktrees, real tmux
sessions, a year of planted provider-shaped transcripts, and the daemon's warm
derived caches. One sandbox daemon serves both halves, and a production
`next start` in front of it serves the browser.

Run via:

    make docs-capture                 # both halves (what CI runs)
    make docs-screenshots             # TUI only
    make docs-webapp-screenshots      # web only

or directly:

    uv run --group dev python -m tools.screenshots.capture --shots tui,webapp

SEEDING IS THE EXPENSIVE PART, AND IT USED TO BE PAID TWICE. The two halves were
separate entrypoints with separate sandboxes, and each planted and projected the
same year of history: ~17 of a CI capture's ~48 minutes, spent twice to produce
identical input. One seed is safe because nothing the TUI shots do changes the
fleet — they open modals and type into inputs they never submit — so the web
half sees exactly what it would have seen on a fresh seed.

Prerequisites: `tmux`, `bash`, `git` and `node` on PATH; for the web half, a
production webapp build (``make webapp-build`` writes ``webapp/.next``) and the
webapp's Playwright browsers. Output lands in ``docs/img/screenshots/``. The
whole run is sandboxed — throwaway config/state/profile roots, fictional repos,
alternate ports — so nothing touches the user's real daemon, config or repos.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from loguru import logger
from tools.screenshots.driver import Sandbox

# ── Sandbox the whole run BEFORE importing anything that resolves a config
#    dir. See `driver/sandbox.py` for why this cannot move into a package
#    `__init__`.
SANDBOX = Sandbox.rooted(Path("/tmp/grove-shots"))
SANDBOX.activate()

from PIL import Image  # noqa: E402
from tools.screenshots.driver.raster import SvgRaster  # noqa: E402
from tools.screenshots.driver.tui import TuiCapture  # noqa: E402
from tools.screenshots.driver.webapp import (  # noqa: E402
    ClaudeSubscription,
    DemoClaudeQuota,
    WebappCapture,
)
from tools.screenshots.fixture import DemoWorld  # noqa: E402
from tools.screenshots.frame import WindowFramer, save_framed  # noqa: E402
from tools.screenshots.planter import DemoTmux, Fleet, FleetPlanter, GitTree  # noqa: E402

from grove.core import paths  # noqa: E402
from grove.core.manager import WorkspaceManager  # noqa: E402
from grove.core.store import JsonWorkspaceStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "img" / "screenshots"
HALVES = ("tui", "webapp")

REUSE_ENV = "GROVE_SHOTS_REUSE"


class CaptureRun:
    """One seed, one daemon, then whichever halves were asked for."""

    def __init__(self, shots: frozenset[str]) -> None:
        self._shots = shots
        self._daemon = WebappCapture(sandbox=SANDBOX, worktree=REPO_ROOT, out_dir=OUT_DIR)
        self._raster = SvgRaster(REPO_ROOT)

    def preflight(self) -> str | None:
        """The reason this run cannot proceed, checked BEFORE the minutes-long seed."""
        if "tui" in self._shots and (blocked := self._raster.preflight()) is not None:
            return blocked
        if "webapp" in self._shots:
            return self._daemon.preflight()
        return None

    async def run(self) -> list[str]:
        """Capture every requested half; return the names of those that failed.

        A failed half does not stop the other: they were separate CI steps
        before they shared a seed, and one broken selector must not cost the
        other half's pictures.
        """
        reuse = self._prepare()
        # `None` under reuse: the handle exists only to tear the workspaces
        # down, and a reused sandbox is one you are deliberately keeping.
        fleet = None if reuse else self._seed()
        failed: list[str] = []
        try:
            self._daemon.start(webapp="webapp" in self._shots)
            if "tui" in self._shots and not await self._tui(fleet):
                failed.append("tui")
            if "webapp" in self._shots and self._daemon.shoot() != 0:
                print("webapp_shots.mjs failed", file=sys.stderr)
                failed.append("webapp")
        finally:
            self._daemon.stop()
            # KEEP THE SANDBOX WHENEVER REUSE IS ASKED FOR, INCLUDING ON THE
            # RUN THAT SEEDS IT, or the flag can never take effect.
            if os.environ.get(REUSE_ENV) != "1":
                if fleet is not None:
                    fleet.teardown()
                SANDBOX.discard()
        return failed

    @staticmethod
    def _prepare() -> bool:
        """Ready the sandbox, and say whether an existing corpus is being reused.

        ``GROVE_SHOTS_REUSE=1`` keeps the previous run's sandbox and re-serves
        it, so iterating one selector does not pay for the corpus again. It is
        a DEVELOPMENT switch: a published regeneration always runs cold,
        because a reused sandbox carries whatever the last run left in it.
        """
        reuse = os.environ.get(REUSE_ENV) == "1" and SANDBOX.seeded
        DemoTmux.install_quiet_sessions()
        # The capture process asks for quota too (the usage warm-up, the TUI's
        # footer), so it gets the daemon's fake — see `DemoClaudeQuota`.
        DemoClaudeQuota.install()
        if not reuse:
            # DRAIN THE OUTGOING FLEET BEFORE THE RESET: the reset deletes the
            # store that NAMES the previous run's tmux sessions, after which
            # nothing can identify them again.
            stranded = DemoTmux.teardown_recorded(JsonWorkspaceStore())
            if stranded:
                logger.warning("killed {} tmux session(s) left by a previous run", stranded)
            SANDBOX.reset()
            ClaudeSubscription(SANDBOX).seed()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        return reuse

    def _seed(self) -> Fleet:
        """Plant the fleet into the DEFAULT store path, where the daemon finds it.

        The default store rather than a private file, because the daemon is a
        separate process that only reads the default one; a fleet it cannot see
        is an empty Activity wall and an empty web dashboard. `plant` warms the
        derived caches itself, which is why this is minutes rather than seconds,
        and it must happen before the daemon serves anything.
        """
        planter = FleetPlanter(
            DemoWorld.load(), store=JsonWorkspaceStore(), daemon_url=self._daemon.daemon_url
        )
        fleet = planter.plant(SANDBOX.fleet)
        # DISCARD THE QUOTA LEDGER THE WARM JUST WROTE. The ledger is durable
        # across processes on purpose (a TUI, a dashboard and a CLI share one
        # probe budget), so a reading taken while seeding would be served to
        # the daemon as-is, and its cool-off would outlive the run.
        paths.quota_state_path().unlink(missing_ok=True)
        return fleet

    async def _tui(self, fleet: Fleet | None) -> bool:
        """Every TUI shot, rasterized and framed. ``False`` if any failed."""
        svg_dir = SANDBOX.path("tui-svg")
        svg_dir.mkdir(parents=True, exist_ok=True)
        capture = TuiCapture(svg_dir)
        primary = fleet.primary if fleet is not None else self._reused_primary()
        try:
            await capture.capture_fleet(primary)
            await self._capture_empty(capture)
        except Exception as exc:
            print(f"TUI capture failed: {exc}", file=sys.stderr)
            return False
        count = self._publish_tui(svg_dir)
        print(f"wrote {count} framed TUI screenshots to {OUT_DIR}", file=sys.stderr)
        return True

    def _reused_primary(self) -> WorkspaceManager:
        """The primary repo's manager out of a reused sandbox's own store."""
        world = DemoWorld.load()
        planter = FleetPlanter(
            world, store=JsonWorkspaceStore(), daemon_url=self._daemon.daemon_url
        )
        return planter.manager((SANDBOX.fleet / world.repos.primary).resolve())

    @staticmethod
    async def _capture_empty(capture: TuiCapture) -> None:
        """The empty state, which needs a repo with no workspaces of its own."""
        empty_root = SANDBOX.path("empty")
        empty_root.mkdir(parents=True, exist_ok=True)
        planter = FleetPlanter(
            DemoWorld.load(), store=JsonWorkspaceStore(path=empty_root / "state.json")
        )
        empty = planter.manager(GitTree.init_repo(empty_root, "myproject").path)
        try:
            await capture.capture_empty(empty)
        finally:
            DemoTmux.teardown(empty)

    def _publish_tui(self, svg_dir: Path) -> int:
        """Rasterize every TUI capture in `svg_dir` and frame it into `OUT_DIR`.

        THE SVG IS AN INTERMEDIATE, NOT AN ARTIFACT. It stays in the throwaway
        sandbox because publishing both forms would leave a dozen files no page
        references, and an unreferenced asset drifts until nobody can say
        whether it still works.
        """
        framer = WindowFramer.build()
        jobs = {svg: OUT_DIR / f"{svg.stem}.png" for svg in sorted(svg_dir.glob("*.svg"))}
        self._raster.rasterize(jobs)
        for png in jobs.values():
            with Image.open(png) as handle:
                # RGBA in: the raster is transparent outside the terminal's own
                # rounded corners. `convert` drops that alpha to black, which
                # the framer's larger corner radius then cuts away entirely.
                source = handle.convert("RGB")
            save_framed(framer.frame(source), png)
        return len(jobs)


def _shots(value: str) -> frozenset[str]:
    chosen = frozenset(part.strip() for part in value.split(",") if part.strip())
    if value.strip() == "all":
        return frozenset(HALVES)
    unknown = chosen - set(HALVES)
    if not chosen or unknown:
        raise argparse.ArgumentTypeError(f"expected 'all' or a subset of {','.join(HALVES)}")
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shots", type=_shots, default=frozenset(HALVES))
    args = parser.parse_args()

    # Drop loguru's default DEBUG sink; the run is otherwise noisy.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    run = CaptureRun(args.shots)
    blocked = run.preflight()
    if blocked is not None:
        print(blocked, file=sys.stderr)
        return 2
    failed = asyncio.run(run.run())
    if failed:
        print(f"capture failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
