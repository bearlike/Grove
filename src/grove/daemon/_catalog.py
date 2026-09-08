"""Event-driven memo indexes for the host session catalog and diagram gallery.

``SessionCatalog.scan()`` head-reads every session in every adapter's store and
pays one ``/proc`` walk. ``DiagramGallery.scan()`` adds one ``git ls-files`` per
worktree and bounded diagram reads. Neither is a request-sized operation, so a
memo holds each complete result and changes only when its owner tells it about a
filesystem edge.

This module deliberately owns no watcher, timer, or poller. ``core.file_events``
(or another source that already knows an edge) calls the small mutation hooks
below. A source that can construct the changed ``CatalogEntry``/``GalleryItem``
can replace just that record. The current core catalog and gallery expose no
single-path read seam; for an append whose record is not supplied, callers must
ask for the explicit bounded ``reconcile()``. Silently substituting a TTL would
make elapsed time a whole-host scan again and would still miss a precise
completion guarantee.

The catalog also owns its one background job: filling durable parse-derived turn
facts for the rows the scan could not answer. That unbounded work is scheduled
only after a consumer has read the list, never by a timer.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from loguru import logger

from grove.core import process as process_module
from grove.core.gallery import DiagramGallery, GalleryItem
from grove.core.sessions import CatalogEntry, SessionCatalog


class _CatalogMemo:
    """An event-owned, complete ``SessionCatalog`` snapshot.

    Blocking by contract (filesystem + ``/proc`` I/O): every caller runs reads
    and explicit reconciliation in an executor. ``rows()`` bootstraps once and
    never expires on elapsed time. ``generation`` changes after bootstrap and
    every accepted record mutation, letting dependent indexes rejoin exactly
    when their input changed.
    """

    def __init__(self, catalog: SessionCatalog) -> None:
        self._catalog = catalog
        self._lock = threading.Lock()
        self._rows: tuple[CatalogEntry, ...] = ()
        self._bootstrapped = False
        self._generation = 0
        # Its OWN single worker, not the default executor: a cold pass is tens
        # of seconds of transcript parsing, and the default executor is the
        # render path every route off-loads onto. One worker because the job is
        # single-flight by nature — a second pass would re-read the same files.
        self._counters = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-turncount")
        self._counting: Future[int] | None = None
        self._stopped = threading.Event()

    @property
    def generation(self) -> int:
        """The event generation represented by the current rows."""
        with self._lock:
            return self._generation

    def rows(self) -> tuple[CatalogEntry, ...]:
        """Every discoverable session on the host, newest-first.

        The first reader does the one bootstrap scan. Subsequent reads are pure
        snapshot reads until an event hook changes a record or a caller requests
        the explicit ``reconcile()`` fallback for an unknown append.

        LIVENESS IS RE-FOLDED PER READ, because it is the one field on a row
        that is not a fact about a FILE. A process starts and exits with no
        filesystem event this memo could ever observe, so a cached `live` is
        frozen at whenever its row happened to be built — reporting a running
        agent as dead until something unrelated touched its transcript. The
        fold is the pure function over one bounded ``/proc`` scan that the
        cold path already paid for, so this restores the pre-event behaviour
        (one scan per request, never per row, never on the activity tick)
        rather than adding a cost.
        """
        with self._lock:
            if not self._bootstrapped:
                self._replace_rows(self._catalog.scan())
                return self._rows
            # Called on the CLASS, not on `self._catalog`: the fold is a
            # `staticmethod` and a pure judgement, so this is the seam whether
            # the injected catalog is the real one or a narrower double.
            return tuple(
                SessionCatalog.fold_liveness(
                    self._rows, process_module.list_agent_runtimes(), now=time.time()
                )
            )

    def reconcile(self) -> tuple[CatalogEntry, ...]:
        """Explicitly rebuild the complete snapshot for an unknown source edge.

        This is the bounded fallback while ``SessionCatalog`` has no targeted
        lookup/update seam. It is intentionally a named operation: callers must
        not disguise a host-wide scan as ordinary cache expiration.
        """
        with self._lock:
            self._replace_rows(self._catalog.scan())
            return self._rows

    def invalidate(
        self,
        *,
        entry: CatalogEntry | None = None,
        kind: str | None = None,
        cwd: str | None = None,
        session_id: str | None = None,
        path: Path | None = None,
    ) -> bool:
        """Apply one known edge, or explicitly expire the whole snapshot.

        Passing ``entry`` replaces (or appends) that one session record. Without
        it, coordinates identify a deletion and matching cached records are
        removed. A transcript ``path`` is an additional exact selector, useful
        where an event source has no recovered session coordinates.

        With no record or selector, this invalidates the complete snapshot so
        the NEXT reader performs a deliberate ``SessionCatalog.scan()``. This
        is the necessary fallback for an unknown append or modification: the
        current ``SessionCatalog`` has no single-path lookup seam. It is an
        event edge, never elapsed-time expiration.
        """
        with self._lock:
            if entry is not None:
                rows = [row for row in self._rows if self._key(row) != self._key(entry)]
                rows.append(entry)
                rows.sort(key=lambda row: row.ref.mtime, reverse=True)
                self._rows = tuple(rows)
                self._bootstrapped = True
                self._generation += 1
                return True

            if kind is None and cwd is None and session_id is None and path is None:
                self._bootstrapped = False
                self._generation += 1
                return True
            if not self._bootstrapped:
                return False
            retained = tuple(
                row
                for row in self._rows
                if not self._matches(row, kind=kind, cwd=cwd, session_id=session_id, path=path)
            )
            if len(retained) == len(self._rows):
                return False
            self._rows = retained
            self._generation += 1
            return True

    def update_path(self, path: Path) -> bool:
        """Refresh one changed transcript through the catalog's bounded path seam."""
        entry = self._catalog.entry_for_path(path)
        return self.invalidate(entry=entry) if entry is not None else self.invalidate(path=path)

    def on_file_events(self, batch: object) -> None:
        """Apply path-local file events; recovery intentionally expires all state.

        ``core.file_events`` owns collection and batching. Its normal batch has
        an ``events`` tuple whose members expose canonical ``path`` values. A
        recovery has no trustworthy complete delta, so it explicitly expires the
        snapshot. The callback performs no watcher work and never whole-scans.
        """
        events = getattr(batch, "events", None)
        if events is None:
            self.invalidate()
            return
        for event in events:
            path = getattr(event, "path", None)
            if isinstance(path, Path):
                self.update_path(path)

    def count_turns_in_background(self) -> None:
        """Fill durable turn facts for the current generation off this thread.

        An event mutation changes the snapshot's transcript fingerprints. The
        next listing schedules a pass for that new generation; if an older pass
        is still in flight it remains single-flight, and the following listing
        starts the fresh pass after it completes. ``TurnCountCache`` itself
        rejects obsolete fingerprints, so an old pass cannot publish stale facts
        for a changed transcript.
        """
        with self._lock:
            if self._stopped.is_set() or not self._rows:
                return
            if self._counting is not None and not self._counting.done():
                return
            rows = self._rows
            self._counting = self._counters.submit(self._count_turns, rows)

    def close(self) -> None:
        """Stop counting. Never waits on a pass in flight."""
        self._stopped.set()
        self._counters.shutdown(wait=False, cancel_futures=True)

    def _replace_rows(self, rows: tuple[CatalogEntry, ...]) -> None:
        self._rows = rows
        self._bootstrapped = True
        self._generation += 1

    @staticmethod
    def _key(row: CatalogEntry) -> tuple[str, str]:
        return (row.ref.adapter_kind, row.ref.session_id)

    @staticmethod
    def _matches(
        row: CatalogEntry,
        *,
        kind: str | None,
        cwd: str | None,
        session_id: str | None,
        path: Path | None,
    ) -> bool:
        if kind is not None and row.ref.adapter_kind != kind:
            return False
        if cwd is not None and row.ref.cwd != cwd:
            return False
        if session_id is not None and row.ref.session_id != session_id:
            return False
        return path is None or row.ref.transcript_path == path

    def _count_turns(self, rows: tuple[CatalogEntry, ...]) -> int:
        counted = self._catalog.count_turns(rows, stop=self._stopped.is_set)
        if counted:
            logger.debug("counted turns for {} session(s)", counted)
        return counted

    def find(self, *, kind: str, cwd: str, session_id: str) -> CatalogEntry | None:
        """The row identified by the three catalog coordinates, or ``None``."""
        return next(
            (
                row
                for row in self.rows()
                if row.ref.session_id == session_id
                and row.ref.adapter_kind == kind
                and row.ref.cwd == cwd
            ),
            None,
        )


