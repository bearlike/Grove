"""Cancellation cannot release admission while a side effect is still running."""

import asyncio
from threading import Event

import pytest

from grove.core.errors import WorkCapacityExceeded
from grove.daemon._lifecycle import _LifecycleRunner


@pytest.mark.asyncio
async def test_disconnected_request_keeps_its_capacity_and_key() -> None:
    runner = _LifecycleRunner(max_workers=1, max_pending=1)
    entered, release = Event(), Event()

    def operation() -> None:
        entered.set()
        release.wait(2)

    task = asyncio.create_task(runner.run("workspace", operation))
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(WorkCapacityExceeded):
            await runner.run("other", lambda: None)
        release.set()
        async with asyncio.timeout(1):
            while runner._pending:
                await asyncio.sleep(0)
        assert await runner.run("other", lambda: 7) == 7
    finally:
        release.set()
        runner.shutdown()
