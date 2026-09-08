"""The container-facing listener cannot expose the rest of the daemon."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, Header, HTTPException

from grove.daemon.mailbox_socket import MailboxSocket

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="Unix socket ownership")


@pytest.mark.asyncio
async def test_private_socket_only_exposes_authenticated_router(tmp_path: Path) -> None:
    router = APIRouter()

    @router.get("/mailboxes/peers")
    async def peers(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        if authorization != "Bearer synthetic":
            raise HTTPException(401)
        return {"ok": True}

    socket_path = tmp_path / "private" / "mailboxes.sock"
    listener = MailboxSocket(socket_path, router)
    await listener.start()
    try:
        assert socket_path.stat().st_mode & 0o777 == 0o600
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)), base_url="http://localhost"
        ) as client:
            assert (await client.get("/mailboxes/peers")).status_code == 401
            assert (
                await client.get("/mailboxes/peers", headers={"Authorization": "Bearer synthetic"})
            ).status_code == 200
            assert (await client.get("/workspaces")).status_code == 404
    finally:
        await listener.close()
    assert not socket_path.exists()


@pytest.mark.asyncio
async def test_existing_path_is_never_deleted(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "mailboxes.sock"
    path.write_text("not owned")
    listener = MailboxSocket(path, APIRouter())
    with pytest.raises(RuntimeError, match="already exists"):
        await listener.start()
    assert path.read_text() == "not owned"
