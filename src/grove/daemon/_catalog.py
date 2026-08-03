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
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Final

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

    def rows(self) -> tuple[CatalogEntry, ...]:
        """Every discoverable session on the host, newest-first."""
        with self._lock:
            now = self._clock()
            if self._scanned_at is None or (now - self._scanned_at) >= self._ttl:
                self._rows = self._catalog.scan()
                self._scanned_at = now
            return self._rows

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
