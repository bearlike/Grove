"""HTTP for the fleet digest and for one message to many workspaces.

Both routes are thin. What a row says and how a batch is delivered are engine
decisions in :mod:`grove.core.fleet_digest`; this module resolves auth,
validates the query, offloads, and maps refusals to status codes.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from grove.core.contracts.fleet import (
    FleetDigestView,
    MailboxBatchReceipt,
    MailboxBatchRequest,
)
from grove.core.fleet_digest import (
    BatchConflict,
    DigestCursorInvalid,
    FleetDigest,
    MailboxBatch,
)

_WORKSPACE_ID = re.compile(r"^[a-f0-9]{32}$")
#: An opaque token is base64 of a small JSON object; anything longer was not ours.
_TOKEN_MAX = 512


class FleetDigestRouter:
    """Builds the fleet digest and batch mail routes for this daemon instance."""

    def __init__(
        self,
        *,
        digest: FleetDigest,
        batch: MailboxBatch,
        auth_dep: Callable[..., object],
    ) -> None:
        self._digest = digest
        self._batch = batch
        self._auth_dep = auth_dep

    def router(self) -> APIRouter:
        router = APIRouter(tags=["fleet"])
        auth = Depends(self._auth_dep)

        @router.get("/fleet/digest", response_model=FleetDigestView)
        async def digest(
            _: object = auth,
            since: Annotated[str | None, Query(min_length=1, max_length=_TOKEN_MAX)] = None,
            page: Annotated[str | None, Query(min_length=1, max_length=_TOKEN_MAX)] = None,
            workspaces: Annotated[str | None, Query(max_length=33 * 500)] = None,
        ) -> FleetDigestView:
            selection = self._selection(workspaces)
            try:
                read = await asyncio.to_thread(
                    self._digest.read, since=since, page=page, workspaces=selection
                )
            except DigestCursorInvalid as exc:
                raise HTTPException(
                    status_code=422, detail={"error": "invalid_digest_token", "message": str(exc)}
                ) from exc
            return read.view

        @router.post("/mailboxes/batch", response_model=MailboxBatchReceipt)
        async def send_batch(request: MailboxBatchRequest, _: object = auth) -> MailboxBatchReceipt:
            try:
                return await self._batch.send(request)
            except BatchConflict as exc:
                raise HTTPException(
                    status_code=409, detail={"error": "batch_conflict", "message": str(exc)}
                ) from exc

        return router

    @staticmethod
    def _selection(workspaces: str | None) -> list[str] | None:
        """The explicit ids a read is narrowed to. A malformed one is a 422, never ignored."""
        if workspaces is None:
            return None
        ids = [part.strip() for part in workspaces.split(",") if part.strip()]
        bad = [part for part in ids if not _WORKSPACE_ID.match(part)]
        if bad or not ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_workspace_ids",
                    "message": "`workspaces` must be comma-separated 32-hex workspace ids",
                },
            )
        return ids


__all__ = ["FleetDigestRouter"]
