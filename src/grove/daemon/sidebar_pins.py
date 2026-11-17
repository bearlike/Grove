"""HTTP over the host-wide sidebar pin list. No logic beyond the existence gate."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from grove.core.errors import WorkspaceNotFound
from grove.core.sidebar_pins import SidebarPinStore
from grove.core.store import JsonWorkspaceStore


class SidebarPinRouter:
    """Builds the sidebar pin router for this daemon instance."""

    def __init__(
        self,
        *,
        pins: SidebarPinStore,
        workspaces: JsonWorkspaceStore,
        auth_dep: Callable[..., object],
    ) -> None:
        self._pins = pins
        self._workspaces = workspaces
        self._auth_dep = auth_dep

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/sidebar/pins", tags=["sidebar"])
        auth = Depends(self._auth_dep)

        def _listing() -> list[str]:
            # Filtered to records that still exist: a killed workspace is no
            # longer displayed.
            live = {s.id for s in self._workspaces.load_all()}
            return [i for i in self._pins.pinned() if i in live]

        def _pin(workspace_id: str) -> list[str]:
            self._workspaces.get(workspace_id)
            self._pins.pin(workspace_id)
            return _listing()

        def _unpin(workspace_id: str) -> list[str]:
            self._pins.unpin(workspace_id)
            return _listing()

        # Store calls take a cross-process file lock, so every handler offloads.
        @router.get("")
        async def listing(_: object = auth) -> list[str]:
            return await asyncio.to_thread(_listing)

        @router.put("/{workspace_id}")
        async def pin(
            workspace_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> list[str]:
            try:
                return await asyncio.to_thread(_pin, workspace_id)
            except WorkspaceNotFound as exc:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "workspace_not_found",
                        "message": f"no workspace with id {workspace_id!r}",
                    },
                ) from exc

        @router.delete("/{workspace_id}")
        async def unpin(
            workspace_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
            _: object = auth,
        ) -> list[str]:
            return await asyncio.to_thread(_unpin, workspace_id)

        return router
