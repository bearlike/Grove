"""Shared pane event owner: no periodic terminal capture per viewer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime

import pytest

from grove.core.admission import AdmissionLimits
from grove.core.pane_events import (
    PaneEventHub,
    PaneEventsUnavailable,
    PaneKey,
    PaneSnapshot,
    PaneTrigger,
    TmuxControlPaneSource,
)


class _Source:
    def __init__(self) -> None:
        self.events_to_emit: asyncio.Queue[PaneTrigger | None] = asyncio.Queue()
        self.closed = False

    async def events(self) -> AsyncIterator[PaneTrigger]:
        while (event := await self.events_to_emit.get()) is not None:
            yield event

    async def aclose(self) -> None:
        self.closed = True
        self.events_to_emit.put_nowait(None)


async def _next(events: AsyncGenerator[PaneSnapshot, None]) -> PaneSnapshot:
    return await anext(events)


async def test_two_viewers_share_source_and_one_initial_capture() -> None:
    source = _Source()
    factories = 0
    captures = 0

    async def capture() -> PaneSnapshot:
        nonlocal captures
        captures += 1
        return PaneSnapshot("\x1b[31mready", datetime.now(UTC))

    def factory() -> _Source:
        nonlocal factories
        factories += 1
        return source

    hub = PaneEventHub()
    key = PaneKey("workspace")
    one = hub.subscribe(key, capture=capture, source=factory)
    two = hub.subscribe(key, capture=capture, source=factory)
    one_events = one.events()
    two_events = two.events()
    first, second = await asyncio.gather(_next(one_events), _next(two_events))

    assert factories == 1
    assert captures == 1
    assert first.ansi == second.ansi == "\x1b[31mready"

    await one_events.aclose()
    assert not source.closed
    await two_events.aclose()
    assert source.closed


async def test_output_burst_coalesces_to_one_trailing_resnapshot() -> None:
    source = _Source()
    entered_capture = asyncio.Event()
    release_capture = asyncio.Event()
    captures = 0

    async def capture() -> PaneSnapshot:
        nonlocal captures
        captures += 1
        if captures == 1:
            entered_capture.set()
            await release_capture.wait()
        return PaneSnapshot(str(captures), datetime.now(UTC))

    hub = PaneEventHub()
    subscription = hub.subscribe(PaneKey("workspace"), capture=capture, source=lambda: source)
    events = subscription.events()
    initial = asyncio.create_task(anext(events))
    await entered_capture.wait()
    source.events_to_emit.put_nowait(PaneTrigger.OUTPUT)
    source.events_to_emit.put_nowait(PaneTrigger.OUTPUT)
    source.events_to_emit.put_nowait(PaneTrigger.RESIZE)
    release_capture.set()

    assert (await initial).ansi == "1"
    assert (await anext(events)).ansi == "2"
    await asyncio.sleep(0)
    assert captures == 2
    await events.aclose()


async def test_slow_viewer_receives_latest_whole_snapshot_after_releasing_old_one() -> None:
    source = _Source()
    current = "first"

    async def capture() -> PaneSnapshot:
        return PaneSnapshot(current, datetime.now(UTC))

    hub = PaneEventHub(subscriber_limits=AdmissionLimits(max_items=1, max_bytes=1024))
    subscription = hub.subscribe(PaneKey("workspace"), capture=capture, source=lambda: source)
    events = subscription.events()
    assert (await anext(events)).ansi == "first"

    current = "second"
    source.events_to_emit.put_nowait(PaneTrigger.OUTPUT)
    current = "third"
    source.events_to_emit.put_nowait(PaneTrigger.OUTPUT)
    await asyncio.sleep(0)
    assert (await anext(events)).ansi == "third"
    await events.aclose()


async def test_source_disconnect_closes_subscribers_and_removes_owner() -> None:
    source = _Source()

    async def capture() -> PaneSnapshot:
        return PaneSnapshot("frame", datetime.now(UTC))

    hub = PaneEventHub()
    subscription = hub.subscribe(PaneKey("workspace"), capture=capture, source=lambda: source)
    events = subscription.events()
    assert (await anext(events)).ansi == "frame"
    source.events_to_emit.put_nowait(None)
    with pytest.raises(StopAsyncIteration):
        await anext(events)

    replacement_source = _Source()
    replacement = hub.subscribe(
        PaneKey("workspace"), capture=capture, source=lambda: replacement_source
    )
    replacement_events = replacement.events()
    assert (await anext(replacement_events)).ansi == "frame"
    await replacement_events.aclose()


def test_control_source_refuses_malformed_explicit_pane_id() -> None:
    with pytest.raises(PaneEventsUnavailable, match="pane-id target"):
        TmuxControlPaneSource(target="workspace:agent", pane_id="agent")


def test_control_source_adds_docker_interactive_before_container_id() -> None:
    source = TmuxControlPaneSource(
        target="agent",
        pane_id="%42",
        command=("docker", "exec", "-u", "agent", "container", "tmux"),
    )

    assert source._pane_id == "%42"
    assert source._prefixed_control_command() == (
        "docker",
        "exec",
        "-u",
        "agent",
        "-i",
        "container",
        "tmux",
    )


def test_control_output_filters_sibling_panes() -> None:
    source = TmuxControlPaneSource(target="workspace:agent", pane_id="%42")

    own = source._OUTPUT.match("%output %42 agent-output")
    sibling = source._OUTPUT.match("%output %41 sibling-output")

    assert own is not None and own.group("pane") == source._pane_id
    assert sibling is not None and sibling.group("pane") != source._pane_id
