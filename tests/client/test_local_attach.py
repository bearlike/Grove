"""LocalAttach: PTY round-trip with a known echo command."""

from __future__ import annotations

import asyncio
import sys

import pytest

from grove.client.attach import LocalAttach

# LocalAttach uses pty.fork + fcntl.ioctl(TIOCSWINSZ) — POSIX-only by
# nature. Windows users attach via WSL (where the same tests run as
# Linux) or via a remote SSH backend (SshAttach works on every OS).
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="LocalAttach requires POSIX (pty / fcntl / termios)",
)


@pytest.mark.asyncio
async def test_local_attach_echoes_input() -> None:
    # Use 'cat' instead of tmux for a deterministic byte-echo test.
    attach = LocalAttach.with_command(["cat"])
    received: list[bytes] = []
    attach.on_output(received.append)

    await attach.start()
    try:
        await attach.feed_input(b"hello\n")
        for _ in range(100):
            if b"hello" in b"".join(received):
                break
            await asyncio.sleep(0.02)
    finally:
        await attach.close()

    assert b"hello" in b"".join(received)


@pytest.mark.asyncio
async def test_local_attach_resize_does_not_error() -> None:
    attach = LocalAttach.with_command(["cat"])
    attach.on_output(lambda _: None)
    await attach.start()
    try:
        await attach.resize(cols=120, rows=40)
    finally:
        await attach.close()
