"""Capture the Grove web dashboard documentation screenshots.

This stands up the SAME synthetic demo fleet the TUI captures use
(`tools/screenshots/_fleet.py`) behind a sandbox daemon and a production
`next start`, then drives a headless Playwright browser
(`tools/screenshots/webapp_shots.mjs`) through the real pairing flow and
captures the dashboard, the workspace IDE shell, the sessions drill-down,
and the activity wall.

Run via:

    make docs-webapp-screenshots

or directly:

    uv run python -m tools.screenshots.webapp_capture

Prerequisites: a production webapp build must exist (``make webapp-build``
writes ``webapp/.next``), and ``node`` plus the webapp's ``node_modules``
(including Playwright's browsers) must be present.

The whole run is sandboxed. ``XDG_CONFIG_HOME`` / ``XDG_STATE_HOME`` /
``CLAUDE_CONFIG_DIR`` point at a throwaway tree, the repos are fictional
and live under ``/tmp``, and the daemon + webapp listen on alternate
ports, so nothing touches the user's real daemon, config, or repos. The
shared host tmux server is left untouched apart from the fleet's own
sessions, which are torn down on exit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Sandbox BEFORE importing grove so every resolved config dir lands in
#    the throwaway tree (platformdirs reads these env vars at call time).
SANDBOX = Path("/tmp/grove-webapp-shots")
os.environ["XDG_CONFIG_HOME"] = str(SANDBOX / "config")
os.environ["XDG_STATE_HOME"] = str(SANDBOX / "state")
os.environ["CLAUDE_CONFIG_DIR"] = str(SANDBOX / "claude")

from loguru import logger  # noqa: E402
from tools.screenshots import _fleet  # noqa: E402

from grove.core.store import JsonWorkspaceStore  # noqa: E402

WORKTREE = _fleet.REPO_ROOT
WEBAPP_DIR = WORKTREE / "webapp"
OUT_DIR = WORKTREE / "docs" / "img" / "screenshots"
SHOTS_MJS = WORKTREE / "tools" / "screenshots" / "webapp_shots.mjs"

DAEMON_PORT = 7533
WEBAPP_PORT = 3344
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"
WEBAPP_URL = f"http://127.0.0.1:{WEBAPP_PORT}"


def _wait_http(url: str, *, timeout: float, label: str) -> None:
    """Block until ``url`` answers any HTTP status, or raise on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # a 3xx/4xx still means the server is up
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    raise TimeoutError(f"{label} did not come up at {url} within {timeout}s")


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    if not (WEBAPP_DIR / ".next").is_dir():
        print(
            "webapp is not built — run `make webapp-build` first (writes webapp/.next).",
            file=sys.stderr,
        )
        return 2

    node = shutil.which("node")
    if node is None:
        print("node not found on PATH", file=sys.stderr)
        return 2

    _fleet.install_quiet_tmux()
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Seed into the default store path (under the sandbox XDG_STATE_HOME) so
    # the daemon, which builds a default JsonWorkspaceStore(), serves the
    # same fleet.
    fleet = _fleet.seed_fleet(SANDBOX / "fleet", JsonWorkspaceStore())

    daemon_proc: subprocess.Popen[bytes] | None = None
    webapp_proc: subprocess.Popen[bytes] | None = None
    daemon_log = (SANDBOX / "daemon.log").open("wb")
    webapp_log = (SANDBOX / "webapp.log").open("wb")
    try:
        daemon_proc = subprocess.Popen(
            ["uv", "run", "grove", "daemon", "serve", "--port", str(DAEMON_PORT)],
            cwd=WORKTREE,
            env=os.environ.copy(),
            stdout=daemon_log,
            stderr=subprocess.STDOUT,
        )
        _wait_http(f"{DAEMON_URL}/healthz", timeout=40, label="daemon")
        logger.warning("daemon up on {}", DAEMON_URL)

        webapp_env = os.environ.copy()
        webapp_env["GROVE_DAEMON_URL"] = DAEMON_URL
        webapp_proc = subprocess.Popen(
            [str(WEBAPP_DIR / "node_modules" / ".bin" / "next"), "start", "-p", str(WEBAPP_PORT)],
            cwd=WEBAPP_DIR,
            env=webapp_env,
            stdout=webapp_log,
            stderr=subprocess.STDOUT,
        )
        _wait_http(WEBAPP_URL, timeout=60, label="webapp")
        logger.warning("webapp up on {}", WEBAPP_URL)

        shots_env = os.environ.copy()
        shots_env.update(
            {
                "BASE": WEBAPP_URL,
                "OUT": str(OUT_DIR),
                "WORKTREE": str(WORKTREE),
                "STORAGE_STATE": str(SANDBOX / "storage-state.json"),
            }
        )
        result = subprocess.run(  # noqa: PLW1510 — we check returncode below
            [node, str(SHOTS_MJS)],
            cwd=WEBAPP_DIR,
            env=shots_env,
        )
        if result.returncode != 0:
            print("webapp_shots.mjs failed", file=sys.stderr)
            return result.returncode
    finally:
        for proc in (webapp_proc, daemon_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        daemon_log.close()
        webapp_log.close()
        _fleet.teardown(*fleet.managers())
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print(f"wrote web screenshots to {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
