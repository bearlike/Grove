"""Lossless framing for native providers whose JSON records have no size limit."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


class NativeStream:
    """Treat the asyncio buffer limit as flow control, never a protocol frame cap."""

    @staticmethod
    async def lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
        pending = bytearray()
        while chunk := await stream.read(65536):
            parts = chunk.split(b"\n")
            for part in parts[:-1]:
                pending.extend(part)
                yield bytes(pending)
                pending.clear()
            pending.extend(parts[-1])
        if pending:
            yield bytes(pending)
