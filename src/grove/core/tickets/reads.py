"""One shared, short-lived admission window for ticket reads.

The sticky status publisher and ticket watcher both ask a forge for a ticket's
current state. This module is below both consumers, so its per-ticket window is
the only place that can enforce their combined request budget.

A key remains present while its read is in flight and for 60 seconds after that
read finishes, whether it returned a state or a typed provider error. This is
both a single flight and a throttle: concurrent misses join the existing read,
and an outage cannot turn consumers' retries into repeated forge requests.

Entries expire at their deadline, including during an otherwise idle daemon.
Evicting a fresh entry would violate the admission guarantee, so a fleet with
more than 512 tickets active in one window retains all of them until they
expire. Memory therefore follows active keys in the TTL, rather than every key
the daemon has ever seen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Timer
from typing import ClassVar

from grove.core.contracts.tickets import TicketKind, TicketProviderName
from grove.core.errors import GroveError, TicketProviderError
from grove.core.tickets.provider import TicketProvider, TicketState

#: A cache key names the ticket on its FORGE, not in one repo's list. The
#: provider's ``context`` (``owner/repo``) is part of it because two repos on one
#: forge number their issues independently, and ``#42`` in each is a different
#: ticket.
_Key = tuple[TicketProviderName, str | None, str, TicketKind]


@dataclass(slots=True)
class _Entry:
    """One pending or completed provider outcome, shared by callers of one key."""

    completed_at: datetime | None = None
    state: TicketState | None = None
    error: GroveError | None = None
    completed: Event = field(default_factory=Event)

    def result(self) -> TicketState:
        """Return the read state or re-raise its original provider failure."""
        if self.error is not None:
            raise self.error
        if self.state is None:  # defensive: completion always publishes one outcome
            raise RuntimeError("ticket read completed without an outcome")
        return self.state


class TicketReads:
    """Read one ticket once per completion-based TTL, without blocking other keys.

    Thread-safe. A caller owns only its key's fetch; all other cache coordination
    holds ``_lock`` briefly, while callers of that key wait on its entry's event.
    A slow forge therefore cannot delay a cache hit or miss for an unrelated
    ticket.
    """

    TTL: ClassVar[timedelta] = timedelta(seconds=60)
    """How long a completed provider attempt is reused after it finishes."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock if clock is not None else _utcnow
        self._entries: dict[_Key, _Entry] = {}
        self._lock = Lock()
        self._expiry: Timer | None = None

    def state(self, provider: TicketProvider, ticket_id: str, kind: TicketKind) -> TicketState:
        """Return a ticket state or replay its provider failure during the read window."""
        key: _Key = (provider.name, provider.context, ticket_id, kind)
        now = self._clock()
        with self._lock:
            self._discard_expired(now)
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry()
                self._entries[key] = entry
                fetch = True
            elif entry.completed_at is None:
                fetch = False
            else:
                return entry.result()

        if not fetch:
            entry.completed.wait()
            return entry.result()

        try:
            fresh = provider.read_state(ticket_id, kind)
        except GroveError as error:
            self._complete(entry, error=error)
            raise
        except Exception:
            typed_error = TicketProviderError("ticket state read failed")
            self._complete(entry, error=typed_error)
            raise typed_error from None
        self._complete(entry, state=fresh)
        return fresh

    def _complete(
        self,
        entry: _Entry,
        *,
        state: TicketState | None = None,
        error: GroveError | None = None,
    ) -> None:
        """Publish an outcome at fetch completion, then wake callers of its key."""
        with self._lock:
            entry.completed_at = self._clock()
            entry.state = state
            entry.error = error
            self._discard_expired(entry.completed_at)
            self._schedule_expiry()
            entry.completed.set()

    def _schedule_expiry(self) -> None:
        """Schedule one cleanup at the nearest expiry without retaining idle keys."""
        if self._expiry is not None:
            return
        deadlines = [entry.completed_at for entry in self._entries.values() if entry.completed_at]
        if not deadlines:
            return
        delay = max((min(deadlines) + self.TTL - self._clock()).total_seconds(), 0)
        self._expiry = Timer(delay, self._expire)
        self._expiry.daemon = True
        self._expiry.start()

    def _expire(self) -> None:
        """Release completed windows even if no caller visits the cache."""
        with self._lock:
            self._expiry = None
            self._discard_expired(self._clock())
            self._schedule_expiry()

    def _discard_expired(self, now: datetime) -> None:
        """Drop completed windows that no longer throttle a future provider call."""
        for key, entry in list(self._entries.items()):
            if entry.completed_at is not None and now - entry.completed_at >= self.TTL:
                del self._entries[key]


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["TicketReads"]
