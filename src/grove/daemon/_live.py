"""Per-workspace live-step relay: worker pulse in, viewer stream out.

A native worker folds its own stream into a tiny "what am I doing now" record
and posts it ONLY while this hub says somebody is watching that workspace. The
hub counts viewers per workspace, turns the first arrival and the last departure
into a ``watch`` toggle on the owner's existing control stream, and fans each
posted pulse out to the viewers' bounded queues. Nothing here polls, persists or
rides the host-wide activity bus: with no viewer, no pulse is produced at all.

A REMOTE workspace's pane rides the same room. Its worker's output is not on
this host, so while anybody watches either the live step or the Stream tab the
worker also posts the lines it printed, and the room keeps them in a
:class:`~grove.core.pane_log.PaneLog` replica the pane stream reads. The
replica lives exactly as long as the room: the last viewer leaving drops it,
and the next watch starts with the worker's whole log.

All state is loop-owned (routes and the pulse POST run on the daemon loop), so
no lock is needed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from grove.core.contracts.live import LivePulseView
from grove.core.native_owners import NativeOwnerRegistry
from grove.core.pane_log import PaneLog

# One pulse waiting is enough: a viewer only ever wants the newest.
_VIEWER_QUEUE: int = 1


@dataclass(slots=True)
class _Room:
    viewers: set[asyncio.Queue[LivePulseView]] = field(default_factory=set)
    latest: LivePulseView | None = None
    # Each pane viewer's wake-up; set when the replica gains lines.
    pane_viewers: set[asyncio.Event] = field(default_factory=set)
    pane: PaneLog = field(default_factory=PaneLog)
    pane_at: datetime | None = None

    def empty(self) -> bool:
        return not self.viewers and not self.pane_viewers


class LiveHub:
    """Who watches which workspace's live step, and the newest pulse for each."""

    def __init__(self, owners: NativeOwnerRegistry) -> None:
        self._owners = owners
        self._rooms: dict[str, _Room] = {}

    def join(self, workspace_id: str) -> asyncio.Queue[LivePulseView]:
        """Add a viewer; the first one asks the owner to start publishing."""
        room = self._room(workspace_id)
        queue: asyncio.Queue[LivePulseView] = asyncio.Queue(_VIEWER_QUEUE)
        room.viewers.add(queue)
        if room.latest is not None:
            queue.put_nowait(room.latest)
        return queue

    def join_pane(self, workspace_id: str) -> asyncio.Event:
        """Add a pane viewer, set at once so it reads whatever the replica already holds."""
        wake = asyncio.Event()
        self._room(workspace_id).pane_viewers.add(wake)
        wake.set()
        return wake

    def leave_pane(self, workspace_id: str, wake: asyncio.Event) -> None:
        room = self._rooms.get(workspace_id)
        if room is not None:
            room.pane_viewers.discard(wake)
            self._close_if_empty(workspace_id, room)

    def pane(self, workspace_id: str) -> tuple[str | None, datetime | None]:
        """The replica's text and when it last moved; ``(None, None)`` with nothing posted."""
        room = self._rooms.get(workspace_id)
        return (None, None) if room is None else (room.pane.text(), room.pane_at)

    def publish_pane(self, workspace_id: str, first: int, lines: list[str], *, reset: bool) -> None:
        """Apply a worker's pane batch and wake every pane viewer.

        A batch for a workspace nobody watches is dropped, because the room is
        the replica's whole lifetime.

        Raises:
            PaneGap: the batch does not continue the replica.
        """
        room = self._rooms.get(workspace_id)
        if room is None:
            return
        room.pane.apply(first, lines, reset=reset)
        room.pane_at = datetime.now(UTC)
        for wake in room.pane_viewers:
            wake.set()

    def _room(self, workspace_id: str) -> _Room:
        """The workspace's room; creating it is the first viewer, which starts the watch."""
        room = self._rooms.get(workspace_id)
        if room is None:
            room = self._rooms[workspace_id] = _Room()
            self._owners.watch(workspace_id, on=True)
        return room

    def _close_if_empty(self, workspace_id: str, room: _Room) -> None:
        if room.empty():
            del self._rooms[workspace_id]
            self._owners.watch(workspace_id, on=False)

    def leave(self, workspace_id: str, queue: asyncio.Queue[LivePulseView]) -> None:
        """Remove a viewer; the last one tells the owner to stop."""
        room = self._rooms.get(workspace_id)
        if room is None or queue not in room.viewers:
            return
        room.viewers.discard(queue)
        self._close_if_empty(workspace_id, room)

    def rewatch(self, workspace_id: str) -> None:
        """Re-state an open watch to an owner that just (re)connected.

        A watch belongs to one connection, so a worker that reconnects after a
        daemon blip starts unwatched; a viewer still waiting must not go dark.
        """
        if workspace_id in self._rooms:
            self._owners.watch(workspace_id, on=True)

    def publish(self, workspace_id: str, pulse: LivePulseView) -> None:
        """Hand a posted pulse to every viewer, replacing any unread one."""
        room = self._rooms.get(workspace_id)
        if room is None:
            return
        room.latest = pulse
        for queue in room.viewers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(pulse)

    def watched(self, workspace_id: str) -> bool:
        return workspace_id in self._rooms


__all__ = ["LiveHub"]
