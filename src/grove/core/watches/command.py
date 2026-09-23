"""The argv predicate: a bounded observation in a workspace's own runtime."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from grove.core.contracts.watches import CommandPredicate, WatchOutcome
from grove.core.watches.watcher import Watcher
from grove.core.workspace import Runtime, WorkspaceState


class CommandWatcher(Watcher[CommandPredicate]):
    """Run one registered argv and settle only on an expected exit status.

    A command has to observe the workspace which registered it, not the daemon's
    ambient environment. The workspace lookup is injected so this boundary never
    imports the manager and a vanished workspace can settle honestly.
    """

    kind = "command"
    TIMEOUT_SECONDS: Final[float] = 30.0
    #: How long to wait for a SIGKILLed group to actually leave the process
    #: table. Bounded because this runs on the shared watch worker: a group that
    #: refuses to die must cost one observation, never the scheduler.
    _REAP_GRACE_SECONDS: Final[float] = 5.0
    OUTPUT_LIMIT_BYTES: Final[int] = 2_048

    def __init__(self, workspace_lookup: Callable[[str], WorkspaceState | None]) -> None:
        self._workspace_lookup = workspace_lookup

    def observe(self, predicate: CommandPredicate, now: datetime) -> WatchOutcome | None:
        """Run one bounded command observation, or wait for its expected status."""
        del now
        workspace = self._workspace_lookup(predicate.workspace_id)
        if workspace is None:
            return WatchOutcome(
                ok=False,
                summary="The workspace that registered this command watch no longer exists.",
            )
        argv = self._argv_for(workspace, predicate)
        if argv is None:
            return None
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace.agent_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            return None

        output = bytearray()
        truncated = [False]
        reader = threading.Thread(
            target=self._drain_output,
            args=(process, output, truncated),
            name="grove-command-watch-output",
        )
        reader.start()
        try:
            returncode = process.wait(timeout=self.TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._kill_process_group(process)
            reader.join(timeout=self.TIMEOUT_SECONDS)
            return WatchOutcome(
                ok=False,
                summary=self._summary(
                    "The command timed out and its process group was killed.", output, truncated[0]
                ),
            )
        reader.join()
        if returncode not in predicate.terminal_exit_codes:
            return None
        return WatchOutcome(
            ok=True,
            summary=self._summary(
                f"The command exited with terminal status {returncode}.", output, truncated[0]
            ),
        )

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
        """Kill the session leader's group and WAIT for the group to be gone.

        ``process.wait()`` reaps the direct child only. A grandchild — the
        ordinary shape, since a predicate is usually a script that spawns
        something — is killed by the same ``killpg`` but is nobody's child here,
        so nothing reaps it and nothing observes it leave. SIGKILL delivery is
        asynchronous, so returning as soon as the direct child is reaped is a
        race that a fast machine wins and a loaded one loses: measured green
        locally and red on CI, with the grandchild still in the process table.

        **Signalling is the wrong probe, and that cost a CI round trip.** A
        killed-but-unreaped process is a ZOMBIE: it stays in ``/proc`` and
        ``kill(pid, 0)`` still succeeds on it, so a poll waiting for
        ``ProcessLookupError`` spins its whole budget and then returns anyway.
        Grove cannot fix that by reaping, because a grandchild is not its child
        — whoever inherits it does, and under a container whose PID 1 does not
        reap (the ordinary devcontainer shape) nobody ever will.

        So the wait reads the process STATE instead: ``Z`` means the process is
        dead for every purpose a caller cares about — it holds no memory, runs
        no code, and cannot touch the workspace. Waiting for the entry to vanish
        would be waiting on somebody else's reaper.
        """
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        deadline = time.monotonic() + CommandWatcher._REAP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if CommandWatcher._group_is_dead(process.pid):
                return
            time.sleep(0.01)

    @staticmethod
    def _group_is_dead(leader_pid: int) -> bool:
        """Is every member of this group gone or a zombie?

        Reads ``/proc`` directly rather than shelling out, because this runs on
        the shared watch worker and a fork per poll would cost more than the
        thing it observes. A platform without ``/proc`` answers True: the group
        was SIGKILLed either way, and blocking the worker to prove it on a
        system that cannot be asked would trade a real cost for a formality.
        """
        proc = Path("/proc")
        if not proc.is_dir():  # pragma: no cover - Linux is what CI and the fleet run
            return True
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                status = (entry / "status").read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue  # it exited while we looked, which is the answer we wanted
            # `stat` is "pid (comm) state ..." and comm may contain spaces, so
            # the state field is read after the LAST ')' rather than by index.
            tail = stat.rpartition(")")[2].split()
            if not tail or tail[0] == "Z":
                continue
            for line in status.splitlines():
                if line.startswith("NSpgid:") or line.startswith("Pgid:"):
                    if line.split()[-1] == str(leader_pid):
                        return False
                    break
        return True

    def _drain_output(
        self, process: subprocess.Popen[bytes], output: bytearray, truncated: list[bool]
    ) -> None:
        """Drain the pipe while retaining only its bounded prefix.

        Continuing to read after the cap prevents a verbose command from blocking
        on a full pipe, while retaining no more data than the outcome can report.
        """
        stdout = process.stdout
        if stdout is None:  # pragma: no cover - Popen above always supplies it
            return
        for chunk in iter(lambda: stdout.read(8_192), b""):
            remaining = self.OUTPUT_LIMIT_BYTES - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated[0] = True

    @staticmethod
    def _summary(prefix: str, output: bytearray, truncated: bool) -> str:
        """Render bounded output without making an omitted tail ambiguous."""
        detail = bytes(output).decode(errors="replace").strip()
        parts = [prefix]
        if detail:
            parts.append(f"Output: {detail}")
        if truncated:
            parts.append("Output truncated at the command-watch limit.")
        return " ".join(parts)

    @staticmethod
    def _argv_for(workspace: WorkspaceState, predicate: CommandPredicate) -> list[str] | None:
        """Build the host or in-container argv without reinterpreting user tokens."""
        if workspace.runtime is not Runtime.CONTAINER:
            return list(predicate.argv)
        if workspace.container is None:
            return None
        return workspace.container.exec_argv(predicate.argv)


__all__ = ["CommandWatcher"]
