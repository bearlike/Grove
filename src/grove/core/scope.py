"""One transient systemd user scope (a cgroup) per host workspace, so ending it ends everything.

A workspace agent starts servers, browsers and watchers, and some of them
double-fork away from the tree a ``/proc`` walk follows. A cgroup does not lose
them: everything a process starts stays in its cgroup however it detaches. So a
host workspace's private tmux server is started inside ``grove-<socket>.scope``
(``systemd-run --user --scope -p Delegate=yes``) and ending the workspace stops
the scope, which kills every process in it.

Where systemd's user manager is absent (a container, macOS, a host without a
user session) the server starts as before and teardown falls back to the
process-tree walk, which is reported as approximate by :attr:`Scope.exact`.
A container workspace is already its own cgroup and never uses this.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

#: How long a ``systemctl --user`` / ``systemd-run`` call may take before it
#: counts as no answer. The user manager answers in milliseconds when it is up.
_SYSTEMCTL_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class Scope:
    """The systemd user scope that holds one workspace's tmux server and its descendants."""

    unit: str

    @classmethod
    def for_socket(cls, socket: str) -> Scope:
        """The scope named after a private tmux socket (itself named after the workspace)."""
        return cls(unit=f"{socket}.scope")

    @staticmethod
    def available() -> bool:
        """Whether this process can start scopes under a systemd user manager."""
        if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
            return False
        if not Path("/run/systemd/system").is_dir():
            return False
        return Scope.user_bus_reachable(os.environ)

    @staticmethod
    def user_bus_reachable(env: Mapping[str, str]) -> bool:
        """Whether *env* lets ``systemd-run --user`` find the user manager's bus.

        A host booted with systemd says nothing about THIS process: a daemon
        started without ``XDG_RUNTIME_DIR`` (a stripped test env, cron, an SSH
        session without lingering) gets ``Failed to connect to bus`` from every
        ``systemd-run --user``, so wrapping its tmux start fails every create.
        Without a reachable bus the start runs unwrapped, like a host with no
        user manager at all.
        """
        if env.get("DBUS_SESSION_BUS_ADDRESS"):
            return True
        runtime_dir = env.get("XDG_RUNTIME_DIR")
        return runtime_dir is not None and runtime_dir != "" and Path(runtime_dir, "bus").exists()

    @property
    def exact(self) -> bool:
        """Whether teardown through this scope reaches every descendant, not an approximation."""
        return self.available()

    def wrap(self, argv: Sequence[str]) -> list[str]:
        """*argv* run inside this scope, or unchanged where scopes are unavailable.

        Args:
            argv: the command that starts the workspace's process tree.

        Returns:
            The ``systemd-run`` invocation, which exits when *argv* does and
            leaves anything it daemonized (a tmux server) in the scope.
        """
        if not self.available():
            return list(argv)
        return [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit={self.unit}",
            "-p",
            "Delegate=yes",
            "--",
            *argv,
        ]

    def stop(self) -> bool:
        """Kill every process in the scope. Best-effort: never raises.

        Returns:
            ``True`` when systemd stopped the scope (or it was already gone);
            ``False`` when scopes are unavailable or the stop did not answer,
            in which case the caller's tree walk is the only teardown.
        """
        if not self.available():
            return False
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", self.unit],
                check=False,
                capture_output=True,
                timeout=_SYSTEMCTL_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("scope {}: stop did not answer: {}", self.unit, exc)
            return False
        return True

    def pids(self) -> tuple[int, ...]:
        """Every process in the scope right now; empty when it is gone or unreadable."""
        if not self.available():
            return ()
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", self.unit, "-p", "ControlGroup", "--value"],
                check=False,
                capture_output=True,
                text=True,
                timeout=_SYSTEMCTL_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ()
        group = result.stdout.strip()
        if not group:
            return ()
        try:
            procs = (Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.procs").read_text()
        except OSError:
            return ()
        return tuple(int(line) for line in procs.split() if line.isdigit())


__all__ = ["Scope"]
