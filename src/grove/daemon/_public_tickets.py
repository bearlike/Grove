"""Bounded ticket enrichment for the unauthenticated share reader.

A public overview is polled, and resolving every stored ref on every request
would let many anonymous readers multiply one host credential into a tracker
request storm. The daemon does the resolution itself, after the share token
already scoped the workspace, then this memo collapses every reader of that
project's ref into one upstream call per minute.

The project root is part of the key even though a ``TicketRef`` names only a
provider and id. Providers are constructed from per-project configuration, so
two projects can legitimately bind ``gitea:42`` to different trackers or
repositories; sharing the cached answer would disclose the other project's
ticket metadata.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from loguru import logger

from grove.core.contracts.tickets import TicketRef

_PUBLIC_TICKET_TTL_SECONDS: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class _TicketMemoEntry:
    resolved: TicketRef | None
    fetched_at: float


class _PublicTicketMemo:
    """Single-flight TTL cache for public ticket display enrichment.

    Blocking by contract: the public route runs its reader through
    :func:`asyncio.to_thread`, and a resolver makes a bounded provider HTTP
    request there. Failures are memoized as ``None`` too: retrying a down
    tracker for every five-second public poll would defeat the bound precisely
    when the provider is least able to answer.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _PUBLIC_TICKET_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str], _TicketMemoEntry] = {}

    @staticmethod
    def _key(repo_root: Path, ref: TicketRef) -> tuple[str, str, str]:
        """The repo-scoped identity of a ticket-resolution cache entry.

        ``provider:id`` only identifies a stored ref within a repository: each
        manager binds its provider to that repository's tracker configuration.
        Omitting the canonical root here would let a daemon-wide memo disclose
        one project's ticket metadata through another project's public link.
        """
        return str(repo_root.resolve()), ref.provider, ref.id

    def resolve(
        self,
        *,
        repo_root: Path,
        ref: TicketRef,
        fetch: Callable[[], TicketRef],
    ) -> TicketRef | None:
        """Fetch one ref at most once per TTL, returning ``None`` on failure."""
        key = self._key(repo_root, ref)
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and (now - entry.fetched_at) < self._ttl:
                return entry.resolved
            try:
                resolved = fetch()
            except Exception as exc:  # public ticket display is always best-effort
                logger.warning(
                    "public ticket resolution failed for {}:{}: {}",
                    ref.provider,
                    ref.id,
                    type(exc).__name__,
                )
                resolved = None
            else:
                logger.debug("public ticket resolved for {}:{}", ref.provider, ref.id)
            self._entries[key] = _TicketMemoEntry(resolved=resolved, fetched_at=now)
            return resolved
