"""Grove processes still serving a workspace no record names.

Two shapes, each recognised by the argv Grove itself composes, never by a name
pattern a user's own process could share: a native worker
(``… grove.core.native_worker --config <dir>/<workspace id>.json``) and a tmux
control-mode observer (``tmux -L grove-<id> … -C attach-session -r``).
"""

from __future__ import annotations

import re
import sys
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

from grove.core.errors import GroveError, ProcessError
from grove.core.process import ProcessTree
from grove.core.reaper.ledger import Ledger, ResourceClass, ResourceRef

#: A native worker's config file is named for its workspace.
_WORKER_CONFIG = re.compile(r"(?:^|/)([a-f0-9]{32})\.json$")
_WORKER_ENTRIES = ("grove.core.native_worker", "grove-native-worker")


class ProcessCollector:
    """Lists this user's Grove workers and observers, and stops one by identity."""

    cls: ClassVar[ResourceClass] = "process"
    GRACE: ClassVar[timedelta] = timedelta(seconds=60)

    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        """Bind to a ``/proc`` tree.

        Args:
            proc_root: The real ``/proc`` in production; a fake tree in tests.
        """
        self._proc = proc_root

    def found(self) -> list[ResourceRef]:
        if sys.platform != "linux":
            return []
        try:
            entries = list(self._proc.iterdir())
        except OSError:
            return []
        refs: list[ResourceRef] = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            ref = self._ref(entry)
            if ref is not None:
                refs.append(ref)
        return refs

    def _ref(self, entry: Path) -> ResourceRef | None:
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            # Gone mid-scan, or another user's process.
            return None
        words = [word.decode(errors="replace") for word in argv if word]
        workspace_id = self.serves(words)
        if workspace_id is None:
            return None
        try:
            # comm may hold spaces or parentheses; the last ')' ends it. Field
            # 22 (start time) pins this incarnation, so a recycled pid never
            # matches the ref a later sweep acts on.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None
        if fields[0] == "Z":
            return None
        return ResourceRef("process", f"{entry.name}:{fields[19]}", workspace_id)

    @staticmethod
    def serves(argv: list[str]) -> str | None:
        """What a Grove process serves, read off its own argv; ``None`` if not Grove's.

        A worker names its workspace id (its config file). An observer names
        only the private tmux socket it watches, so it reports that socket and
        :meth:`Ledger.candidates` matches it against the sockets records name.
        """
        if any(entry in word for word in argv for entry in _WORKER_ENTRIES):
            for flag, value in pairwise(argv):
                if flag == "--config" and (match := _WORKER_CONFIG.search(value)):
                    return match.group(1)
            return None
        if (
            argv
            and Path(argv[0]).name == "tmux"
            and ("-C" in argv or "-CC" in argv)
            and "attach-session" in argv
        ):
            for flag, value in pairwise(argv):
                if flag == "-L" and Ledger.owns_socket(value):
                    return value
        return None

    def remove(self, ref: ResourceRef) -> None:
        pid, _, start = ref.key.partition(":")
        tree = ProcessTree(((int(pid), start),), self._proc)
        try:
            tree.terminate_and_wait()
        except ProcessError:
            try:
                tree.kill_and_wait()
            except ProcessError as exc:
                raise GroveError(f"process {pid} did not stop: {exc}") from exc


__all__ = ["ProcessCollector"]
