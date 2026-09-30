"""Project the planted corpus into the daemon's derived caches before anything reads it."""

from __future__ import annotations

import time

from loguru import logger

from grove.core.config import GroveConfig
from grove.core.registry import RepoRegistry
from grove.core.sessions import SessionCatalog
from grove.core.store import JsonWorkspaceStore
from grove.core.usage.service import UsageService


class UsageCacheWarmer:
    """Pay the transcript projection HERE, where it is a number somebody sees.

    Every usage surface refreshes on read, and a first read of a year-long
    corpus is minutes of transcript parsing — most of it `bashlex` attributing
    one leading executable per shell call (~1 ms each, and half of ~435,000 tool
    calls are shell). Left to the capture run, that whole cost lands inside the
    browser's first request to `/usage`, which times out long before it
    finishes.

    The store is the fleet's own rather than a default one, so a session that
    belongs to a live workspace is attributed to it — the TUI capture seeds into
    a non-default store path, where a default one resolves nothing.

    The cache is fingerprinted per transcript file, so the daemon's own refresh a
    moment later is a no-op. Best-effort by design: a screenshot run must still
    produce every other shot if the usage projection fails.

    THE SESSION CATALOG'S TURN-COUNT FILE IS WARMED HERE TOO, and skipping it
    stalls the usage page, not the sessions page. The capture opens the
    host-wide session list just before `/usage`, and that listing starts the
    daemon's background turn-count pass: a pure-Python full parse of every
    transcript, holding the GIL, on a thread inside the same process that
    serves the usage reads. SQLite gives the GIL up on every row step and then
    queues to get it back, so reads that step through many rows crawl for as
    long as the pass runs. Measured on this corpus, the page's usage reads took
    19 s quiet against 146 s with the pass running, which is the CI capture's
    90 s timeout. The two files share a fingerprint rule (`(mtime, size)` per
    transcript), so a pass started after this finds nothing left to parse.
    """

    def __init__(self, *, cfg: GroveConfig, store: JsonWorkspaceStore) -> None:
        self._cfg = cfg
        self._store = store

    def warm(self) -> None:
        registry = RepoRegistry(cfg=self._cfg, store=self._store)
        started = time.monotonic()
        try:
            result = UsageService(cfg=self._cfg, registry=registry).refresh(force=False)
        except Exception as exc:  # pragma: no cover - a capture tool, not a gate
            logger.warning("usage cache warm-up failed: {}", exc)
        else:
            logger.warning(
                "usage cache warm: {} sources in {:.1f}s",
                result.indexed_sources,
                time.monotonic() - started,
            )

        started = time.monotonic()
        try:
            catalog = SessionCatalog(registry)
            filled = catalog.count_turn_facts(catalog.scan())
        except Exception as exc:  # pragma: no cover - a capture tool, not a gate
            logger.warning("turn-count warm-up failed: {}", exc)
            return
        logger.warning(
            "turn-count warm: {} sessions in {:.1f}s (complete={})",
            filled.counted,
            time.monotonic() - started,
            filled.complete,
        )
