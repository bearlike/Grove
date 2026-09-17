"""Deliver native filesystem edges from explicit roots without fallback polling."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from loguru import logger

from grove.core.admission import Admission, AdmissionLimits, BoundedInbox, InboxClosed

_BATCH_EVENT_OVERHEAD_BYTES = 16

type FileChange = tuple[int, str]
type FileWatcher = Callable[..., AsyncIterator[set[FileChange]]]


class FileEventKind(StrEnum):
    """The portable subset of filesystem changes consumers may observe."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"

    @classmethod
    def from_watchfiles(cls, change: int) -> FileEventKind:
        """Translate watchfiles' stable numeric change code at the boundary."""
        return (cls.ADDED, cls.MODIFIED, cls.DELETED)[change - 1]


@dataclass(frozen=True, slots=True)
class FileEvent:
    """One changed watched file in an admitted upstream batch."""

    kind: FileEventKind
    path: Path


@dataclass(frozen=True, slots=True)
class FileEventBatch:
    """A coherently observed upstream batch of changed explicit roots."""

    events: tuple[FileEvent, ...]


class FileEventRecoveryReason(StrEnum):
    """Why a consumer must reconcile its own filesystem state."""

    OVERLOADED = "overloaded"
    WATCHER_FAILURE = "watcher_failure"


@dataclass(frozen=True, slots=True)
class FileEventRecovery:
    """A watch gap that callers repair by rereading their explicit roots."""

    reason: FileEventRecoveryReason
    reconnects_remaining: int


type FileEventCallback = Callable[[FileEventBatch], None]
type FileEventRecoveryCallback = Callable[[FileEventRecovery], None]


