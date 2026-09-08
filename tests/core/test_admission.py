"""Admission is bounded before crossing threads, not just inside the consumer."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from grove.core.admission import Admission, AdmissionLimits, BoundedInbox, InboxClosed


@pytest.mark.asyncio
async def test_inflight_work_keeps_its_item_and_byte_reservation() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=2, max_bytes=6))
    inbox.bind()
    assert inbox.offer("one", size_bytes=3) is Admission.ACCEPTED
    first = await inbox.take()
    assert inbox.offer("two", size_bytes=3) is Admission.ACCEPTED
    assert inbox.offer("x", size_bytes=1) is Admission.FULL
    assert inbox.stats().items == 2
    assert inbox.stats().bytes == 6
    inbox.complete(first)
    assert inbox.offer("x", size_bytes=1) is Admission.ACCEPTED
    inbox.close()


@pytest.mark.asyncio
async def test_replaceable_state_keeps_a_trailing_update_during_processing() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=2, max_bytes=10))
    inbox.bind()
    assert inbox.offer("old", size_bytes=3, key="workspace") is Admission.ACCEPTED
    first = await inbox.take()
    assert inbox.offer("next", size_bytes=4, key="workspace") is Admission.ACCEPTED
    assert inbox.offer("final", size_bytes=5, key="workspace") is Admission.COALESCED
    assert first.value == "old"
    assert inbox.stats().bytes == 8
    inbox.complete(first)
    last = await inbox.take()
    assert last.value == "final"
    inbox.complete(last)
    assert inbox.stats().items == 0
    inbox.close()


@pytest.mark.asyncio
async def test_rejected_replacement_preserves_the_accepted_value() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=2, max_bytes=6))
    inbox.bind()
    inbox.offer("first", size_bytes=3, key="a")
    inbox.offer("other", size_bytes=3, key="b")
    assert inbox.offer("too large", size_bytes=4, key="a") is Admission.FULL
    assert (await inbox.take()).value == "first"
    inbox.close()


@pytest.mark.asyncio
async def test_commands_remain_ordered_and_are_never_coalesced() -> None:
    inbox = BoundedInbox[int](AdmissionLimits(max_items=3, max_bytes=3))
    inbox.bind()
    for n in range(3):
        assert inbox.offer(n, size_bytes=1) is Admission.ACCEPTED
    assert inbox.offer(3, size_bytes=1) is Admission.FULL
    for n in range(3):
        delivery = await inbox.take()
        assert delivery.value == n
        inbox.complete(delivery)
    inbox.close()


@pytest.mark.asyncio
async def test_concurrent_producers_cannot_overbook_or_schedule_per_message() -> None:
    inbox = BoundedInbox[int](AdmissionLimits(max_items=8, max_bytes=8))
    inbox.bind()
    # Keep the event loop stationary while producer threads fill the admission boundary.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: inbox.offer(n, size_bytes=1), range(100)))
    assert results.count(Admission.ACCEPTED) == 8
    assert results.count(Admission.FULL) == 92
    assert inbox.stats().wakeups == 1
    assert inbox.stats().items == 8
    inbox.close()


@pytest.mark.asyncio
async def test_close_returns_undelivered_commands_and_wakes_waiters() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=2, max_bytes=10))
    inbox.bind()
    inbox.offer("running", size_bytes=1)
    running = await inbox.take()
    inbox.offer("pending", size_bytes=1)
    abandoned = inbox.close()
    assert [item.value for item in abandoned] == ["pending"]
    assert inbox.offer("late", size_bytes=1) is Admission.CLOSED
    inbox.complete(running)
    assert inbox.stats().items == 0
    with pytest.raises(InboxClosed):
        await inbox.take()
    assert inbox.close() == ()

    other = BoundedInbox[str](AdmissionLimits(max_items=1, max_bytes=1))
    other.bind()
    waiter = asyncio.create_task(other.take())
    await asyncio.sleep(0)
    other.close()
    with pytest.raises(InboxClosed):
        await asyncio.wait_for(waiter, timeout=1)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_or_release_work() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=1, max_bytes=1))
    inbox.bind()
    waiter = asyncio.create_task(inbox.take())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert inbox.offer("x", size_bytes=1) is Admission.ACCEPTED
    delivery = await inbox.take()
    assert delivery.value == "x"
    inbox.complete(delivery)
    with pytest.raises(ValueError, match="not in flight"):
        inbox.complete(delivery)
    inbox.close()


@pytest.mark.parametrize("items,bytes_", [(0, 1), (1, 0), (-1, 1)])
def test_invalid_capacity_is_refused_before_startup(items: int, bytes_: int) -> None:
    with pytest.raises(ValueError):
        AdmissionLimits(max_items=items, max_bytes=bytes_)


@pytest.mark.asyncio
async def test_invalid_size_and_unbound_admission_are_explicit() -> None:
    inbox = BoundedInbox[str](AdmissionLimits(max_items=1, max_bytes=4))
    assert inbox.offer("x", size_bytes=1) is Admission.NOT_READY
    inbox.bind()
    with pytest.raises(ValueError):
        inbox.offer("x", size_bytes=-1)
    assert inbox.offer("large", size_bytes=5) is Admission.TOO_LARGE
    inbox.close()


@pytest.mark.asyncio
async def test_pending_age_uses_injected_clock_and_survives_coalescing() -> None:
    now = [10.0]
    inbox = BoundedInbox[str](AdmissionLimits(max_items=2, max_bytes=8), clock=lambda: now[0])
    inbox.bind()
    inbox.offer("old", size_bytes=3, key="a")
    now[0] = 13.0
    inbox.offer("new", size_bytes=3, key="a")
    assert inbox.stats().oldest_age_seconds == 3.0
    delivery = await inbox.take()
    now[0] = 15.0
    assert inbox.stats().oldest_age_seconds == 5.0
    inbox.complete(delivery)
    assert inbox.stats().oldest_age_seconds == 0.0
    inbox.close()
