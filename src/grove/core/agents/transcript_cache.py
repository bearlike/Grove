"""Incremental transcript reading — parse only the bytes appended since last read.

Why this exists: the activity poll re-reads every session's transcript files on
every ~2 s tick, and every ``/activity`` snapshot request repeats the scan. On a
host with a multi-GB transcript tree the ``json.loads`` over full history was
~95 % of daemon CPU samples — two executor threads pegged and RSS ballooned
(profiled 2026-07-11). Session JSONL is append-only in the steady state, so the
whole cost is avoidable: keep fold state per path set and advance it by parsing
only new bytes.

Two atomic classes, both pure mechanism (no provider knowledge, no policy):

- :class:`TranscriptCache` — per path-tuple fold state. The adapter supplies
  the *folder* (its per-line fold policy: Claude's dedup/absorb merge, Codex's
  plain append) via ``folder_factory``; the cache owns byte cursors, appends,
  reset-on-truncation/rotation, and an LRU source-byte budget. Records parsed
  here are private to one fold state, so a folder that mutates records in
  place (``absorb_continuation``) stays correct — a reset always re-parses
  from disk, never from previously-mutated objects.
- :class:`ResultMemo` — a stat-signature memo for derived read products
  (activity/messages/turns/digest), so an unchanged transcript costs one
  ``stat`` per file instead of a record walk.

Thread-safety: one lock per instance — the daemon reads from the poll executor
thread and request executor threads concurrently. Memo values must be
immutable (frozen dataclasses / tuples); they are shared across callers.
"""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from loguru import logger

_T = TypeVar("_T")

# Bounds chosen for a large active fleet (a session set can span a ~30 MB main
# transcript + tens of MB of sub-agent files) while keeping worst-case resident
# memory well under the runaway RSS this module exists to fix.
DEFAULT_MAX_SOURCE_BYTES = 256 * 1024 * 1024
DEFAULT_MEMO_MAXSIZE = 512


class RecordFolder(Protocol):
    """An adapter's per-line fold policy: ``add`` sees each parsed line exactly
    once (in file order per path, paths in the order given to ``read``);
    ``records`` returns the accumulated fold output, which the cache snapshots.

    ``source`` identifies the FILE the line was read from. A fold that MERGES
    records sharing a provider-assigned id needs it, because one logical record
    is written to exactly one file: the same id arriving from a second file is a
    replay of another thread's record, never a continuation of this one. The
    cache stays provider-agnostic — it supplies the fact, the folder decides
    whether it means anything.

    It is the ``(st_dev, st_ino)`` pair rather than the path STRING, and the
    difference is load-bearing: ``locate`` globs each project directory and
    de-dupes its results lexically, so one transcript reachable through a
    symlinked directory is returned under two distinct strings. Keyed by
    string, those two aliases are two sources and a record the global uuid
    guard cannot drop — one carrying ``message.id`` but no ``uuid`` — is
    retained twice, which is the very duplication this scope exists to
    prevent. The identity is free: ``_ingest`` already holds the ``stat``."""

    def add(self, raw: dict[str, Any], source: str) -> None: ...

    def records(self) -> list[Any]: ...


@dataclass(slots=True)
class _Cursor:
    """Byte progress through one file: ``offset`` covers complete lines only —
    a trailing partial line (writer mid-append) is left for the next read."""

    ino: int = -1
    offset: int = 0
    size: int = -1
    mtime_ns: int = -1


@dataclass(slots=True)
class _State:
    folder: RecordFolder
    cursors: dict[str, _Cursor] = field(default_factory=dict)

    @property
    def source_bytes(self) -> int:
        return sum(c.offset for c in self.cursors.values())


