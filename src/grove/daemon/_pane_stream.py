"""SSE projection of the shared event-driven pane owner.

The core owner reads one tmux control-mode stream per pane and resnapshots only
on its output/layout edges.  This module turns its bounded snapshots into the
existing SSE wire frame; it does not own terminal observation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

from grove.core.contracts.activity import DashboardEvent
from grove.core.contracts.views import WorkspacePaneView
from grove.core.pane_events import PaneSubscription


class _PaneStreamer:
    """Project one shared pane subscription onto the established SSE contract."""

    def __init__(
        self,
        *,
        workspace_id: str,
        subscription: PaneSubscription,
        next_seq: Callable[[], int],
    ) -> None:
        self._workspace_id = workspace_id
        self._subscription = subscription
        self._next_seq = next_seq
        self._last_ansi: str | None | object = _UNSET

    async def events(self) -> AsyncGenerator[DashboardEvent | None, None]:
        """Yield changed snapshots; unchanged resyncs remain lightweight beats."""
        try:
            async for snapshot in self._subscription.events():
                if snapshot.ansi != self._last_ansi:
                    self._last_ansi = snapshot.ansi
                    yield DashboardEvent.pane_event(
                        WorkspacePaneView.from_capture(
                            self._workspace_id, snapshot.ansi, snapshot.taken_at
                        ),
                        seq=self._next_seq(),
                    )
                else:
                    yield None
        finally:
            await self._subscription.aclose()


_UNSET: object = object()
