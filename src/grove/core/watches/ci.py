"""Observe CI checks for one immutable commit SHA."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.watches import CiPredicate, WatchOutcome
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import CommitChecks
from grove.core.watches.watcher import Watcher

if TYPE_CHECKING:
    from grove.core.tickets.registry import TicketProviderRegistry


class CiWatcher(Watcher[CiPredicate]):
    """Settle when a commit has checks and none remains in progress.

    A commit immediately after a push often has no checks yet. It is still
    pending, not a successful result; CI only settles after the first check
    appears and every observed check has concluded.
    """

    kind = "ci"
    _CACHE_TTL = timedelta(seconds=10)

    def __init__(self, providers: TicketProviderRegistry) -> None:
        self._providers = providers
        self._cache: dict[tuple[str, str, str, str], tuple[datetime, CommitChecks]] = {}
        self._unreadable_until: dict[tuple[str, str, str, str], datetime] = {}

    def observe(self, predicate: CiPredicate, now: datetime) -> WatchOutcome | None:
        key = (predicate.provider, predicate.owner, predicate.repo, predicate.head_sha)
        checks = self._cached(key, predicate, now)
        if checks is None or not checks.checks or checks.running:
            return None
        passed = [check.name for check in checks.checks if check.passed]
        failed = [check.name for check in checks.checks if not check.passed]
        summary = self._summary(predicate, passed, failed)
        return WatchOutcome(ok=not failed, summary=summary, url=checks.url)

    def _cached(
        self,
        key: tuple[str, str, str, str],
        predicate: CiPredicate,
        now: datetime,
    ) -> CommitChecks | None:
        entry = self._cache.get(key)
        if entry is not None and now - entry[0] < self._CACHE_TTL:
            return entry[1]
        unreadable_until = self._unreadable_until.get(key)
        if unreadable_until is not None and now < unreadable_until:
            return None
        try:
            checks = self._providers.get(predicate.provider).get_commit_checks(
                predicate.owner, predicate.repo, predicate.head_sha
            )
        except TicketProviderError as exc:
            logger.warning(
                "could not read CI checks for {}/{}/{} from {}: {}",
                predicate.owner,
                predicate.repo,
                predicate.head_sha,
                predicate.provider,
                exc,
            )
            self._unreadable_until[key] = now + self._CACHE_TTL
            return None
        self._cache[key] = (now, checks)
        self._unreadable_until.pop(key, None)
        return checks

    @staticmethod
    def _summary(predicate: CiPredicate, passed: list[str], failed: list[str]) -> str:
        subject = f"CI checks for {predicate.owner}/{predicate.repo}@{predicate.head_sha}"
        parts: list[str] = []
        if passed:
            parts.append(f"passed: {', '.join(passed)}")
        if failed:
            parts.append(f"failed: {', '.join(failed)}")
        return f"{subject} completed — {'; '.join(parts)}."


__all__ = ["CiWatcher"]
