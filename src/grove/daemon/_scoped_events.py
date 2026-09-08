"""Capability streams carry invalidations, never private fleet payloads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.admission import AdmissionLimits, BoundedInbox
from grove.core.workspace import WorkspaceState


class ScopedWorkspaceEvents:
    """Keep one bounded invalidation slot for an already-authorized workspace.

    The resolver rechecks revocation before every publication. Expiry is a
    semantic deadline, not a discovery timer. No private row or repository field
    is ever serialized here.
    """

    def __init__(
        self,
        activity: ActivityService,
        state: WorkspaceState,
        authorize: Callable[[], WorkspaceState],
    ) -> None:
        self._activity = activity
        self._workspace_id = state.id
        self._expires_at = state.share_expires_at
        self._authorize = authorize
        self._inbox = BoundedInbox[int](AdmissionLimits(max_items=2, max_bytes=32))

    async def events(self) -> AsyncIterator[str]:
        self._inbox.bind()

        def changed(delta: DashboardDelta) -> None:
            if delta.workspace_id == self._workspace_id:
                self._inbox.offer(delta.seq, size_bytes=8, key=self._workspace_id)

        unsubscribe = self._activity.subscribe(changed)
        try:
            await asyncio.to_thread(self._authorize)
            yield "event: snapshot\ndata: {}\n\n"
            while True:
                timeout = 15.0
                if self._expires_at is not None:
                    remaining = (self._expires_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        return
                    timeout = min(timeout, remaining)
                try:
                    delivery = await asyncio.wait_for(self._inbox.take(), timeout=timeout)
                except TimeoutError:
                    if self._expires_at is not None and datetime.now(UTC) >= self._expires_at:
                        return
                    yield "event: heartbeat\ndata: {}\n\n"
                    continue
                try:
                    await asyncio.to_thread(self._authorize)
                    yield "event: changed\ndata: {}\n\n"
                finally:
                    self._inbox.complete(delivery)
        finally:
            unsubscribe()
            self._inbox.close()
