"""The whole live-step path over real HTTP: viewer → daemon → worker → pulse.

A real daemon app served by uvicorn on a loopback port, a real `NativeWorker`
connected to it, and a real SSE reader on `/workspaces/{id}/live`. Only the
provider child is fake, and it emits the frame shape a live Claude session
printed. Two assertions carry the feature: a viewer sees the step, and with no
viewer the worker posts nothing at all.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from grove.core.agents.native_owner import NativeSubmission
from grove.core.native_worker import NativeWorker, NativeWorkerConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

from .test_mailboxes import _WORKSPACE, _config, _state, _write_project_config


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Owner:
    """A provider that thinks for as long as it lives, like a busy model."""

    def __init__(self) -> None:
        self.worker: NativeWorker | None = None
        self._stop = asyncio.Event()

    async def start(self, prompt: str) -> str:
        asyncio.get_running_loop().create_task(self._think())
        return "session-1"

    async def _think(self) -> None:
        tokens = 0
        while not self._stop.is_set():
            tokens += 100
            assert self.worker is not None
            self.worker._observe(
                "recv",
                {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": tokens},
            )
            await asyncio.sleep(0.05)

    async def send(self, message_id: str, text: str) -> NativeSubmission:
        return NativeSubmission(stage="delivered", evidence=message_id)

    async def wait_closed(self) -> None:
        await self._stop.wait()

    async def close(self) -> None:
        self._stop.set()


@pytest.mark.asyncio
async def test_a_viewer_turns_the_watch_on_and_sees_the_live_step(
    tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    store.save(_state(_WORKSPACE, repo))
    app = build_app(cfg=_config(auth=False), store=store)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    posts: list[str] = []
    real_post = httpx.AsyncClient.post

    async def counting_post(self: httpx.AsyncClient, url: str, **kwargs: object) -> httpx.Response:
        if str(url).endswith("/mailboxes/pulse"):
            posts.append(str(url))
        return await real_post(self, url, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "post", counting_post)
    monkeypatch.setattr("grove.core.native_worker._PULSE_INTERVAL_SECONDS", 0.05)

    owner = _Owner()
    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id=_WORKSPACE,
            provider="claude_code",
            command=["unused"],
            resume=True,
            daemon_url=f"http://127.0.0.1:{port}",
        )
    )
    owner.worker = worker
    monkeypatch.setattr(worker, "transport", lambda: owner)
    running = asyncio.create_task(worker.run())
    try:
        # Registered, thinking, and nobody watching: not one pulse is posted.
        # Assert the registration first, or a refused worker passes this vacuously.
        for _ in range(100):
            if app.state.native_owners.owner_for(_WORKSPACE) is not None:
                break
            await asyncio.sleep(0.02)
        assert app.state.native_owners.owner_for(_WORKSPACE) is not None
        await asyncio.sleep(0.6)
        assert posts == []

        pulse = None
        async with (
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as viewer,
            viewer.stream("GET", f"/workspaces/{_WORKSPACE}/live") as response,
        ):
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data:") and '"step"' in line:
                    pulse = json.loads(line[5:])
                    break
        assert pulse is not None
        assert pulse["step"] == "thinking"
        assert pulse["thinking_tokens"] > 0

        # The viewer left: the watch goes off and the posts stop.
        await asyncio.sleep(0.3)
        settled = len(posts)
        await asyncio.sleep(0.5)
        assert len(posts) == settled
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        server.should_exit = True
        await serving
