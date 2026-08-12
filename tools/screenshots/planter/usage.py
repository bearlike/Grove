"""Project the planted corpus into the usage cache before anything reads it."""

from __future__ import annotations

import time

from loguru import logger

from grove.core.config import GroveConfig
from grove.core.registry import RepoRegistry
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
    """

    def __init__(self, *, cfg: GroveConfig, store: JsonWorkspaceStore) -> None:
        self._cfg = cfg
        self._store = store

    def warm(self) -> None:
        started = time.monotonic()
        try:
            service = UsageService(
                cfg=self._cfg, registry=RepoRegistry(cfg=self._cfg, store=self._store)
            )
            result = service.refresh(force=False)
        except Exception as exc:  # pragma: no cover - a capture tool, not a gate
            logger.warning("usage cache warm-up failed: {}", exc)
            return
        logger.warning(
            "usage cache warm: {} sources in {:.1f}s",
            result.indexed_sources,
            time.monotonic() - started,
        )