class FileEventSource:
    """Watch explicit paths natively, batch them within budgets, and signal gaps.

    A file root watches its parent non-recursively for atomic replacement. A
    directory root is watched directly and only yields contained paths; it is
    recursive solely when the caller asks. Upstream ``watchfiles`` exposes no
    overflow event, so rejected source batches and watcher errors are explicit
    recovery edges rather than reasons to discover files by polling.
    """

    def __init__(
        self,
        roots: Iterable[Path],
        on_event: FileEventCallback,
        *,
        on_recovery: FileEventRecoveryCallback | None = None,
        limits: AdmissionLimits | None = None,
        recursive: bool = False,
        include_ignored: bool = False,
        max_batch_items: int = 64,
        max_batch_bytes: int = 256 * 1024,
        debounce_ms: int = 50,
        max_age_ms: int = 1_600,
        max_reconnects: int = 2,
        reconnect_delay_seconds: float = 1.0,
        watcher: FileWatcher | None = None,
    ) -> None:
        self._validate(
            max_batch_items=max_batch_items,
            max_batch_bytes=max_batch_bytes,
            debounce_ms=debounce_ms,
            max_age_ms=max_age_ms,
            max_reconnects=max_reconnects,
            reconnect_delay_seconds=reconnect_delay_seconds,
        )
        canonical_roots = tuple(dict.fromkeys(root.resolve() for root in roots))
        if not canonical_roots:
            raise ValueError("at least one watched root is required")
        # A root that does not exist YET is classified as a directory, not a
        # file, and the asymmetry is deliberate. Classification happens once, at
        # construction, while an agent provider creates `<config>/projects` on
        # its first session — routinely after the daemon started. Filed as a
        # file root, that root matched only itself forever (file containment is
        # exact equality), so every transcript later written beneath it was
        # rejected and a fresh profile never indexed a session until restart.
        #
        # Reading it the other way costs nothing, because the directory rule
        # DEGRADES correctly for a path that turns out to be a file: a file is
        # relative to itself, so it is still admitted, and nothing else can be
        # relative to it. Absence is therefore resolved toward the reading that
        # can still be right later.
        self._file_roots = frozenset(
            root for root in canonical_roots if root.exists() and not root.is_dir()
        )
        self._directory_roots = frozenset(
            root for root in canonical_roots if root not in self._file_roots
        )
        self._roots = canonical_roots
        self._on_event = on_event
        self._on_recovery = on_recovery or (lambda _: None)
        self._recursive = recursive
        # Git metadata is an explicit source, but watchfiles' default filter
        # suppresses every .git path. Containment still applies after this opt-in.
        self._include_ignored = include_ignored
        self._max_batch_items = max_batch_items
        self._max_batch_bytes = max_batch_bytes
        self._debounce_ms = debounce_ms
        self._max_age_ms = max_age_ms
        self._max_reconnects = max_reconnects
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._watcher = watcher or self._default_watcher()
        self._inbox = BoundedInbox[FileEventBatch](limits or AdmissionLimits())
        self._watch_task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._closed = False
        self._stopped = False
        self._ready = asyncio.Event()

    @property
    def roots(self) -> tuple[Path, ...]:
        """Return the canonical file or directory roots this source may report."""
        return self._roots

    @property
    def closed(self) -> bool:
        """Whether shutdown has permanently closed this source."""
        return self._closed

    @property
    def stopped(self) -> bool:
        """Whether the bounded reconnect budget has been exhausted."""
        return self._stopped

    async def start(self) -> None:
        """Start one watcher and one loop-owned delivery task, idempotently."""
        if self._closed:
            raise RuntimeError("file event source is closed")
        if self._watch_task is not None:
            return
        self._inbox.bind()
        self._delivery_task = asyncio.create_task(
            self._deliver(), name="grove-file-events-delivery"
        )
        self._watch_task = asyncio.create_task(self._watch(), name="grove-file-events-watch")

    async def wait_ready(self, timeout: float = 2.0) -> None:
        """Wait for the native watcher to complete its first read cycle."""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        if self._stopped:
            raise RuntimeError("filesystem source failed before readiness")

    async def aclose(self) -> None:
        """Stop both tasks and return accepted pending events to their producer."""
        if self._closed:
            return
        self._closed = True
        self._inbox.close()
        tasks = tuple(task for task in (self._watch_task, self._delivery_task) if task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch(self) -> None:
        reconnects_remaining = self._max_reconnects
        while not self._closed:
            try:
                async for changes in self._watcher(
                    *(str(path) for path in self._watch_paths),
                    force_polling=False,
                    recursive=self._recursive,
                    step=self._debounce_ms,
                    debounce=self._max_age_ms,
                    yield_on_timeout=True,
                    rust_timeout=1000,
                    **({"watch_filter": None} if self._include_ignored else {}),
                ):
                    self._ready.set()
                    self._admit_changes(changes, reconnects_remaining)
                if self._closed:
                    return
                raise RuntimeError("watchfiles ended unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._recover(FileEventRecoveryReason.WATCHER_FAILURE, reconnects_remaining)
                logger.warning("filesystem watcher failed: {}", exc)
            if reconnects_remaining == 0:
                self._stopped = True
                self._ready.set()
                return
            reconnects_remaining -= 1
            await asyncio.sleep(self._reconnect_delay_seconds)

    def _admit_changes(self, changes: set[FileChange], reconnects_remaining: int) -> None:
        batch: list[FileEvent] = []
        batch_bytes = 0
        for raw_kind, raw_path in changes:
            # `resolve()` is a syscall per event and this runs on the loop, so a
            # very large change set (a build, an extraction, a branch switch
            # under a recursive watch) blocks it — measured 525 ms for 20k paths.
            # A cheap unresolved prefix pre-filter was tried and REVERTED: a
            # root is resolved at construction, so a path arriving through a
            # symlinked parent is genuinely contained and still fails a raw
            # prefix test, which silently DROPS its events. Trading a latency
            # spike for lost edges is the wrong direction for a source whose
            # whole contract is that it does not miss one.
            path = Path(raw_path).resolve()
            if not self._contains(path):
                continue
            size_bytes = self._event_size(path)
            if size_bytes > self._max_batch_bytes:
                self._recover(FileEventRecoveryReason.OVERLOADED, reconnects_remaining)
                continue
            if batch and (
                len(batch) == self._max_batch_items
                or batch_bytes + size_bytes > self._max_batch_bytes
            ):
                self._admit_batch(batch, batch_bytes, reconnects_remaining)
                batch = []
                batch_bytes = 0
            batch.append(FileEvent(FileEventKind.from_watchfiles(raw_kind), path))
            batch_bytes += size_bytes
        if batch:
            self._admit_batch(batch, batch_bytes, reconnects_remaining)

    def _admit_batch(
        self, events: list[FileEvent], size_bytes: int, reconnects_remaining: int
    ) -> None:
        outcome = self._inbox.offer(FileEventBatch(tuple(events)), size_bytes=size_bytes)
        if outcome in (Admission.FULL, Admission.TOO_LARGE):
            self._recover(FileEventRecoveryReason.OVERLOADED, reconnects_remaining)

    async def _deliver(self) -> None:
        while True:
            try:
                delivery = await self._inbox.take()
            except InboxClosed:
                return
            try:
                self._on_event(delivery.value)
            except Exception:
                logger.exception("filesystem event callback failed")
            finally:
                self._inbox.complete(delivery)

    @property
    def _watch_paths(self) -> tuple[Path, ...]:
        # What can be ARMED is never what is ADMITTED. A watcher can only attach
        # to a directory that exists, so a file root and a not-yet-created
        # directory root both fall back to their nearest existing ancestor —
        # which widens what is OBSERVED and nothing else, because `_contains`
        # still filters every admitted path against the real root.
        paths: set[Path] = set()
        for root in (*self._directory_roots, *self._file_roots):
            candidate = root if root in self._directory_roots else root.parent
            while not candidate.is_dir() and candidate != candidate.parent:
                candidate = candidate.parent
            paths.add(candidate)
        return tuple(paths)

    def _contains(self, path: Path) -> bool:
        if path in self._file_roots or path in self._directory_roots:
            return True
        if not self._recursive:
            return path.parent in self._directory_roots
        # The path's depth bounds the lookup, not the host's watch count. Scanning
        # every root made each event O(all watched directories) on the event loop.
        return any(parent in self._directory_roots for parent in path.parents)

    @staticmethod
    def _event_size(path: Path) -> int:
        return len(str(path).encode()) + _BATCH_EVENT_OVERHEAD_BYTES

    def _recover(self, reason: FileEventRecoveryReason, reconnects_remaining: int) -> None:
        try:
            self._on_recovery(FileEventRecovery(reason, reconnects_remaining))
        except Exception:
            logger.exception("filesystem recovery callback failed")

    @staticmethod
    def _validate(
        *,
        max_batch_items: int,
        max_batch_bytes: int,
        debounce_ms: int,
        max_age_ms: int,
        max_reconnects: int,
        reconnect_delay_seconds: float,
    ) -> None:
        if max_batch_items <= 0:
            raise ValueError("max_batch_items must be positive")
        if max_batch_bytes <= 0:
            raise ValueError("max_batch_bytes must be positive")
        if debounce_ms <= 0:
            raise ValueError("debounce_ms must be positive")
        if max_age_ms <= 0:
            raise ValueError("max_age_ms must be positive")
        if max_reconnects < 0:
            raise ValueError("max_reconnects must not be negative")
        if reconnect_delay_seconds < 0:
            raise ValueError("reconnect_delay_seconds must not be negative")

    @staticmethod
    def _default_watcher() -> FileWatcher:
        # Imported only when the source is constructed so leaf core imports do
        # not make uvicorn's standard extra a module-import dependency.
        module = __import__("watchfiles", fromlist=["awatch"])
        return cast(FileWatcher, module.awatch)
