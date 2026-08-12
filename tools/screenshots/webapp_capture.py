"""Capture the Grove web dashboard documentation screenshots.

Stands up the SAME synthetic demo world the TUI captures use
(`tools/screenshots/fixture/demo.json`) behind a sandbox daemon and a production
`next start`, then drives a headless Playwright browser
(`tools/screenshots/driver/webapp_shots.mjs`) through the real pairing flow.

Run via:

    make docs-webapp-screenshots

or directly:

    uv run python -m tools.screenshots.webapp_capture

Prerequisites: a production webapp build must exist (``make webapp-build``
writes ``webapp/.next``), and ``node`` plus the webapp's ``node_modules``
(including Playwright's browsers) must be present.

The whole run is sandboxed — throwaway config/state/profile roots, fictional
repos under ``/tmp``, alternate ports — so nothing touches the user's real
daemon, config or repos. The shared host tmux server is left untouched apart
from the fleet's own sessions, which are torn down on exit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger
from tools.screenshots.driver import Sandbox

# ── Sandbox BEFORE importing grove so every resolved config dir lands in the
#    throwaway tree. See `driver/sandbox.py` for why this cannot move into a
#    package `__init__`.
SANDBOX = Sandbox(root=Path("/tmp/grove-webapp-shots"))
SANDBOX.activate()

from tools.screenshots.driver.webapp import ClaudeSubscription, WebappCapture  # noqa: E402
from tools.screenshots.fixture import DemoWorld  # noqa: E402
from tools.screenshots.planter import DemoTmux, Fleet, FleetPlanter  # noqa: E402

from grove.core import paths  # noqa: E402
from grove.core.store import JsonWorkspaceStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "docs" / "img" / "screenshots"

REUSE_ENV = "GROVE_SHOTS_REUSE"


def _prepare() -> bool:
    """Ready the sandbox, and say whether an existing corpus is being reused.

    SEEDING AND CAPTURING ARE SEPARABLE, AND ITERATING THE BROWSER MUST NOT PAY
    FOR THE CORPUS. A full seed plants a year of history and spends minutes
    projecting it, so re-running to test one selector costs the same again and
    tempts you to guess instead of measure. ``GROVE_SHOTS_REUSE=1`` keeps the
    previous run's sandbox and re-serves it.

    It is a DEVELOPMENT switch. A published regeneration always runs cold,
    because a reused sandbox carries whatever the last run left in it and can no
    longer claim to be reproducible.
    """
    reuse = os.environ.get(REUSE_ENV) == "1" and SANDBOX.seeded
    DemoTmux.install_quiet_sessions()
    if not reuse:
        # DRAIN THE OUTGOING FLEET BEFORE THE RESET, because the reset is what
        # makes the leak permanent: it deletes the store that NAMES the previous
        # run's tmux sessions, after which nothing can identify them again. A
        # reuse run deliberately leaves its fleet up, so this is the only place
        # those sessions are still recoverable.
        stranded = DemoTmux.teardown_recorded(JsonWorkspaceStore())
        if stranded:
            logger.warning("killed {} tmux session(s) left by a previous run", stranded)
        SANDBOX.reset()
        ClaudeSubscription(SANDBOX).seed()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return reuse


def _seed() -> Fleet:
    """Plant the fleet into the DEFAULT store path, where the daemon finds it.

    `FleetPlanter.plant` warms the usage cache itself, which is why this is
    minutes rather than seconds. It has to happen HERE, before the daemon serves
    anything, and never as an HTTP refresh from the browser: at a full year of
    history the projection outlives the Next BFF's own fetch timeout, so that
    call died at exactly 300s with a 502 naming the daemon — which points at the
    wrong process entirely.
    """
    fleet = FleetPlanter(DemoWorld.load(), store=JsonWorkspaceStore()).plant(SANDBOX.fleet)

    # DISCARD THE QUOTA LEDGER THE WARM JUST WROTE. `service.refresh()` probes
    # quota as well as projecting, and that probe runs HERE, in a process the
    # daemon wrapper's Claude patch has not touched — so the real provider is
    # asked, fails, and records `rate_limited` plus a cool-off deadline in the
    # cross-process ledger. The daemon then honours that cool-off and serves the
    # failure without ever calling the patched fetch, so the captured card read
    # "Rate limited" while the fixture underneath it was perfectly good. The
    # ledger is deliberately durable across processes (it exists so a TUI, a
    # dashboard and a CLI share one probe budget), which is exactly why seeding
    # can poison the run that follows.
    paths.quota_state_path().unlink(missing_ok=True)
    return fleet


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    capture = WebappCapture(sandbox=SANDBOX, worktree=REPO_ROOT, out_dir=OUT_DIR)
    blocked = capture.preflight()
    if blocked is not None:
        print(blocked, file=sys.stderr)
        return 2

    reuse = _prepare()
    # `None` under reuse: the fleet handle exists only to tear the workspaces
    # down at the end, and a reused sandbox is one you are deliberately keeping.
    fleet = None if reuse else _seed()

    try:
        capture.start()
        code = capture.shoot()
        if code != 0:
            print("webapp_shots.mjs failed", file=sys.stderr)
            return code
    finally:
        capture.stop()
        # KEEP THE SANDBOX WHENEVER REUSE IS ASKED FOR, INCLUDING ON THE RUN
        # THAT SEEDS IT. Tearing down here on the seeding run means the flag can
        # never take effect — the next run finds no corpus and pays for a fresh
        # one, which is the whole cost it exists to avoid.
        if os.environ.get(REUSE_ENV) != "1":
            if fleet is not None:
                fleet.teardown()
            SANDBOX.discard()

    print(f"wrote web screenshots to {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
