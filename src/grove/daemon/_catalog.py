"""Short-TTL memo over the host-wide session catalog.

``SessionCatalog.scan()`` head-reads every session in every adapter's store and
pays one ``/proc`` walk per call. That is cheap enough to serve a request and
far too expensive to repeat per row or per dashboard tick — so the catalog is
**request-scoped**: it never rides the 2 s activity poll, and this memo absorbs
the burst a single UI interaction makes (a listing, then a drill-in into one of
its rows, then a re-listing on focus) into one scan.

The memo holds the **unbounded** scan and lets the route slice it, because
``limit`` is applied after the newest-first sort — memoizing per-limit would
answer a wider request from a narrower cache. One lock, so a burst of
concurrent requests pays for one scan rather than N.

It also owns the catalog's one background job: filling the durable turn-count
cache for the rows the scan could not answer. That work is a full transcript
parse per changed session, so it can never run inside the request it serves —
but it must not be a timer either, because a count nobody is looking at is pure
waste. Scheduling it FROM a scan is the gate: the daemon counts transcripts
exactly when something asked to see the sessions list.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Final

from loguru import logger

from grove.core.sessions import CatalogEntry, SessionCatalog

_CATALOG_TTL_SECONDS: Final[float] = 5.0
"""Long enough that list → drill-in → re-list is one scan, short enough that a
session started seconds ago shows up on the next look. Deliberately unrelated
to the 2 s activity poll: nothing here is ever polled."""


class _CatalogMemo:
    """A ``SessionCatalog`` behind a TTL cache — the daemon's only catalog reader.

    Blocking by contract (filesystem + ``/proc`` I/O): every caller runs it in
    the executor, like every other scan the daemon serves.
    """

    def __init__(
        self,
        catalog: SessionCatalog,
        *,
        ttl_seconds: float = _CATALOG_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._catalog = catalog
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: tuple[CatalogEntry, ...] = ()
        self._scanned_at: float | None = None
        # Its OWN single worker, not the default executor: a cold pass is tens
        # of seconds of transcript parsing, and the default executor is the
        # render path every route off-loads onto. One worker because the job is
        # single-flight by nature — a second pass would re-read the same files.
        self._counters = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grove-turncount")
        self._counting: Future[int] | None = None
        self._stopped = threading.Event()

    def rows(self) -> tuple[CatalogEntry, ...]:
        """Every discoverable session on the host, newest-first."""
        with self._lock:
            now = self._clock()
            if self._scanned_at is None or (now - self._scanned_at) >= self._ttl:
                self._rows = self._catalog.scan()
                self._scanned_at = now
            return self._rows

    def count_turns_in_background(self) -> None:
        """Fill the durable turn-count cache for the last scan, off this thread.

        Returns immediately, always. A pass already running is left alone
        rather than queued: it is reading the same files, and the rows it
        counts land in the file (every 25 sessions) where the next scan picks
        them up — so a user watching the sessions list sees the column fill in
        while the first pass is still working.

        Called after serving a scan, never on a timer: the count exists to be
        looked at, and nobody is looking unless a listing was just requested.
        """
        with self._lock:
            if self._stopped.is_set() or not self._rows:
                return
            if self._counting is not None and not self._counting.done():
                return
            rows = self._rows
            self._counting = self._counters.submit(self._count_turns, rows)

    def close(self) -> None:
        """Stop counting. Never waits on a pass in flight.

        ``shutdown(wait=False, cancel_futures=True)`` drops what is queued, and
        the stop flag ends a running pass at the next session boundary — a
        shutdown must not sit through tens of seconds of parsing, and everything
        counted so far is already durable.
        """
        self._stopped.set()
        self._counters.shutdown(wait=False, cancel_futures=True)

    def _count_turns(self, rows: tuple[CatalogEntry, ...]) -> int:
        counted = self._catalog.count_turns(rows, stop=self._stopped.is_set)
        if counted:
            logger.debug("counted turns for {} session(s)", counted)
        return counted

    def find(self, *, kind: str, cwd: str, session_id: str) -> CatalogEntry | None:
        """The row identified by ``(kind, cwd, session_id)`` — the coordinates a
        catalog row carries, and the only ones that identify a session with no
        workspace to resolve through.

        ``cwd`` matches the value the row itself reported, exactly as the
        adapters match a recorded cwd; a client passes back what it was given.
        """
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