class TranscriptCache:
    """Incrementally folded records per path tuple, LRU-bounded by source bytes."""

    def __init__(
        self,
        folder_factory: Callable[[], RecordFolder],
        *,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self._folder_factory = folder_factory
        self._max_source_bytes = max_source_bytes
        self._states: OrderedDict[tuple[str, ...], _State] = OrderedDict()
        self._lock = threading.Lock()

    def read(self, paths: Sequence[Path]) -> list[Any]:
        """The folded records for ``paths`` — a snapshot copy, safe to sort/slice.

        Any file that was truncated, rotated (inode change), rewritten in place
        (same size, new mtime), or removed resets the whole path-set state: the
        fold is order- and history-sensitive, so a partial rebuild could double
        merged records. Reset cost equals today's full parse — paid once per
        anomaly, not per tick.
        """
        key = tuple(str(p) for p in paths)
        with self._lock:
            state = self._states.get(key)
            if state is None or not self._advance_all(state, paths):
                state = _State(folder=self._folder_factory())
                self._advance_all(state, paths)
            self._states[key] = state
            self._states.move_to_end(key)
            self._evict()
            return list(state.folder.records())

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    # ── internal ──────────────────────────────────────────────────────────

    def _advance_all(self, state: _State, paths: Sequence[Path]) -> bool:
        """Advance every cursor; ``False`` means the state is unsalvageable
        (a file shrank/rotated/vanished) and the caller must rebuild."""
        return all(self._advance(state, path) for path in paths)

    def _advance(self, state: _State, path: Path) -> bool:
        key = str(path)
        cursor = state.cursors.get(key)
        stat = self._stat(path)
        if stat is None:
            if cursor is not None and cursor.offset > 0:
                return False  # had content, now gone → rebuild without it
            state.cursors[key] = _Cursor()  # absent (yet); keep waiting
            return True
        if cursor is None:
            cursor = _Cursor(ino=stat.st_ino)
            state.cursors[key] = cursor
        elif (
            stat.st_ino == cursor.ino
            and stat.st_size == cursor.size
            and stat.st_mtime_ns == cursor.mtime_ns
        ):
            return True  # unchanged — the hot fast path
        elif stat.st_ino != cursor.ino or stat.st_size <= cursor.size:
            # Rotated, truncated, or rewritten in place without net growth
            # (the unchanged case returned above) — the cursor can't describe
            # the new content, so the caller rebuilds from scratch.
            return False
        return self._ingest(state.folder, path, cursor, stat)

    @staticmethod
    def _stat(path: Path) -> os.stat_result | None:
        try:
            return path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.debug("transcript stat failed for {}: {}", path, exc)
            return None

    @staticmethod
    def _ingest(folder: RecordFolder, path: Path, cursor: _Cursor, stat: os.stat_result) -> bool:
        """Parse the bytes appended past ``cursor`` and fold each complete line.

        The offset only ever advances past the last ``\\n``: a trailing partial
        line (writer mid-append) is left in place and re-read once complete —
        UTF-8 never straddles that boundary because ``\\n`` is a single byte."""
        try:
            with path.open("rb") as fh:
                fh.seek(cursor.offset)
                data = fh.read()
        except OSError as exc:
            logger.debug("transcript read failed for {}: {}", path, exc)
            return False
        complete = data.rfind(b"\n") + 1
        # File IDENTITY, not the path string — see RecordFolder.add. Two aliases
        # of one transcript must fold as one source or the scope is defeated.
        source = f"{stat.st_dev}:{stat.st_ino}"
        for line in data[:complete].split(b"\n"):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(obj, dict):
                folder.add(obj, source)
        cursor.offset += complete
        cursor.size = stat.st_size
        cursor.mtime_ns = stat.st_mtime_ns
        cursor.ino = stat.st_ino
        return True

    def _evict(self) -> None:
        total = sum(s.source_bytes for s in self._states.values())
        while total > self._max_source_bytes and len(self._states) > 1:
            _, evicted = self._states.popitem(last=False)
            total -= evicted.source_bytes


class ResultMemo:
    """Stat-signature memo for a derived, immutable read product.

    Keyed by the caller's logical key (method, cwd, session id, …); valid while
    every backing file's ``(path, ino, size, mtime_ns)`` signature is unchanged.
    A changed path *set* (a transcript appearing, a new sub-agent file) changes
    the signature too, so "no transcript yet" never sticks.
    """

    def __init__(self, *, maxsize: int = DEFAULT_MEMO_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._entries: OrderedDict[tuple[Any, ...], tuple[tuple[Any, ...], Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def signature(paths: Sequence[Path]) -> tuple[Any, ...]:
        sig: list[tuple[str, int, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
                sig.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns))
            except OSError:
                sig.append((str(path), -1, -1, -1))
        return tuple(sig)

    def get_or_compute(
        self,
        key: tuple[Any, ...],
        paths: Sequence[Path],
        compute: Callable[[], _T],
    ) -> _T:
        sig = self.signature(paths)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] == sig:
                self._entries.move_to_end(key)
                return hit[1]  # type: ignore[no-any-return]
        value = compute()  # outside the lock: compute may take the cache's own lock
        with self._lock:
            self._entries[key] = (sig, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
