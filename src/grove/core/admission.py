"""Reserve work before crossing a thread boundary; retain it until completion."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field


class AdmissionLimits(BaseModel):
    """Capacity includes work already handed to consumers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_items: int = Field(default=256, gt=0)
    max_bytes: int = Field(default=4 * 1024 * 1024, gt=0)


class Admission(StrEnum):
    ACCEPTED = "accepted"
    COALESCED = "coalesced"
    FULL = "full"
    TOO_LARGE = "too_large"
    NOT_READY = "not_ready"
    CLOSED = "closed"


class InboxClosed(Exception):
    """No further deliveries can be taken from this owner."""


@dataclass(frozen=True, slots=True)
class Delivery[T]:
    value: T
    size_bytes: int
    admitted_at: float
    token: int
    key: str | None


@dataclass(frozen=True, slots=True)
class AdmissionStats:
    items: int
    bytes: int
    pending: int
    in_flight: int
    oldest_age_seconds: float
    wakeups: int
    rejected: int
    coalesced: int


class BoundedInbox[T]:
    """One thread-safe intake and loop-owned consumption boundary.

    Unkeyed work is FIFO and never replaced. Keys explicitly opt into replacing
    pending state, not commands. Taking an item removes its coalescing key, so
    an edge received during processing remains as a trailing update. Producers
    retain responsibility for refused work; close returns accepted pending work
    to its owner rather than silently discarding commands.
    """

    def __init__(
        self, limits: AdmissionLimits, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._lock = Lock()
        self._pending: OrderedDict[int, Delivery[T]] = OrderedDict()
        self._keys: dict[str, int] = {}
        self._in_flight: dict[int, Delivery[T]] = {}
        self._bytes = 0
        self._next_token = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: asyncio.Event | None = None
        self._closed = False
        self._wake_scheduled = False
        self._wakeups = 0
        self._rejected = 0
        self._coalesced = 0

    def bind(self) -> None:
        """Bind once on the consuming loop before enabling producers."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise InboxClosed
            if self._loop is not None:
                if self._loop is not loop:
                    raise RuntimeError("inbox already belongs to another event loop")
                return
            self._loop = loop
            self._ready = asyncio.Event()

    def offer(self, value: T, *, size_bytes: int, key: str | None = None) -> Admission:
        """Reserve before scheduling; size includes the payload retained by the owner."""
        if size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        with self._lock:
            if self._closed:
                return Admission.CLOSED
            if self._loop is None:
                return Admission.NOT_READY
            if size_bytes > self._limits.max_bytes:
                self._rejected += 1
                return Admission.TOO_LARGE
            old_token = self._keys.get(key) if key is not None else None
            old = self._pending.get(old_token) if old_token is not None else None
            items = len(self._pending) + len(self._in_flight)
            new_bytes = self._bytes + size_bytes - (old.size_bytes if old else 0)
            if new_bytes > self._limits.max_bytes or (
                old is None and items >= self._limits.max_items
            ):
                self._rejected += 1
                return Admission.FULL
            token = old.token if old else self._next_token
            if old is None:
                self._next_token += 1
            self._pending[token] = Delivery(
                value, size_bytes, old.admitted_at if old else self._clock(), token, key
            )
            if key is not None:
                self._keys[key] = token
            self._bytes = new_bytes
            if old:
                self._coalesced += 1
            self._schedule_wake()
            return Admission.COALESCED if old else Admission.ACCEPTED

    def _schedule_wake(self) -> None:
        # One outstanding callback regardless of producer count or burst size.
        if not self._wake_scheduled and self._loop is not None:
            self._wake_scheduled = True
            self._wakeups += 1
            self._loop.call_soon_threadsafe(self._wake)

    def _wake(self) -> None:
        with self._lock:
            self._wake_scheduled = False
            if self._ready is not None:
                self._ready.set()

    async def take(self) -> Delivery[T]:
        """Transfer work without releasing its reservation."""
        if asyncio.get_running_loop() is not self._loop or self._ready is None:
            raise RuntimeError("take must run on the bound event loop")
        while True:
            with self._lock:
                if self._pending:
                    token, delivery = self._pending.popitem(last=False)
                    if delivery.key is not None:
                        del self._keys[delivery.key]
                    self._in_flight[token] = delivery
                    return delivery
                if self._closed:
                    raise InboxClosed
                self._ready.clear()
            await self._ready.wait()

    def complete(self, delivery: Delivery[T]) -> None:
        """Release only the exact delivery issued by this inbox."""
        with self._lock:
            if self._in_flight.get(delivery.token) is not delivery:
                raise ValueError("delivery is not in flight in this inbox")
            del self._in_flight[delivery.token]
            self._bytes -= delivery.size_bytes

    def close(self) -> tuple[Delivery[T], ...]:
        """Stop intake and return undelivered work; running work still owns its budget."""
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._keys.clear()
            self._bytes -= sum(item.size_bytes for item in pending)
            self._schedule_wake()
            return pending

    def stats(self) -> AdmissionStats:
        with self._lock:
            oldest = min(
                (item.admitted_at for item in (*self._pending.values(), *self._in_flight.values())),
                default=self._clock(),
            )
            return AdmissionStats(
                items=len(self._pending) + len(self._in_flight),
                bytes=self._bytes,
                pending=len(self._pending),
                in_flight=len(self._in_flight),
                oldest_age_seconds=max(0.0, self._clock() - oldest),
                wakeups=self._wakeups,
                rejected=self._rejected,
                coalesced=self._coalesced,
            )
