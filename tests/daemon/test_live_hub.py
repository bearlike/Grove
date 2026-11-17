"""The live-step relay is demand-driven: no viewer, no watch, no pulse."""

from __future__ import annotations

import asyncio

from grove.core.contracts.live import LivePulseView
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import NativeOwnerRegistry, OwnerIdentity
from grove.daemon._live import LiveHub

WS = "a" * 32


def _owner(registry: NativeOwnerRegistry, *, live: bool):
    identity = OwnerIdentity(address=MailboxAddress(workspace_id=WS), generation="b" * 32)
    return registry.register(identity, "session-1", live=live)


def _watch_frames(binding) -> list[str]:
    frames = []
    while not binding.queue.empty():
        _, _, frame = binding.queue.get_nowait()
        if frame is not None and frame.op == "watch":
            frames.append(frame.text)
    return frames


async def test_first_viewer_starts_and_last_viewer_stops_the_watch() -> None:
    registry = NativeOwnerRegistry()
    binding = _owner(registry, live=True)
    hub = LiveHub(registry)

    first = hub.join(WS)
    second = hub.join(WS)
    hub.leave(WS, first)
    assert _watch_frames(binding) == ["on"]

    hub.leave(WS, second)
    assert _watch_frames(binding) == ["off"]
    assert not hub.watched(WS)


async def test_an_owner_that_did_not_declare_live_is_never_sent_a_watch() -> None:
    """An older worker queues an unknown op as PROMPT TEXT, so a watch sent to
    one would type "on" into the agent's conversation."""
    registry = NativeOwnerRegistry()
    binding = _owner(registry, live=False)
    hub = LiveHub(registry)

    hub.leave(WS, hub.join(WS))
    assert _watch_frames(binding) == []


async def test_a_pulse_reaches_every_viewer_and_a_late_one_gets_the_latest() -> None:
    registry = NativeOwnerRegistry()
    _owner(registry, live=True)
    hub = LiveHub(registry)
    early = hub.join(WS)

    hub.publish(WS, LivePulseView(step="thinking", thinking_tokens=10))
    hub.publish(WS, LivePulseView(step="tool", tool="Bash"))

    # A slow viewer keeps only the newest pulse, never a backlog.
    assert (await asyncio.wait_for(early.get(), 1)).tool == "Bash"
    late = hub.join(WS)
    assert (await asyncio.wait_for(late.get(), 1)).tool == "Bash"


async def test_a_pulse_with_no_viewer_is_dropped() -> None:
    registry = NativeOwnerRegistry()
    _owner(registry, live=True)
    hub = LiveHub(registry)

    hub.publish(WS, LivePulseView(step="thinking"))
    queue = hub.join(WS)
    assert queue.empty()


async def test_a_reconnecting_owner_is_re_told_about_an_open_watch() -> None:
    registry = NativeOwnerRegistry()
    hub = LiveHub(registry)
    hub.join(WS)  # viewer arrives while no owner is connected
    binding = _owner(registry, live=True)

    hub.rewatch(WS)
    assert _watch_frames(binding) == ["on"]
