"""HTTP over the watch scheduler. Three verbs and no logic of its own.

The router is deliberately thinner than the mailbox's: registration, listing and
cancellation are all engine decisions, so every handler here resolves auth,
offloads and returns. Anything that looked like policy in this file would be a
second opinion about a question :mod:`grove.core.watches` already answers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from grove.core.contracts.watches import (
    WatchList,
    WatchRegistration,
    WatchView,
)
from grove.core.watches import WatchScheduler


class WatchRouter:
    """Builds the sole watch router for this daemon instance."""

    def __init__(
        self,
        *,
        scheduler: WatchScheduler,
        auth_dep: Callable[..., object],
    ) -> None:
        self._scheduler = scheduler
        self._auth_dep = auth_dep

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/watches", tags=["watches"])
        auth = Depends(self._auth_dep)

        # Every handler offloads: the durable log takes a cross-process lock, so
        # a registration racing a `grove watch` CLI verb would otherwise block
        # the event loop on another process's file lock.
        @router.post("", response_model=WatchView, status_code=201)
        async def register(request: WatchRegistration, _: object = auth) -> WatchView:
            # Deliberately takes NO lifecycle hold, unlike the mailbox send. A
            # watch names a recipient it may not deliver to for hours; holding
            # that workspace's lock across the registration would serialize an
            # agent's own lifecycle against a promise about its future.
            return await asyncio.to_thread(self._scheduler.register, request)

        @router.get("", response_model=WatchList)
        async def listing(
            workspace: Annotated[str | None, Query(pattern=r"^[a-f0-9]{32}$")] = None,
            _: object = auth,
        ) -> WatchList:
            return await asyncio.to_thread(self._scheduler.list, workspace_id=workspace)

        @router.delete("/{watch_id}", response_model=WatchView)
        async def cancel(
            watch_id: Annotated[str, Path(pattern=r"^wch_[a-f0-9]{32}$")],
            _: object = auth,
        ) -> WatchView:
            settled = await asyncio.to_thread(self._scheduler.cancel, watch_id)
            if settled is None:
                raise HTTPException(status_code=404, detail={"error": "watch_not_found"})
            return settled

        return router


__all__ = ["WatchRouter"]
