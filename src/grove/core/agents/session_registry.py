"""Read Claude Code's native interactive-session registry.

Claude Code writes one small pid-keyed record under each configured
``sessions/`` directory.  The files are evidence only: a record outlives its
process, so the registry exposes only entries whose pid is still live (and,
when supplied, whose process-start tick still matches).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import ClassVar, Literal

from grove.core.agents.claude_code import _ClaudeHome


@dataclass(frozen=True, slots=True)
class NativeClaudeSession:
    """One live native Claude Code interactive session.

    ``tmux`` is the provider's pane target, not a Grove reconstruction.  It is
    retained with the status record so consumers that need pane identity do not
    have to parse the registry a second time.
    """

    pid: int
    session_id: str
    cwd: str
    tmux: str | None
    status: Literal["busy", "idle"]
    proc_start: str | None


@dataclass(frozen=True, slots=True)
class _StatSignature:
    """The metadata that tells a memoized registry record changed."""

    device: int
    inode: int
    size: int
    mtime_ns: int


class NativeSessionRegistry:
    """Scan live native interactive sessions across Claude's config cascade.

    The on-disk keys are pids rather than durable session identities.  A stale
    file must therefore never become activity evidence merely because its JSON
    still parses; liveness is checked on every scan rather than memoized with
    the file's unchanged contents.
    """

    _cache: ClassVar[dict[Path, tuple[_StatSignature, NativeClaudeSession | None]]] = {}
    _lock: ClassVar[RLock] = RLock()

    @classmethod
    def scan(cls, config_dirs: Sequence[Path] | None = None) -> tuple[NativeClaudeSession, ...]:
        """Return live interactive records in config-cascade order.

        Unchanged files pay one ``stat`` and reuse their parsed immutable value;
        a process check remains outside that memo because a live process can end
        while its registry file is unchanged.
        """
        bases = _ClaudeHome.config_dirs() if config_dirs is None else config_dirs
        paths: list[Path] = []
        seen: set[Path] = set()
        for base in bases:
            try:
                found = sorted((base / "sessions").glob("*.json"))
            except OSError:
                continue
            for path in found:
                key = path.resolve() if path.exists() else path
                if key not in seen:
                    seen.add(key)
                    paths.append(path)

        records: list[NativeClaudeSession] = []
        # A pid's record is unique in the registry, but a duplicated config base
        # can still expose it twice through a symlinked cascade entry.
        seen_sessions: set[str] = set()
        with cls._lock:
            live_paths = set(paths)
            for cached in tuple(cls._cache):
                if cached not in live_paths:
                    del cls._cache[cached]
            for path in paths:
                record = cls._read_cached(path)
                if (
                    record is not None
                    and record.session_id not in seen_sessions
                    and cls.is_live(record)
                ):
                    seen_sessions.add(record.session_id)
                    records.append(record)
        return tuple(records)

    @classmethod
    def for_session(
        cls, session_id: str, config_dirs: Sequence[Path] | None = None
    ) -> NativeClaudeSession | None:
        """The live native record for ``session_id``, or ``None`` when absent."""
        return next(
            (record for record in cls.scan(config_dirs) if record.session_id == session_id),
            None,
        )

    @classmethod
    def clear_cache(cls) -> None:
        """Drop memoized file contents; the public test/reset seam."""
        with cls._lock:
            cls._cache.clear()

    @staticmethod
    def is_live(record: NativeClaudeSession) -> bool:
        """Whether the record still names its original Linux process.

        PID existence alone is insufficient after reuse.  Claude's ``procStart``
        is Linux's ``/proc/<pid>/stat`` start-time tick, so compare it when the
        record provides it while accepting older records that did not.
        """
        try:
            stat = Path(f"/proc/{record.pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return False
        try:
            # The executable name is parenthesized and may contain spaces; the
            # remainder begins at field 3, with start-time at offset 19.
            fields = stat.rsplit(") ", 1)[1].split()
            if fields[0] == "Z":
                return False  # /proc survives briefly after an exited process.
            start = fields[19]
        except (IndexError, ValueError):
            return False
        return record.proc_start is None or start == record.proc_start

    @classmethod
    def _read_cached(cls, path: Path) -> NativeClaudeSession | None:
        try:
            stat = path.stat()
        except OSError:
            cls._cache.pop(path, None)
            return None
        signature = _StatSignature(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = cls._cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        record = cls._read(path)
        cls._cache[path] = (signature, record)
        return record

    @staticmethod
    def _read(path: Path) -> NativeClaudeSession | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return _native_session(data)


def _native_session(data: object) -> NativeClaudeSession | None:
    """Narrow one untrusted registry payload to the small activity shape."""
    if not isinstance(data, dict) or data.get("kind") != "interactive":
        return None
    pid = data.get("pid")
    session_id = data.get("sessionId")
    cwd = data.get("cwd")
    status = data.get("status")
    proc_start = data.get("procStart")
    tmux = data.get("tmux")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(cwd, str)
        or not cwd
        or status not in {"busy", "idle"}
        or not (proc_start is None or isinstance(proc_start, str))
        or not (tmux is None or isinstance(tmux, str))
    ):
        return None
    return NativeClaudeSession(
        pid=pid,
        session_id=session_id,
        cwd=cwd,
        tmux=tmux,
        status=status,
        proc_start=proc_start,
    )
