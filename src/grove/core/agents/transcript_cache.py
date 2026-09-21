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
  reset-on-truncation/rotation, and LRU retention budgets. Records parsed here
  are private to one fold state, so a folder that mutates records in
  place (``absorb_continuation``) stays correct — a reset always re-parses
  from disk, never from previously-mutated objects.
- :class:`ResultMemo` — a stat-signature memo for derived read products
  (activity/messages/turns/digest), so an unchanged transcript costs one
  ``stat`` per file instead of a record walk.

Thread-safety: the lock is PER FOLD STATE, with a short instance lock over the
LRU map and the byte totals. The daemon folds several workspaces' transcripts
concurrently (the poll executor thread and request executor threads), and those
path tuples are disjoint, so a single instance lock made every one of them wait
on whichever fold happened to be running. Lock order is always *state then
instance*, never the reverse — ``_state_for`` and ``_rebuild`` take the instance
lock holding no state lock, and ``_project`` takes it while holding one.

Memo values must be immutable (frozen dataclasses / tuples); they are shared
across callers.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from loguru import logger

_T = TypeVar("_T")

#: Approximate retained-object budget for each incremental fold cache.
#:
#: This bounds the cache's own Python object graph, not process RSS: objects may
#: share internals with each other or with an immutable memo value, and CPython's
#: allocator retains arenas after objects are released. The estimate walks only
#: newly admitted values and records its result, never re-walking a warm state.
DEFAULT_MAX_RETAINED_BYTES = 256 * 1024 * 1024
#: Legacy source-byte budget retained as a compatible constructor argument.
DEFAULT_MAX_SOURCE_BYTES = 256 * 1024 * 1024
DEFAULT_MEMO_MAXSIZE = 512
DEFAULT_MEMO_MAX_BYTES = 64 * 1024 * 1024
_PREFIX_BYTES = 256


