"""Per-backend transport — owns the subprocess (local) or SSH connection (remote).

``Transport`` is a Protocol; concrete impls are ``LocalTransport`` (spawns
a child ``grove daemon serve --print-port`` and reads the picked port from
stdout) and ``SshTransport`` (Task 13). Both expose the same surface so
``GroveClient`` is transport-agnostic.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Protocol

import asyncssh
from loguru import logger

from grove.client.backend import BackendConfig
from grove.client.errors import TransportError

if TYPE_CHECKING:
    # Task 14 lands grove.client.attach; until then mypy can't resolve it.
    from grove.client.attach import AttachSession  # type: ignore[import-untyped,import-not-found,unused-ignore] # noqa: I001


class Transport(Protocol):
    """Owns the connection lifecycle for one backend."""

    async def start(self) -> None: ...

    @property
    def http_url(self) -> str: ...

    async def open_attach(self, tmux_session: str) -> AttachSession: ...

    async def close(self) -> None: ...


class LocalTransport:
    """Spawns and supervises a child ``grove daemon serve`` process."""

    _STARTUP_TIMEOUT_S = 10.0
    _GRACE_S = 15.0

    def __init__(self, config: BackendConfig) -> None:
        if config.ssh_target is not None:
            raise ValueError("LocalTransport requires ssh_target=None")
        self._config = config
        self._proc: subprocess.Popen[str] | None = None
        self._port: int | None = None

    @property
    def http_url(self) -> str:
        if self._port is None:
            raise TransportError("LocalTransport not started")
        return f"http://127.0.0.1:{self._port}"

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "grove.cli",
                "daemon",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--print-port",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            self._port = await asyncio.wait_for(
                asyncio.to_thread(self._read_port),
                timeout=self._STARTUP_TIMEOUT_S,
            )
        except TimeoutError as exc:
            await self.close()
            raise TransportError("local daemon failed to print port within timeout") from exc

    def _read_port(self) -> int:
        # stdout is a PIPE we set up; assert is defensive.
        assert self._proc is not None and self._proc.stdout is not None
        line = self._proc.stdout.readline().strip()
        if not line:
            raise TransportError("local daemon exited before printing port")
        return int(line)

    async def open_attach(self, tmux_session: str) -> AttachSession:
        # Construction-only: callers wire ``on_output`` first, THEN call
        # ``attach.start()``. Calling start() here would race the PTY
        # reader pump against any subsequent on_output registration, so
        # the first chunks would be silently dropped.
        from grove.client.attach import LocalAttach  # noqa: PLC0415

        return LocalAttach(tmux_session)

    async def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=self._GRACE_S)
        except TimeoutError:
            logger.warning("local daemon did not exit in {}s; killing", self._GRACE_S)
            proc.kill()
            await asyncio.to_thread(proc.wait)


class SshTransport:
    """Opens an asyncssh connection and forwards a local port to the remote daemon.

    The same ``SSHClientConnection`` is reused by ``open_attach()`` for
    interactive ``tmux attach`` — one SSH connection, two jobs.
    """

    def __init__(
        self,
        config: BackendConfig,
        *,
        known_hosts: Any = ...,
    ) -> None:
        if config.ssh_target is None:
            raise ValueError("SshTransport requires ssh_target set")
        self._config = config
        self._known_hosts = known_hosts
        self._conn: asyncssh.SSHClientConnection | None = None
        self._listener: asyncssh.SSHListener | None = None
        self._port: int | None = None

    @property
    def http_url(self) -> str:
        if self._port is None:
            raise TransportError("SshTransport not started")
        return f"http://127.0.0.1:{self._port}"

    @staticmethod
    def _parse_target(target: str) -> dict[str, Any]:
        """Parse ``[user[:pw]@]host[:port]`` into asyncssh.connect kwargs.

        asyncssh.connect's first positional arg accepts only ``host`` — it does
        not split off ``user:pw@host:port`` like the OpenSSH CLI does. We do
        the parse ourselves so test fixtures can pass passwords inline; in
        production callers use ssh-agent / keys and pw is absent.
        """
        userinfo: str | None = None
        rest = target
        if "@" in target:
            userinfo, rest = target.rsplit("@", 1)
        host = rest
        port: int | None = None
        if ":" in rest:
            host, port_str = rest.rsplit(":", 1)
            port = int(port_str)
        kwargs: dict[str, Any] = {"host": host}
        if port is not None:
            kwargs["port"] = port
        if userinfo is not None:
            if ":" in userinfo:
                user, pw = userinfo.split(":", 1)
                kwargs["username"] = user
                kwargs["password"] = pw
            else:
                kwargs["username"] = userinfo
        return kwargs

    async def start(self) -> None:
        if self._conn is not None:
            return
        connect_kwargs: dict[str, Any] = self._parse_target(
            self._config.ssh_target  # type: ignore[arg-type]  # guarded in __init__
        )
        if self._known_hosts is not ...:
            connect_kwargs["known_hosts"] = self._known_hosts
        try:
            self._conn = await asyncssh.connect(**connect_kwargs)
        except (asyncssh.Error, OSError) as exc:
            raise TransportError(f"ssh connect failed: {exc}") from exc

        try:
            self._listener = await self._conn.forward_local_port(
                "", 0, "127.0.0.1", self._config.daemon_port
            )
        except asyncssh.Error as exc:
            await self.close()
            raise TransportError(f"ssh forward_local_port failed: {exc}") from exc

        self._port = self._listener.get_port()

    async def open_attach(self, tmux_session: str) -> AttachSession:
        # Construction-only: same contract as ``LocalTransport.open_attach``.
        # Caller wires ``on_output`` then ``await attach.start()``; the
        # asyncssh reader loop only begins once start() runs.
        from grove.client.attach import SshAttach  # noqa: PLC0415

        if self._conn is None:
            raise TransportError("SshTransport not started")
        return SshAttach(self._conn, tmux_session)

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None
        self._port = None
