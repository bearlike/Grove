"""Private workspace tmux servers (``tmux -L grove-<id>``) that no record names."""

from __future__ import annotations

import socket as _socket
import stat
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

from grove.core.errors import GroveError, TmuxError
from grove.core.reaper.ledger import Ledger, ResourceClass, ResourceRef
from grove.core.tmux import TmuxServer, kill_server


class TmuxCollector:
    """Lists Grove's private tmux sockets and ends one server.

    A workspace server holds exactly one workspace's sessions and ends itself
    with its last one (``exit-empty``), so a socket still present for a
    workspace no record names is a leak whether or not anything answers on it.
    """

    cls: ClassVar[ResourceClass] = "tmux"
    GRACE: ClassVar[timedelta] = timedelta(seconds=60)

    def __init__(self, socket_dir: Path | None = None) -> None:
        """Bind to the directory tmux keeps this user's sockets in.

        Args:
            socket_dir: Where ``-L`` sockets live; ``None`` asks tmux's own rule
                (``$TMUX_TMPDIR/tmux-<uid>``) through :class:`TmuxServer`.
        """
        if socket_dir is None:
            probe = TmuxServer(socket="x").socket_path()
            socket_dir = probe.parent if probe is not None else None
        self._dir = socket_dir

    def found(self) -> list[ResourceRef]:
        if self._dir is None:
            return []
        try:
            entries = list(self._dir.iterdir())
        except OSError:
            return []
        return [
            ResourceRef("tmux", entry.name)
            for entry in entries
            if Ledger.owns_socket(entry.name) and self._is_socket(entry)
        ]

    @staticmethod
    def _is_socket(path: Path) -> bool:
        try:
            return stat.S_ISSOCK(path.lstat().st_mode)
        except OSError:
            return False

    def remove(self, ref: ResourceRef) -> None:
        # The name is re-checked here, at the moment of acting: `kill-server` on
        # any other socket would end somebody else's sessions.
        if self._dir is None or not Ledger.owns_socket(ref.key):
            raise GroveError(f"refusing to end tmux server {ref.key!r}: not a Grove socket")
        socket = self._dir / ref.key
        try:
            kill_server(TmuxServer(socket=ref.key))
        except TmuxError as exc:
            if self._listening(socket):
                # Wedged: it accepts and never answers, and its socket is the
                # only thing still naming it, so the socket is NOT unlinked.
                raise GroveError(f"tmux server {ref.key} did not end: {exc}") from exc
        try:
            # A server that crashed or was SIGKILLed leaves its socket behind.
            socket.unlink(missing_ok=True)
        except OSError as exc:
            raise GroveError(f"could not remove stale tmux socket {socket}: {exc}") from exc

    @staticmethod
    def _listening(socket: Path) -> bool:
        """Whether a server still accepts on *socket*; a refused connect means it is stale."""
        probe = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            probe.connect(str(socket))
        except OSError:
            return False
        finally:
            probe.close()
        return True


__all__ = ["TmuxCollector"]
