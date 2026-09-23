"""Has the code on disk moved since this process imported it?

A long-lived Grove process (the daemon above all) keeps running whatever it
imported at boot, while the CLI imports afresh on every call. After a pull into
an editable install, or a reinstall, the two therefore run DIFFERENT code, and
nothing says so. Measured on 2026-09-22: a daemon booted before a phase-
vocabulary rename rejected every phase file the new CLI wrote. The rejection is
the tolerant reader's documented behaviour, so it rendered as "the agent has
not reported" and pointed the reader at the agent instead of the daemon.

This module answers that one question and holds no policy about what to do.
The daemon publishes the answer on ``WhoamiView`` and a client says "restart".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import grove


@dataclass(frozen=True, slots=True)
class LoadedSource:
    """The newest source mtime of the package, captured when the process booted.

    An mtime rather than a hash or a git revision, because it covers every way
    code arrives: a pull into an editable checkout rewrites the files it
    changes, and a wheel reinstall writes all of them. It errs toward "changed"
    (touching a file without editing it counts), which only ever costs a
    restart. It never misses a real update.
    """

    root: Path
    booted_mtime_ns: int

    @classmethod
    def capture(cls, root: Path | None = None) -> LoadedSource:
        """Snapshot the package this process is running from, right now."""
        package = root if root is not None else Path(grove.__file__).parent
        return cls(root=package, booted_mtime_ns=cls._newest(package))

    def changed(self) -> bool:
        """True once any source file is newer than it was at capture.

        A scan of a few hundred files (measured ~3 ms), so a caller on an event
        loop runs it off-thread. A file that vanishes mid-scan is skipped: an
        unreadable tree is not evidence that the code moved.
        """
        return self._newest(self.root) > self.booted_mtime_ns

    @staticmethod
    def _newest(root: Path) -> int:
        newest = 0
        for path in root.rglob("*.py"):
            try:
                newest = max(newest, path.stat().st_mtime_ns)
            except OSError:
                continue
        return newest


__all__ = ["LoadedSource"]