class _GalleryMemo:
    """An event-owned gallery index joined against ``_CatalogMemo`` rows.

    It observes the catalog generation rather than a clock: session mutations
    that affect attribution cause the one necessary rejoin, while unrelated
    reads cost no scan. Diagram-file events can replace or remove one known item
    directly; an unknown diagram append uses explicit ``reconcile()`` because
    ``DiagramGallery`` currently offers no single-path scan seam.
    """

    def __init__(self, gallery: DiagramGallery, sessions: _CatalogMemo) -> None:
        self._gallery = gallery
        self._sessions = sessions
        self._lock = threading.Lock()
        self._items: tuple[GalleryItem, ...] = ()
        self._bootstrapped = False
        self._sessions_generation: int | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        """The event generation represented by the current items."""
        with self._lock:
            return self._generation

    def items(self) -> tuple[GalleryItem, ...]:
        """Every attributable diagram on the host, newest-first.

        ``session_live`` / ``workspace_live`` are re-folded per read for the
        reason ``_CatalogMemo.rows`` states: a process starting or exiting
        moves no generation, so a cached bit reports a running agent as dead
        on a row nothing else touched. Only those two fields are refreshed —
        the expensive attribution join still rides the catalog generation, so
        this costs one dict lookup per row and no scan.
        """
        rows = self._sessions.rows()
        session_generation = self._sessions.generation
        with self._lock:
            if not self._bootstrapped or self._sessions_generation != session_generation:
                self._replace_items(self._gallery.scan(rows), session_generation)
                return self._items
            live = {(row.ref.adapter_kind, row.ref.session_id): row.live for row in rows}
            return tuple(
                replace(
                    item,
                    session_live=live.get((item.session_kind or "", item.session_id), False),
                )
                if item.session_id is not None
                else item
                for item in self._items
            )

    def reconcile(self) -> tuple[GalleryItem, ...]:
        """Explicitly rebuild diagrams when an event names no materialized item."""
        rows = self._sessions.rows()
        session_generation = self._sessions.generation
        with self._lock:
            self._replace_items(self._gallery.scan(rows), session_generation)
            return self._items

    def invalidate(self, *, item: GalleryItem | None = None, path: Path | None = None) -> bool:
        """Apply a known diagram edge, or explicitly expire the whole index.

        ``item`` replaces or adds one item. ``path`` removes its matching item.
        With neither, the NEXT reader does one deliberate gallery scan. That is
        the fallback for an unknown added or modified file until ``DiagramGallery``
        grows a bounded single-path scan seam.
        """
        with self._lock:
            if item is not None:
                items = [known for known in self._items if known.path != item.path]
                items.append(item)
                items.sort(key=lambda known: known.modified_at, reverse=True)
                self._items = tuple(items)
                self._bootstrapped = True
                self._sessions_generation = self._sessions.generation
                self._generation += 1
                return True
            if path is None:
                self._bootstrapped = False
                self._generation += 1
                return True
            if not self._bootstrapped:
                return False
            retained = tuple(known for known in self._items if known.path != path)
            if len(retained) == len(self._items):
                return False
            self._items = retained
            self._generation += 1
            return True

    def update_path(self, path: Path) -> bool:
        """Refresh one changed diagram through the gallery's bounded path seam."""
        item = self._gallery.item_for_path(path, self._sessions.rows())
        return self.invalidate(item=item) if item is not None else self.invalidate(path=path)

    def on_file_events(self, batch: object) -> None:
        """Apply path-local file events; recovery intentionally expires all state."""
        events = getattr(batch, "events", None)
        if events is None:
            self.invalidate()
            return
        for event in events:
            path = getattr(event, "path", None)
            if isinstance(path, Path):
                self.update_path(path)

    def _replace_items(self, items: tuple[GalleryItem, ...], session_generation: int) -> None:
        self._items = items
        self._bootstrapped = True
        self._sessions_generation = session_generation
        self._generation += 1

    def find(self, item_id: str) -> GalleryItem | None:
        """The item whose opaque id is *item_id*, or ``None``."""
        return next((item for item in self.items() if item.id == item_id), None)
