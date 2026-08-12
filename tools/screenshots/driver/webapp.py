"""Stand up a sandbox daemon plus a production `next start`, and drive a browser.

The daemon is launched through a ``python -c`` wrapper rather than
``grove daemon serve`` because two of its answers would otherwise bake real or
unreachable facts into committed PNGs:

* ``GET /whoami`` reads ``socket.gethostname()`` / ``getpass.getuser()`` at
  request time (`grove.daemon.app._build_whoami`), so an unpatched run stamps
  this machine's real hostname and username onto the rail footer's account menu.
* ``ClaudeQuotaProvider.collect`` spends a live OAuth request against
  ``api.anthropic.com``, which an offline sandbox can never answer.

Only those are faked, and only at the narrowest point. Everything upstream of
the quota call stays real: `ClaudeSubscription` below plants an ordinary
(fictional) ``.credentials.json`` under the sandbox profile so
``_read_credential``/``describe()`` and the plan-tier parsing all exercise their
real code paths. The Codex window needs no fake at all — its provider tail-reads
a local rollout the demo fleet already wrote.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Final

from loguru import logger

from grove.core import paths

from .sandbox import Sandbox

DAEMON_PORT: Final[int] = 7533
WEBAPP_PORT: Final[int] = 3344
SHOTS_MJS: Final[Path] = Path(__file__).with_name("webapp_shots.mjs")


def _daemon_wrapper(port: int) -> str:
    """The in-process patches applied BEFORE `grove.daemon._asgi` is imported."""
    return f"""
import socket, getpass
socket.gethostname = lambda: "grove-demo"
getpass.getuser = lambda: "grove"

from datetime import UTC, datetime, timedelta
from grove.core.usage.quota.claude import ClaudeQuotaProvider

def _fake_claude_fetch(self, token):
    now = datetime.now(UTC)
    payload = {{
        "limits": [
            {{
                "kind": "5h_limit",
                "group": "session",
                "percent": 42.0,
                "resets_at": (now + timedelta(hours=3, minutes=12)).isoformat(),
            }},
            {{
                "kind": "7d_limit",
                "group": "weekly",
                "percent": 18.0,
                "resets_at": (now + timedelta(days=4, hours=6)).isoformat(),
            }},
        ]
    }}
    return 200, payload, None

ClaudeQuotaProvider._fetch = _fake_claude_fetch

