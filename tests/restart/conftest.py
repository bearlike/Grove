"""A real ``grove daemon serve`` child that a test can SIGKILL and start again.

Every row of the restart-survival suite is shaped *do X → SIGKILL → start →
assert*, so the daemon has to be a separate process: an in-process app cannot be
killed without killing the test. A child loads no conftest and honours no
monkeypatch, so its isolation is carried entirely by its environment and its
config file (tests/CLAUDE.md, the ``test_client_http.py`` and
``test_local_transport.py`` lessons): private ``XDG_STATE_HOME``,
``XDG_CONFIG_HOME``, ``TMUX_TMPDIR`` and ``HOME``, and ``container.enabled=false``
pinned in the config the child reads.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from grove.core.auth import SessionStore
from grove.core.store import JsonWorkspaceStore
from grove.core.watches.log import WatchLog
from tests.native_fake import fake_provider_source


@dataclass(slots=True)
class DaemonChild:
    """One isolated daemon process and the private paths it reads.

    The paths are exposed so a test can seed or inspect durable state from the
    pytest process, exactly as a second Grove process (the CLI) would.
    """

    root: Path
    env: dict[str, str]
    proc: subprocess.Popen[str] | None = None
    port: int | None = None
    #: Bind this port on every start instead of an ephemeral one. A native worker
    #: reads the daemon's URL once at launch, so it can only reconnect to a
    #: restarted daemon that comes back where it was.
    fixed_port: int | None = None
    token: str = ""
    _spawned: list[int] = field(default_factory=list)

    @property
    def state_dir(self) -> Path:
        return Path(self.env["XDG_STATE_HOME"]) / "grove"

    @property
    def config_dir(self) -> Path:
        return Path(self.env["XDG_CONFIG_HOME"]) / "grove"

    def store(self) -> JsonWorkspaceStore:
        """The workspace store file the child reads."""
        return JsonWorkspaceStore(path=self.state_dir / "state.json")

    def watch_log(self) -> WatchLog:
        """The durable watch registry the child reads."""
        return WatchLog(self.state_dir / "watches.json")

    def auth(self) -> SessionStore:
        """The bearer store the child validates against."""
        return SessionStore(path=self.config_dir / "auth.json")

    def start(self) -> None:
        """Spawn the daemon and wait until it serves an authenticated request."""
        assert self.proc is None, "already running"
        self.proc = subprocess.Popen(
            [
                *(sys.executable, "-m", "grove.cli", "daemon", "serve"),
                *("--host", "127.0.0.1", "--port", str(self.fixed_port or 0), "--print-port"),
            ],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=(self.root / "daemon.log").open("a"),
            text=True,
        )
        self._spawned.append(self.proc.pid)
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        assert line, f"daemon exited before printing a port; see {self.root / 'daemon.log'}"
        self.port = int(line)
        if not self.token:
            self.token = self.auth().mint_local("restart-suite")
        # An ephemeral port is announced at bind, a fixed one BEFORE bind; either
        # way /whoami answers only once startup (watch re-arm, reconcile) has run,
        # so a refused connection here means "not listening yet", not "dead".
        deadline = time.monotonic() + 60
        while True:
            try:
                self.get("/whoami", timeout=None)
                return
            except httpx.ConnectError:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    def get(self, path: str, *, timeout: float | None = 30.0) -> httpx.Response:
        """An authenticated GET against the live child."""
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, body: object) -> httpx.Response:
        """An authenticated POST against the live child."""
        return self._request("POST", path, json=body)

    def _request(
        self, method: str, path: str, *, timeout: float | None = 30.0, **kw: object
    ) -> httpx.Response:
        assert self.port is not None, "daemon not started"
        return httpx.request(
            method,
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout,
            **kw,  # type: ignore[arg-type]
        )

    def kill9(self) -> None:
        """SIGKILL the daemon: no lifespan shutdown, no flush, no goodbye."""
        assert self.proc is not None, "not running"
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait()
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc = None
        self.port = None

    def stop(self) -> None:
        """Teardown: kill only a process this fixture itself spawned."""
        if self.proc is None:
            return
        # A destructive teardown verifies its target at the moment it acts
        # (tests/CLAUDE.md): never signal a pid this fixture did not spawn.
        assert self.proc.pid in self._spawned
        self.kill9()


def kill9_and_restart(daemon: DaemonChild) -> None:
    """The one restart every row of this suite performs."""
    daemon.kill9()
    daemon.start()


def _child_env(root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GROVE_", "TMUX"))}
    env.update(
        XDG_STATE_HOME=str(root / "xdg-state"),
        XDG_CONFIG_HOME=str(root / "xdg-config"),
        TMUX_TMPDIR=str(root / "tmux"),
        # No developer `~/.tmux.conf` or plugin manager in the child's server.
        HOME=str(root / "home"),
        CLAUDE_CONFIG_DIR=str(root / "claude"),
    )
    for key in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "TMUX_TMPDIR", "HOME"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


@pytest.fixture
def daemon_child(tmp_path: Path, tmp_repo: Path) -> Iterator[DaemonChild]:
    """A started daemon on private state, killed (and only it) at teardown."""
    env = _child_env(tmp_path)
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "grove"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                # The default runtime is a container; only config reaches a child.
                "container": {"enabled": False},
                "projects": [str(tmp_repo)],
                "agents": [
                    {"name": "claude", "command": "sh -c 'sleep 600'", "native": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    daemon = DaemonChild(root=tmp_path, env=env)
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


@pytest.fixture
def native_daemon_child(tmp_path: Path, tmp_repo: Path) -> Iterator[tuple[DaemonChild, Path]]:
    """A daemon on a FIXED port whose `claude` is a native fake provider; and that provider's log.

    The provider is :mod:`tests.native_fake`, the same program the native
    integration suite drives, so the two cannot drift on what a provider says.
    """
    env = _child_env(tmp_path)
    port = _free_port()
    provider_log = tmp_path / "provider.json"
    src = str(Path(__file__).resolve().parents[2] / "src")
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "grove"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "container": {"enabled": False},
                "hooks": {"enabled": False},
                "telemetry": {"enabled": False},
                "reaper": {"enabled": False},
                "projects": [str(tmp_repo)],
                "daemon": {"mailbox_url": f"http://127.0.0.1:{port}"},
                "agents": [
                    {
                        "name": "claude",
                        "kind": "claude_code",
                        "native": True,
                        "command": " ".join(
                            [sys.executable, "-u", "-c", shlex.quote(fake_provider_source())]
                        ),
                        "env": {"FAKE_PROVIDER_LOG": str(provider_log), "PYTHONPATH": src},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env["PYTHONPATH"] = src
    daemon = DaemonChild(root=tmp_path, env=env, fixed_port=port)
    daemon.start()
    try:
        yield daemon, provider_log
    finally:
        daemon.stop()
        _kill_private_tmux(env)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _kill_private_tmux(env: dict[str, str]) -> None:
    """End the tmux servers this test's daemon started, and only those.

    Every one sits under the child's private ``TMUX_TMPDIR``; a socket path is
    named explicitly with ``-S`` so no inherited ``$TMUX`` can redirect it.
    """
    root = Path(env["TMUX_TMPDIR"])
    for sock in root.glob("tmux-*/*"):
        if sock.is_socket():
            subprocess.run(
                ["tmux", "-S", str(sock), "kill-server"], capture_output=True, check=False
            )
