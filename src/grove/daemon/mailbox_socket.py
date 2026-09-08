"""A private Unix listener exposes only the existing authenticated mailbox router."""

from __future__ import annotations

import asyncio
import os
import socket
import stat
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI


class _MailboxServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # The enclosing daemon owns process signals and shutdown ordering.
        yield


class MailboxSocket:
    """Never widen the daemon listener or unlink an endpoint another owner created."""

    def __init__(self, path: Path, router: APIRouter) -> None:
        self.path = path
        self._router = router
        self._socket: socket.socket | None = None
        self._server: _MailboxServer | None = None
        self._task: asyncio.Task[None] | None = None
        self._identity: tuple[int, int] | None = None

    async def start(self) -> None:
        if not hasattr(socket, "AF_UNIX") or not hasattr(os, "getuid"):
            raise RuntimeError("private mailbox sockets require a Unix host")
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("mailbox socket requires an owned private directory")
        if self.path.exists() or self.path.is_symlink():
            raise RuntimeError("mailbox socket path already exists; refusing to replace it")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            self.path.chmod(0o600)
            info = self.path.stat()
            self._identity = (info.st_dev, info.st_ino)
            listener.listen(64)
            listener.setblocking(False)
            app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
            app.include_router(self._router)
            server = _MailboxServer(uvicorn.Config(app, log_level="warning", lifespan="off"))
            self._socket = listener
            self._server = server
            self._task = asyncio.create_task(server.serve(sockets=[listener]))
            for _ in range(100):
                if server.started:
                    return
                if self._task.done():
                    await self._task
                    raise RuntimeError("mailbox listener failed to start")
                await asyncio.sleep(0.01)
            raise TimeoutError("mailbox listener startup timed out")
        except BaseException:
            listener.close()
            await self.close()
            raise

    async def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 3)
            except TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
        if self._socket is not None:
            self._socket.close()
        with suppress(FileNotFoundError):
            info = self.path.lstat()
            if self._identity == (info.st_dev, info.st_ino):
                self.path.unlink()
