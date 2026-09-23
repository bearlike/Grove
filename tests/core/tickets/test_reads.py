"""The shared ticket-read admission window."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Barrier, BrokenBarrierError, Event, Lock

import pytest

from grove.core.contracts.tickets import TicketKind, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import TicketState
from grove.core.tickets.reads import TicketReads


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _Provider:
    name = "gitea"
    context = "org/repo"

    def __init__(self) -> None:
        self.calls = 0

    def read_state(self, ticket_id: str, kind: TicketKind) -> TicketState:
        self.calls += 1
        return _state(ticket_id, kind)


def _state(ticket_id: str, kind: TicketKind) -> TicketState:
    return TicketState(
        ref=TicketRef(provider="gitea", id=ticket_id, kind=kind),
        body="body",
        comment_count=0,
    )


def test_reads_one_ticket_once_when_two_consumers_miss_together() -> None:
    """A fetch admission released before I/O lets both consumers call the forge."""

    class _OverlappingProvider(_Provider):
        def __init__(self) -> None:
            super().__init__()
            self._calls_lock = Lock()
            self._attempts = Barrier(2, timeout=1)

        def read_state(self, ticket_id: str, kind: TicketKind) -> TicketState:
            with self._calls_lock:
                self.calls += 1
            with suppress(BrokenBarrierError):
                self._attempts.wait()
            return _state(ticket_id, kind)

    reads = TicketReads()
    provider = _OverlappingProvider()
    consumers = Barrier(2, timeout=1)

    def read() -> TicketState:
        consumers.wait()
        return reads.state(provider, "42", "issue")

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: read(), range(2)))

    assert states == [_state("42", "issue"), _state("42", "issue")]
    assert provider.calls == 1


def test_reads_an_unrelated_ticket_while_a_fetch_is_in_flight() -> None:
    """A slow forge call for one key must not hold the cache-wide admission lock."""

    started = Event()
    release = Event()

    class _SlowProvider(_Provider):
        def read_state(self, ticket_id: str, kind: TicketKind) -> TicketState:
            self.calls += 1
            if ticket_id == "slow":
                started.set()
                assert release.wait(timeout=1)
            return _state(ticket_id, kind)

    reads = TicketReads()
    provider = _SlowProvider()

    with ThreadPoolExecutor(max_workers=1) as pool:
        slow = pool.submit(reads.state, provider, "slow", "issue")
        assert started.wait(timeout=1)
        assert reads.state(provider, "fast", "issue") == _state("fast", "issue")
        release.set()
        assert slow.result(timeout=1) == _state("slow", "issue")

    assert provider.calls == 2


def test_reads_keep_all_fresh_tickets_when_the_active_window_has_over_512_keys() -> None:
    """Evicting a fresh entry to honor capacity violates its 60-second admission window."""

    reads = TicketReads()
    provider = _Provider()

    for ticket_number in range(513):
        reads.state(provider, str(ticket_number), "issue")

    reads.state(provider, "0", "issue")

    assert provider.calls == 513


def test_reads_drop_expired_entries_without_a_later_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quiet daemon must release a completed entry at its deadline."""

    callbacks: list[object] = []

    class _ExpiryTimer:
        def __init__(self, _: float, callback: object) -> None:
            callbacks.append(callback)
            self.daemon = False

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr("grove.core.tickets.reads.Timer", _ExpiryTimer)
    clock = _Clock()
    reads = TicketReads(clock=clock)
    provider = _Provider()

    reads.state(provider, "42", "issue")
    clock.advance(60)

    callback = callbacks.pop()
    assert callable(callback)
    callback()

    assert not reads._entries


def test_reads_normalize_and_replay_an_untyped_provider_failure() -> None:
    """The shared admission layer must not leak an adapter's untyped exception."""

    class _BrokenProvider(_Provider):
        def read_state(self, ticket_id: str, kind: TicketKind) -> TicketState:
            self.calls += 1
            raise ValueError("unexpected provider payload")

    reads = TicketReads()
    provider = _BrokenProvider()

    with pytest.raises(TicketProviderError, match="ticket state read failed"):
        reads.state(provider, "42", "issue")
    with pytest.raises(TicketProviderError, match="ticket state read failed"):
        reads.state(provider, "42", "issue")

    assert provider.calls == 1


def test_reads_replay_a_typed_failure_until_sixty_seconds_after_completion() -> None:
    """A provider failure consumes the same admission window as a successful fetch."""

    class _FailingProvider(_Provider):
        def __init__(self, clock: _Clock) -> None:
            super().__init__()
            self._clock = clock

        def read_state(self, ticket_id: str, kind: TicketKind) -> TicketState:
            self.calls += 1
            self._clock.advance(59)
            raise TicketProviderError("forge unavailable")

    clock = _Clock()
    reads = TicketReads(clock=clock)
    provider = _FailingProvider(clock)

    with pytest.raises(TicketProviderError, match="forge unavailable"):
        reads.state(provider, "42", "issue")

    clock.advance(1)

    with pytest.raises(TicketProviderError, match="forge unavailable"):
        reads.state(provider, "42", "issue")

    assert provider.calls == 1