def _retained_size(value: object, seen: set[int] | None = None) -> int:
    """Approximate a currently retained graph, including ordinary instance dicts.

    ``seen`` is intentionally local to one admission. Persisting object ids
    across admissions is unsafe: ids of released temporaries can be reused, and
    a retained record may grow through a continuation. A local cycle guard makes
    the estimate conservative across admissions without letting either case
    undercount the budget.
    """
    if seen is None:
        seen = set()
    ident = id(value)
    if ident in seen:
        return 0
    seen.add(ident)
    total = sys.getsizeof(value)
    if isinstance(value, dict):
        return total + sum(
            _retained_size(key, seen) + _retained_size(item, seen) for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return total + sum(_retained_size(item, seen) for item in value)
    if isinstance(value, (bytearray, bytes, str)):
        return total
    if is_dataclass(value) and not isinstance(value, type):
        total += sum(_retained_size(getattr(value, item.name), seen) for item in fields(value))
    if hasattr(value, "__dict__"):
        total += _retained_size(vars(value), seen)
    for owner in type(value).__mro__:
        slots = getattr(owner, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        total += sum(
            _retained_size(getattr(value, slot), seen)
            for slot in slots
            if slot not in {"__weakref__", "__dict__"} and hasattr(value, slot)
        )
    return total


@dataclass(slots=True)
class CacheMetrics:
    """Deterministic counters for one cache instance — an operator's evidence.

    Every earlier decision about this cache was argued from a profiler, which
    can say where time went and not *why*: a cold parse and a warm reset look
    identical in a stack sample. These are counts of the events that decide it,
    so "the budget is too small for this fleet" (``resets`` climbing with
    ``evictions``) is distinguishable from "the transcripts are simply growing"
    (``parsed_bytes`` climbing alone) without attaching anything to the daemon.

    Cumulative for the process's life and never reset by ``clear()``, which is a
    test seam rather than an operational event.
    """

    hits: int = 0
    misses: int = 0
    resets: int = 0
    evictions: int = 0
    parsed_bytes: int = 0

    def snapshot(self) -> CacheMetrics:
        """A detached copy — the counters keep moving under the caller."""
        return CacheMetrics(
            hits=self.hits,
            misses=self.misses,
            resets=self.resets,
            evictions=self.evictions,
            parsed_bytes=self.parsed_bytes,
        )


class RecordFolder(Protocol):
    """An adapter's per-line fold policy: ``add`` sees each parsed line exactly
    once (in file order per path, paths in the order given to ``read``);
    ``records`` returns the accumulated fold output, which the cache snapshots.
    ``add`` may return the newly retained root (or an aggregate containing roots)
    so a folder's own incremental projections are charged without a history walk.

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

    def add(self, raw: dict[str, Any], source: str) -> object | None: ...

    def records(self) -> list[Any]: ...


@dataclass(slots=True)
class _Cursor:
    """Byte progress through one file: ``offset`` covers complete lines only —
    a trailing partial line (writer mid-append) is left for the next read."""

    ino: int = -1
    offset: int = 0
    size: int = -1
    mtime_ns: int = -1
    prefix: bytes = b""


@dataclass(slots=True)
class _State:
    folder: RecordFolder
    #: Serializes this fold alone. Disjoint path tuples fold concurrently; only
    #: the LRU map and the byte totals need the cache-wide lock.
    lock: threading.Lock = field(default_factory=threading.Lock)
    cursors: dict[str, _Cursor] = field(default_factory=dict)
    retained_bytes: int = 0
    source_bytes: int = 0
    folder_shallow_bytes: int = 0
    accounted_retained_bytes: int = 0
    accounted_source_bytes: int = 0

    def __post_init__(self) -> None:
        # Even a missing-file state has a folder, cursor map, and LRU entry.
        self.folder_shallow_bytes = _folder_shallow_size(self.folder)
        self.retained_bytes = (
            sys.getsizeof(self) + sys.getsizeof(self.cursors) + self.folder_shallow_bytes
        )

    def reset(self, folder: RecordFolder) -> None:
        """Rebuild this fold in place, keeping the identity its lock protects.

        A rebuild replaces the folder rather than the ``_State``, because a
        waiting thread already holds a reference to this object's lock — swapping
        the map entry under it would let two threads fold the same paths into two
        different folders and publish whichever finished last.
        """
        self.folder = folder
        self.cursors = {}
        self.source_bytes = 0
        self.folder_shallow_bytes = _folder_shallow_size(folder)
        self.retained_bytes = (
            sys.getsizeof(self) + sys.getsizeof(self.cursors) + self.folder_shallow_bytes
        )


def _folder_shallow_size(folder: RecordFolder) -> int:
    """Size the folder's own containers, without walking its retained records."""
    total = sys.getsizeof(folder)
    values: list[object] = []
    if hasattr(folder, "__dict__"):
        values.extend(vars(folder).values())
    slots = getattr(type(folder), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    for slot in slots:
        if hasattr(folder, slot):
            values.append(getattr(folder, slot))
    return total + sum(sys.getsizeof(value) for value in values)


class TranscriptCache:
    """Incrementally folded records per path tuple, LRU-bounded by retained bytes.

    The budget is an object-size estimate, not an RSS guarantee. Parsed line
    graphs are charged once as they enter a fold, and folder/list bookkeeping is
    charged by its shallow capacity changes. This deliberately over-charges
    folded-away duplicate lines: admitting less is preferable to preserving an
    unbounded cache, and it avoids a history-sized graph walk on warm reads.
    """

    def __init__(
        self,
        folder_factory: Callable[[], RecordFolder],
        *,
        max_retained_bytes: int = DEFAULT_MAX_RETAINED_BYTES,
        max_source_bytes: int | None = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        if max_retained_bytes <= 0:
            raise ValueError("max_retained_bytes must be positive")
        if max_source_bytes is not None and max_source_bytes <= 0:
            raise ValueError("max_source_bytes must be positive when set")
        self._folder_factory = folder_factory
        self._max_retained_bytes = max_retained_bytes
        self._max_source_bytes = max_source_bytes
        self._states: OrderedDict[tuple[str, ...], _State] = OrderedDict()
        self._retained_bytes = 0
        self._source_bytes = 0
        self._lock = threading.Lock()
        self._metrics = CacheMetrics()

    @property
    def metrics(self) -> CacheMetrics:
        """A snapshot of this cache's counters. See :class:`CacheMetrics`."""
        with self._lock:
            return self._metrics.snapshot()

    def read(self, paths: Sequence[Path]) -> list[Any]:
        """Return a safe snapshot, retaining the fold only while it fits its budget."""
        return self._consume(paths, lambda folder: (list(folder.records()), None))

    def project(
        self,
        paths: Sequence[Path],
        projection: Callable[[RecordFolder], tuple[_T, object | None]],
    ) -> _T:
        """Project a fold under its lock and charge any newly retained roots.

        ``projection`` receives the private folder only while the fold lock is
        held. It returns ``(result, retained_roots)``; the first value must be a
        safe snapshot/immutable value, while the second names only objects the
        folder retained during this call. Mutation and accounting share this
        cache's lock; the callback must not re-enter the cache or acquire a
        derived-result memo lock.
        """
        return self._consume(paths, projection)

    def _consume(
        self,
        paths: Sequence[Path],
        consume: Callable[[RecordFolder], tuple[_T, object | None]],
    ) -> _T:
        key = tuple(str(path) for path in paths)
        state = self._state_for(key)
        with state.lock:
            if not self._advance_all(state, paths):
                state.reset(self._folder_factory())
                self._count("resets")
                self._advance_all(state, paths)
            result, retained_roots = consume(state.folder)
            if retained_roots is not None:
                state.retained_bytes += _retained_size(retained_roots)
            # Folders may retain a derived immutable tuple during projection.
            # Measuring their shallow containers charges pointer arrays in
            # O(fields), without walking all prior records.
            self._charge_shallow(state)
            self._publish(key, state)
        return result

    def _state_for(self, key: tuple[str, ...]) -> _State:
        """The fold for these paths, admitted if absent — under the SHORT lock.

        Returns before any file is read, so the cache-wide lock covers a dict
        lookup rather than a whole ingest. Taken while holding no state lock;
        see the lock-order note in the module docstring.
        """
        with self._lock:
            state = self._states.get(key)
            if state is not None:
                self._states.move_to_end(key)
                self._metrics.hits += 1
                return state
            self._metrics.misses += 1
            state = _State(folder=self._folder_factory())
            self._states[key] = state
            self._refresh_totals(state)
            return state

    def _publish(self, key: tuple[str, ...], state: _State) -> None:
        """Fold this state's byte deltas into the cache totals and re-evict.

        Called holding ``state.lock`` — the one place the two locks nest, and
        always in this order.
        """
        with self._lock:
            if self._states.get(key) is not state:
                # Evicted while this fold ran: its bytes were already subtracted
                # and the next reader will re-admit it cold.
                return
            self._refresh_totals(state)
            self._states.move_to_end(key)
            self._evict()

    @staticmethod
    def _charge_shallow(state: _State) -> None:
        """Re-measure the folder's own containers and charge the difference.

        O(fields) rather than a walk of what they point at — but still a
        ``getsizeof`` per field, so it runs once per read CHUNK (here, and at
        the end of ``_ingest``) rather than once per admitted line. A 4000-line
        catch-up paid this 4000 times to observe a handful of list growths.
        """
        new_shallow_bytes = _folder_shallow_size(state.folder)
        state.retained_bytes += new_shallow_bytes - state.folder_shallow_bytes
        state.folder_shallow_bytes = new_shallow_bytes

    def _count(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self._metrics, field_name, getattr(self._metrics, field_name) + amount)

    def _refresh_totals(self, state: _State) -> None:
        self._retained_bytes += state.retained_bytes - state.accounted_retained_bytes
        self._source_bytes += state.source_bytes - state.accounted_source_bytes
        state.accounted_retained_bytes = state.retained_bytes
        state.accounted_source_bytes = state.source_bytes

    def _discard(self, key: tuple[str, ...]) -> None:
        state = self._states.pop(key)
        self._retained_bytes -= state.accounted_retained_bytes
        self._source_bytes -= state.accounted_source_bytes

    def configure(
        self,
        *,
        max_retained_bytes: int = DEFAULT_MAX_RETAINED_BYTES,
        max_source_bytes: int | None = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        """Apply a new cache policy without replacing live state or its locks."""
        if max_retained_bytes <= 0:
            raise ValueError("max_retained_bytes must be positive")
        if max_source_bytes is not None and max_source_bytes <= 0:
            raise ValueError("max_source_bytes must be positive when set")
        with self._lock:
            self._max_retained_bytes = max_retained_bytes
            self._max_source_bytes = max_source_bytes
            self._evict()

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._retained_bytes = 0
            self._source_bytes = 0

    # ── internal ──────────────────────────────────────────────────────────

    def _advance_all(self, state: _State, paths: Sequence[Path]) -> bool:
        """Advance every cursor; ``False`` means the state is unsalvageable
        (a file shrank/rotated/vanished) and the caller must rebuild."""
        return all(self._advance(state, path) for path in paths)

    def _ingest_chunk(
        self, state: _State, path: Path, cursor: _Cursor, stat: os.stat_result
    ) -> bool:
        """One file's appended bytes, with the folder's capacity charged ONCE.

        The per-chunk charge is what makes a catch-up read O(appended bytes)
        rather than O(lines * folder fields): the accounting is only used to
        decide eviction, and eviction is decided per read, so measuring it per
        line bought nothing a per-chunk measurement does not.
        """
        before = cursor.offset
        ok = self._ingest(state, path, cursor, stat)
        self._charge_shallow(state)
        self._count("parsed_bytes", max(0, cursor.offset - before))
        return ok

    def _advance(self, state: _State, path: Path) -> bool:
        key = str(path)
        cursor = state.cursors.get(key)
        stat = self._stat(path)
        if stat is None:
            if cursor is not None and cursor.offset > 0:
                return False  # had content, now gone → rebuild without it
            if cursor is None:
                state.cursors[key] = _Cursor()  # absent (yet); keep waiting
                state.retained_bytes += sys.getsizeof(key) + sys.getsizeof(state.cursors[key])
            return True
        if cursor is None:
            cursor = _Cursor(ino=stat.st_ino)
            state.cursors[key] = cursor
            state.retained_bytes += sys.getsizeof(key) + sys.getsizeof(cursor)
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
        return self._ingest_chunk(state, path, cursor, stat)

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
    def _ingest(state: _State, path: Path, cursor: _Cursor, stat: os.stat_result) -> bool:
        """Stream complete appended lines into a folder and charge their retention.

        Reading one line at a time avoids transient copies of the whole delta;
        an individual record remains uncapped to preserve complete tool payloads.
        The cursor advances only over complete newline-terminated lines, so an
        in-progress final line remains on disk for the next read.
        """
        try:
            with path.open("rb") as fh:
                if cursor.prefix and fh.read(len(cursor.prefix)) != cursor.prefix:
                    return False
                if not cursor.prefix:
                    fh.seek(0)
                    cursor.prefix = fh.read(min(stat.st_size, _PREFIX_BYTES))
                fh.seek(cursor.offset)
                source = f"{stat.st_dev}:{stat.st_ino}"
                offset = cursor.offset
                while line := fh.readline():
                    if not line.endswith(b"\n"):
                        break
                    offset += len(line)
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(obj, dict):
                        records = state.folder.records()
                        previous_count = len(records)
                        retained = state.folder.add(obj, source)
                        new_records = state.folder.records()
                        roots = (
                            retained
                            if retained is not None
                            else (
                                new_records[previous_count:]
                                if len(new_records) > previous_count
                                # An absorbing legacy folder has no new root to
                                # expose; charge its new raw graph conservatively.
                                else obj
                            )
                        )
                        # The folder's own container capacity is charged ONCE per
                        # chunk by `_ingest_chunk`, never per line.
                        state.retained_bytes += _retained_size(roots)
                cursor.offset = offset
        except OSError as exc:
            logger.debug("transcript read failed for {}: {}", path, exc)
            return False
        cursor.size = stat.st_size
        cursor.mtime_ns = stat.st_mtime_ns
        cursor.ino = stat.st_ino
        state.source_bytes = sum(item.offset for item in state.cursors.values())
        return True

    def _evict(self) -> None:
        while self._states and (
            self._retained_bytes > self._max_retained_bytes
            or (self._max_source_bytes is not None and self._source_bytes > self._max_source_bytes)
        ):
            self._discard(next(iter(self._states)))
            self._metrics.evictions += 1


@dataclass(frozen=True, slots=True)
class _MemoEntry:
    signature: tuple[Any, ...]
    value: Any
    retained_bytes: int


class ResultMemo:
    """Stat-signature memo for a derived, immutable read product.

    Keyed by the caller's logical key (method, cwd, session id, …); valid while
    every backing file's ``(path, ino, size, mtime_ns)`` signature is unchanged.
    A changed path *set* (a transcript appearing, a new sub-agent file) changes
    the signature too, so "no transcript yet" never sticks. Values are measured
    only after computing; oversized values return normally but are not retained.
    """

    def __init__(
        self,
        *,
        maxsize: int = DEFAULT_MEMO_MAXSIZE,
        max_bytes: int = DEFAULT_MEMO_MAX_BYTES,
    ) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._maxsize = maxsize
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple[Any, ...], _MemoEntry] = OrderedDict()
        self._retained_bytes = 0
        self._lock = threading.Lock()
        self._inflight: dict[tuple[Any, ...], threading.Lock] = {}
        self._metrics = CacheMetrics()

    @property
    def metrics(self) -> CacheMetrics:
        """A snapshot of this memo's counters. See :class:`CacheMetrics`."""
        with self._lock:
            return self._metrics.snapshot()

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
        """The memoized value, computing it at most once per concurrent miss.

        ``compute`` runs outside the instance lock — it re-enters the fold cache,
        and holding this lock across that would invert the lock order. The cost
        of that freedom is a thundering herd: on a cold key the ~1 Hz poll and a
        request can both miss and both run a full-history projection, and the
        second one's result is discarded. So a miss takes a PER-KEY lock and
        re-checks the entry under it; the loser of the race waits for a value it
        would otherwise have computed twice.
        """
        sig = self.signature(paths)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit.signature == sig:
                self._entries.move_to_end(key)
                self._metrics.hits += 1
                return hit.value  # type: ignore[no-any-return]
            self._metrics.misses += 1
            gate = self._inflight.get(key)
            if gate is None:
                gate = self._inflight[key] = threading.Lock()
        with gate:
            # The winner published while this thread waited; its signature is the
            # one just measured unless the files moved again, which is an
            # ordinary miss.
            with self._lock:
                hit = self._entries.get(key)
                if hit is not None and hit.signature == sig:
                    self._entries.move_to_end(key)
                    return hit.value  # type: ignore[no-any-return]
            return self._compute_and_store(key, sig, compute)

    def _compute_and_store(
        self, key: tuple[Any, ...], sig: tuple[Any, ...], compute: Callable[[], _T]
    ) -> _T:
        """Run one miss and retain its value, holding this key's gate."""
        try:
            value = compute()  # outside the lock: compute may take the cache's own lock
        finally:
            # Dropped on the way out EITHER WAY. A stranded gate is not a
            # deadlock — the `with` releases it as the exception unwinds, so the
            # next reader acquires it normally — it is a LEAK: `_inflight` is
            # keyed by memo key and bounded by nothing, where `_entries` has a
            # maxsize. A projection raising is ordinary (a transcript vanishing
            # mid-read), so without the `finally` the map grows for the life of
            # the process.
            with self._lock:
                self._inflight.pop(key, None)
        retained_bytes = _retained_size(value)
        entry = _MemoEntry(sig, value, retained_bytes)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._retained_bytes -= previous.retained_bytes
            if retained_bytes <= self._max_bytes:
                self._entries[key] = entry
                self._retained_bytes += retained_bytes
                while len(self._entries) > self._maxsize or self._retained_bytes > self._max_bytes:
                    _, evicted = self._entries.popitem(last=False)
                    self._retained_bytes -= evicted.retained_bytes
                    self._metrics.evictions += 1
        return value

    def configure(
        self,
        *,
        maxsize: int = DEFAULT_MEMO_MAXSIZE,
        max_bytes: int = DEFAULT_MEMO_MAX_BYTES,
    ) -> None:
        """Apply a new memo policy while retaining valid live entries where possible."""
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        with self._lock:
            self._maxsize = maxsize
            self._max_bytes = max_bytes
            while len(self._entries) > maxsize or self._retained_bytes > max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._retained_bytes -= evicted.retained_bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._retained_bytes = 0
