"""Per-workspace child-fleet snapshot streams.

A focused fleet stream consumes the existing activity bus rather than creating a
second poller. Source callbacks can run on a pool thread, so they only admit a
small refresh edge to ``BoundedInbox``; the expensive snapshot read happens on a
worker when the connected client consumes that edge.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.admission import AdmissionLimits, BoundedInbox, Delivery
from grove.core.contracts.activity import SubagentFleetView
from grove.daemon._audience import _PollAudience

_QUEUE_LIMITS = AdmissionLimits(max_items=1, max_bytes=1024)


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    """One scoped fleet replacement and whether its subject ceased to exist."""

    view: SubagentFleetView
    terminal: bool = False


class FleetSnapshotStream:
    """Coalesce relevant activity edges into off-loop scoped fleet snapshots."""

    def __init__(
        self,
        service: ActivityService,
        audience: _PollAudience,
        *,
        workspace_id: str,
        reader: Callable[[], FleetSnapshot],
    ) -> None:
        self._service = service
        self._audience = audience
        self._workspace_id = workspace_id
        self._reader = reader
        self._inbox = BoundedInbox[None](_QUEUE_LIMITS)
        self._unsub: Callable[[], None] | None = None
        self._joined = False

    def start(self) -> None:
        """Subscribe on the serving loop before the initial read can miss an edge."""
        self._inbox.bind()
        self._unsub = self._service.subscribe(self._on_delta)
        self._audience.join()
        self._joined = True

    def stop(self) -> None:
        """Release the bus subscription and poll audience membership once."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._inbox.close()
        if self._joined:
            self._audience.leave()
            self._joined = False

    async def snapshot(self) -> FleetSnapshot:
        """Read a full replacement without blocking sends or unrelated routes."""
        return await asyncio.to_thread(self._reader)

    async def changed(self) -> None:
        """Wait for one coalesced relevant edge, retaining an edge during a read."""
        delivery: Delivery[None] = await self._inbox.take()
        self._inbox.complete(delivery)

    def _on_delta(self, delta: DashboardDelta) -> None:
        if delta.workspace_id != self._workspace_id:
            return
        # Lifecycle deletion/remap and normal activity both change the reader's
        # answer. The inbox key replaces repeated ticks while a prior snapshot is
        # still crossing the worker boundary.
        if delta.kind not in {"session_activity", "workspace_changed"}:
            return
        self._inbox.offer(None, size_bytes=0, key=self._workspace_id)


def fleet_sse_frame(view: SubagentFleetView) -> str:
    """One named fleet snapshot event; snapshots intentionally have no resume id."""
    return f"event: fleet_snapshot\ndata: {view.model_dump_json()}\n\n"


def fleet_heartbeat_frame(session_id: str | None) -> str:
    """A named beat with data, required for EventSource listener delivery."""
    return f"event: heartbeat\ndata: {json.dumps({'session_id': session_id})}\n\n"