import uvicorn
uvicorn.run(
    "grove.daemon._asgi:app",
    host="127.0.0.1",
    port={port},
    log_config=None,
    lifespan="on",
)
"""


class ClaudeSubscription:
    """Plant a synthetic Claude Code OAuth login and select both quota accounts.

    This seeds only what a real profile root would already hold BEFORE the
    network call the wrapper fakes: a ``.credentials.json`` the real
    `_read_credential` reads unmodified, so expiry and plan-tier parsing
    exercise their real code paths, plus a sandboxed user config selecting the
    fake Claude root and the demo fleet's real Codex home
    (``usage.quota.profiles`` is empty by default and opt-in per profile, so
    without this neither account is ever asked for, patched fetch or not).
    """

    TOKEN_STEM = "sk-ant-{kind}-grove-demo-" + "0" * 40

    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    def seed(self) -> None:
        claude_root = self._sandbox.path("claude")
        claude_root.mkdir(parents=True, exist_ok=True)
        expires_at_ms = int((datetime.now(UTC) + timedelta(days=180)).timestamp() * 1000)
        credentials = {
            "claudeAiOauth": {
                "accessToken": self.TOKEN_STEM.format(kind="oat"),
                "refreshToken": self.TOKEN_STEM.format(kind="ort"),
                "expiresAt": expires_at_ms,
                "scopes": ["user:inference"],
                # Read by `ClaudeQuotaProvider.parse_subscription` with no
                # network call at all — the plan tier is local evidence for both
                # providers.
                "subscriptionType": "max",
                "rateLimitTier": "default_claude_max_20x",
            }
        }
        (claude_root / ".credentials.json").write_text(json.dumps(credentials), encoding="utf-8")

        config_path = paths.user_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "usage": {
                        "quota": {
                            "profiles": {
                                "claude_code": [str(claude_root)],
                                "codex": [str(self._sandbox.path("codex"))],
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )


@dataclass(slots=True)
class _Service:
    """One long-running child of the capture, and the log it writes to.

    SIGNAL THE PROCESS GROUP, NOT THE PROCESS. Both children fork their own
    server — `uv run python -c …` execs an interpreter and `next start` forks a
    Next server — so terminating the wrapper alone leaves the real listener
    holding the port, and the NEXT run dies at `EADDRINUSE` in a log nobody
    reads, several minutes after the seed it just paid for. Each is spawned into
    its own session (`start_new_session`), which makes its pid the group id.
    """

    name: str
    process: subprocess.Popen[bytes]
    log: IO[bytes]

    def stop(self) -> None:
        if self.process.poll() is None:
            self._signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
        self.log.close()

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except (ProcessLookupError, PermissionError):
            self.process.send_signal(sig)


class WebappCapture:
    """The daemon, the web server and the browser run, with one teardown.

    Ports are alternates so a capture never collides with the developer's own
    daemon or dev server.
    """

    def __init__(self, *, sandbox: Sandbox, worktree: Path, out_dir: Path) -> None:
        self._sandbox = sandbox
        self._worktree = worktree
        self._webapp_dir = worktree / "webapp"
        self._out_dir = out_dir
        self._services: list[_Service] = []

    @property
    def daemon_url(self) -> str:
        return f"http://127.0.0.1:{DAEMON_PORT}"

    @property
    def webapp_url(self) -> str:
        return f"http://127.0.0.1:{WEBAPP_PORT}"

    def preflight(self) -> str | None:
        """The reason this run cannot proceed, or ``None``."""
        if not (self._webapp_dir / ".next").is_dir():
            return "webapp is not built — run `make webapp-build` first (writes webapp/.next)."
        if shutil.which("node") is None:
            return "node not found on PATH"
        return None

    def start(self) -> None:
        self._spawn(
            "daemon",
            ["uv", "run", "python", "-c", _daemon_wrapper(DAEMON_PORT)],
            cwd=self._worktree,
            env=os.environ.copy(),
        )
        self._wait_http(f"{self.daemon_url}/healthz", timeout=40, label="daemon")
        logger.warning("daemon up on {}", self.daemon_url)

        env = os.environ.copy()
        env["GROVE_DAEMON_URL"] = self.daemon_url
        self._spawn(
            "webapp",
            [
                str(self._webapp_dir / "node_modules" / ".bin" / "next"),
                "start",
                "-p",
                str(WEBAPP_PORT),
            ],
            cwd=self._webapp_dir,
            env=env,
        )
        self._wait_http(self.webapp_url, timeout=60, label="webapp")
        logger.warning("webapp up on {}", self.webapp_url)

    def shoot(self) -> int:
        """Run the browser script, returning its exit code."""
        node = shutil.which("node")
        assert node is not None  # preflight() proved this
        env = os.environ.copy()
        env.update(
            {
                "BASE": self.webapp_url,
                "OUT": str(self._out_dir),
                "WORKTREE": str(self._worktree),
                "STORAGE_STATE": str(self._sandbox.path("storage-state.json")),
            }
        )
        result = subprocess.run(  # noqa: PLW1510 — the caller checks the code
            [node, str(SHOTS_MJS)], cwd=self._webapp_dir, env=env
        )
        return result.returncode

    def stop(self) -> None:
        for service in reversed(self._services):
            service.stop()
        self._services.clear()

    def _spawn(self, name: str, argv: list[str], *, cwd: Path, env: dict[str, str]) -> None:
        log = (self._sandbox.path(f"{name}.log")).open("wb")
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._services.append(_Service(name=name, process=process, log=log))

    @staticmethod
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
