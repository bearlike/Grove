"""AttachSession — bidirectional terminal session bridged to xterm.js.

Two implementations share one Protocol surface so a client's xterm
bridge is transport-agnostic:

* ``LocalAttach`` — stdlib ``pty.fork`` + ``asyncio.add_reader``. Used
  when ``BackendConfig.ssh_target`` is ``None``. ``with_command`` is
  the test-only constructor; production callers always run
  ``tmux attach -t <session>``.
* ``SshAttach`` — ``asyncssh.SSHClientProcess`` over an existing
  ``SSHClientConnection``. The transport's connection is reused so
  HTTP traffic and the interactive attach share one TCP session.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import struct
import sys
from collections.abc import Callable
from typing import Protocol

import asyncssh
from loguru import logger

# ``LocalAttach`` is POSIX-only — it forks a PTY via ``pty.fork`` and drives
# the slave fd through ``fcntl.ioctl(TIOCSWINSZ)``. Neither stdlib module
# exists on Windows. Per CLAUDE.md ("tmux on Windows requires WSL2 — surface
# a clear error rather than half-supporting Windows-native") the module
# imports succeed on every platform but ``LocalAttach.start`` raises a
# typed error on Windows; the SshAttach class is platform-independent.
if sys.platform != "win32":
    import fcntl
    import pty
    import termios


class AttachSession(Protocol):
    """Bidirectional terminal session contract."""

    async def start(self) -> None: ...
    async def feed_input(self, data: bytes) -> None: ...
    async def resize(self, cols: int, rows: int) -> None: ...
    def on_output(self, callback: Callable[[bytes], None]) -> None: ...
    async def close(self) -> None: ...


class LocalAttach:
    """PTY-backed attach session for local backends."""

    _READ_CHUNK = 4096

    def __init__(self, tmux_session: str) -> None:
        """Default constructor — runs ``tmux attach -t <session>``."""
        self._command: list[str] = ["tmux", "attach", "-t", tmux_session]
        self._master_fd: int | None = None
        self._pid: int | None = None
        self._callback: Callable[[bytes], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def with_command(cls, command: list[str]) -> LocalAttach:
        """Test/alternate constructor — run any command in the PTY.

        Production code uses the default constructor (always tmux attach).
        Tests use a deterministic ``cat`` for byte-level assertions.
        """
        instance = cls.__new__(cls)
        instance._command = command
        instance._master_fd = None
        instance._pid = None
        instance._callback = None
        instance._loop = None
        return instance

    def on_output(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback

    async def start(self) -> None:
        if sys.platform == "win32":
            # Windows lacks pty / fcntl / termios; users on Windows attach
            # via WSL or use a remote backend (SshAttach works fine on any
            # platform because asyncssh handles its own PTY abstraction).
            raise NotImplementedError(
                "LocalAttach requires POSIX (use WSL on Windows, or attach "
                "to a remote backend via SSH)"
            )
        if self._master_fd is not None:
            return
        pid, master_fd = pty.fork()
        if pid == 0:  # child
            os.execvp(self._command[0], self._command)
        self._pid = pid
        self._master_fd = master_fd
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(master_fd, self._on_readable)

    def _on_readable(self) -> None:
        # Bound to the event loop reader; never raises into the loop.
        if self._master_fd is None:
            return
        try:
            data = os.read(self._master_fd, self._READ_CHUNK)
        except OSError:
            data = b""
        if not data:
            # PTY closed — drop the reader and let close() finish cleanup.
            if self._loop is not None and self._master_fd is not None:
                with contextlib.suppress(ValueError):
                    self._loop.remove_reader(self._master_fd)
            return
        if self._callback is not None:
            self._callback(data)

    async def feed_input(self, data: bytes) -> None:
        if self._master_fd is None:
            raise RuntimeError("LocalAttach not started")
        # ``os.write`` on a PTY master can block indefinitely when the
        # child process isn't draining (slow agent, tmux paused, …).
        # Wrapping in ``asyncio.to_thread`` keeps PTY backpressure off
        # the client event loop so the rest of the UI stays responsive.
        await asyncio.to_thread(os.write, self._master_fd, data)

    async def resize(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            raise RuntimeError("LocalAttach not started")
        # ``fcntl.ioctl(TIOCSWINSZ)`` is a synchronous kernel call that
        # does not block on user-space I/O — it returns in well under a
        # microsecond. Wrapping in ``asyncio.to_thread`` here would cost
        # more (thread handoff, GIL re-acquire) than running it inline.
        # struct: rows, cols, xpix, ypix — termios.TIOCSWINSZ contract.
        fcntl.ioctl(
            self._master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, 0, 0),
        )

    async def close(self) -> None:
        if self._master_fd is not None and self._loop is not None:
            with contextlib.suppress(ValueError):
                self._loop.remove_reader(self._master_fd)
        if self._pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self._pid, signal.SIGHUP)
                await asyncio.to_thread(os.waitpid, self._pid, 0)
        if self._master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._master_fd)
        self._master_fd = None
        self._pid = None


class SshAttach:
    """Attach session driven via asyncssh process over a shared connection."""

    def __init__(
        self,
        conn: asyncssh.SSHClientConnection,
        tmux_session: str,
        *,
        _command_override: str | None = None,
    ) -> None:
        self._conn = conn
        # ``_command_override`` is for tests only — production callers always
        # attach to a tmux session that the daemon just confirmed exists.
        self._command = _command_override or f"tmux attach -t {tmux_session}"
        self._process: asyncssh.SSHClientProcess[bytes] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._callback: Callable[[bytes], None] | None = None

    def on_output(self, callback: Callable[[bytes], None]) -> None:
        self._callback = callback

    async def start(self) -> None:
        if self._process is not None:
            return
        self._process = await self._conn.create_process(
            self._command,
            term_type="xterm-256color",
            request_pty=True,
            encoding=None,  # binary I/O
        )
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        if self._process is None:
            return
        try:
            async for chunk in self._process.stdout:
                if self._callback is not None and chunk:
                    self._callback(chunk)
        except asyncssh.Error as exc:
            logger.debug("ssh attach reader ended: {}", exc)

    async def feed_input(self, data: bytes) -> None:
        if self._process is None:
            raise RuntimeError("SshAttach not started")
        self._process.stdin.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        if self._process is None:
            raise RuntimeError("SshAttach not started")
        # asyncssh signature: change_terminal_size(width, height, ...)
        self._process.change_terminal_size(width=cols, height=rows)

    async def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            # ``wait_closed`` can raise a variety of asyncssh errors when the
            # remote side dies abruptly (ConnectionLost, ProcessError, etc.).
            # We're tearing down anyway — suppress and continue.
            with contextlib.suppress(Exception):
                await self._process.wait_closed()
            self._process = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
