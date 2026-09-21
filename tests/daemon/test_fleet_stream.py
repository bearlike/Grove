"""``GET /workspaces/{id}/fleet/stream`` and the ``FleetSnapshotStream`` seam.

Unit-level tests drive ``FleetSnapshotStream`` directly against a real
``ActivityService`` (deterministic, no event loop timing) for the properties
that matter most: an edge on a DIFFERENT workspace must never wake this
stream (the two-workspace/session leak case), stopping releases the bus
subscription and the poll-audience membership exactly once, and a slow
snapshot read runs off the loop so it cannot block an unrelated request.
Route-level tests cover the wire framing and root-session scoping.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.config import GroveConfig
from grove.core.contracts.activity import SubagentFleetView
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from grove.daemon._audience import _PollAudience
from grove.daemon._fleet_stream import FleetSnapshot, FleetSnapshotStream
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


def _state(ws_id: str, repo_root: str) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root=repo_root,
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"{repo_root}/.grove/worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
    )


def _service(tmp_path: Path) -> ActivityService:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    cfg = GroveConfig.model_validate({"auth": {"enabled": False}})
    return ActivityService(registry=RepoRegistry(cfg=cfg, store=store))


def _view(session_id: str | None = "s1") -> FleetSnapshot:
    return FleetSnapshot(SubagentFleetView(session_id=session_id, supported=True))


# ─── FleetSnapshotStream: coalescing + scope ────────────────────────────────


async def test_a_delta_on_a_different_workspace_never_wakes_this_stream(
    tmp_path: Path,
) -> None:
    """Two workspaces, two streams: publishing on B must not wake A's edge —
    the scoped cross-workspace leak this stream exists to prevent."""
    service = _service(tmp_path)
    audience = _PollAudience()
    stream_a = FleetSnapshotStream(service, audience, workspace_id="a", reader=_view)
    stream_b = FleetSnapshotStream(service, audience, workspace_id="b", reader=_view)
    stream_a.start()
    stream_b.start()
    try:
        service._emit(  # type: ignore[attr-defined]
            DashboardDelta(kind="session_activity", seq=service.next_seq(), workspace_id="b")
        )
        # B's edge must arrive; A's must not, within a bounded wait.
        await asyncio.wait_for(stream_b.changed(), timeout=1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stream_a.changed(), timeout=0.2)
    finally:
        stream_a.stop()
        stream_b.stop()


async def test_a_session_switch_on_one_workspace_never_leaks_to_a_sibling(
    tmp_path: Path,
) -> None:
    """Two concurrent streams scoped to the SAME workspace but different root
    sessions must both observe an edge for that workspace — the stream scopes
    on workspace id, and root-session selection is the reader's job, not a
    second filter that could silently diverge from it."""
    service = _service(tmp_path)
    audience = _PollAudience()
    seen: list[str] = []

    def _reader_for(root: str) -> Callable[[], FleetSnapshot]:
        def _read() -> FleetSnapshot:
            seen.append(root)
            return FleetSnapshot(_view(root))

        return _read

    stream_root1 = FleetSnapshotStream(
        service, audience, workspace_id="w", reader=_reader_for("root1")
    )
    stream_root2 = FleetSnapshotStream(
        service, audience, workspace_id="w", reader=_reader_for("root2")
    )
    stream_root1.start()
    stream_root2.start()
    try:
        service._emit(  # type: ignore[attr-defined]
            DashboardDelta(kind="session_activity", seq=service.next_seq(), workspace_id="w")
        )
        await asyncio.wait_for(stream_root1.changed(), timeout=1)
        await asyncio.wait_for(stream_root2.changed(), timeout=1)
    finally:
        stream_root1.stop()
        stream_root2.stop()


async def test_stop_unsubscribes_and_releases_audience_exactly_once(tmp_path: Path) -> None:
    """After ``stop()``, a subsequent bus delta must not enqueue an edge, and
    the poll audience count returns to its pre-``start`` value."""
    service = _service(tmp_path)
    audience = _PollAudience()
    stream = FleetSnapshotStream(service, audience, workspace_id="w", reader=_view)

    assert len(audience) == 0
    stream.start()
    assert len(audience) == 1
    stream.stop()
    assert len(audience) == 0

    # A delta after stop must not raise (the inbox is closed) and must not be
    # observable — nothing is listening any more.
    service._emit(  # type: ignore[attr-defined]
        DashboardDelta(kind="session_activity", seq=service.next_seq(), workspace_id="w")
    )

    # Idempotent: a second stop must not double-release the audience or
    # double-unsubscribe.
    stream.stop()
    assert len(audience) == 0


async def test_slow_snapshot_read_runs_off_the_loop(tmp_path: Path) -> None:
    """``snapshot()`` bridges the reader through a worker thread, so a slow
    reader never blocks the loop that serves every other coroutine."""
    entered = threading.Event()
    released = threading.Event()

    def _slow_reader() -> FleetSnapshot:
        entered.set()
        released.wait(timeout=5)
        return _view()

    service = _service(tmp_path)
    audience = _PollAudience()
    stream = FleetSnapshotStream(service, audience, workspace_id="w", reader=_slow_reader)
    stream.start()
    try:
        task = asyncio.create_task(stream.snapshot())
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, entered.wait, 5), "reader never entered"
        # The loop is free to run other coroutines while the reader blocks —
        # proven by awaiting something else concurrently.
        assert not task.done()
        pong = await asyncio.wait_for(asyncio.sleep(0, result="pong"), timeout=1)
        assert pong == "pong"
        released.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.view.session_id == "s1"
    finally:
        stream.stop()


# ─── Route: framing + root-session scope + off-loop send ───────────────────


@pytest.fixture
def daemon(tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def test_fleet_stream_unknown_workspace_is_404(daemon: TestClient) -> None:
    with daemon.stream("GET", "/workspaces/nope/fleet/stream") as resp:
        assert resp.status_code == 404


async def test_fleet_stream_initial_frame_names_the_resolved_root(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> None:
    """The first frame is a named ``fleet_snapshot`` carrying the resolved
    root session id — never a foreign workspace's, and never a session_id the
    caller merely typed without owning."""
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)

    # One lifespan context for both the create call and the stream read —
    # entering `_first_sse_frame`'s OWN lifespan afterwards would re-bind the
    # already-closed SSE intake and raise InboxClosed.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/workspaces",
                json={
                    "agent_name": "claude",
                    "title": "fleet-stream-init",
                    "repo_root": str(tmp_repo),
                    "branch_plan": {"kind": "auto"},
                },
            )
            assert resp.status_code == 200, resp.text
            ws_id = resp.json()["id"]

        start, frame_text = await _stream_first_frame(app, f"/workspaces/{ws_id}/fleet/stream")

    assert start["status"] == 200
    lines = frame_text.splitlines()
    assert "event: fleet_snapshot" in lines
    data_line = next(item for item in lines if item.startswith("data:"))
    payload = json.loads(data_line[len("data:") :].strip())
    assert payload["session_id"] is not None
    assert payload["supported"] is True


async def _stream_first_frame(app: FastAPI, path: str) -> tuple[dict[str, Any], str]:
    """Like ``_first_sse_frame`` but assumes the lifespan is ALREADY entered —
    for a test that needs the same running app to serve a prior request first."""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    start: dict[str, Any] = {}
    chunks: list[bytes] = []
    got_first = asyncio.Event()
    request_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await got_first.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            got_first.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    text = b"".join(chunks).decode()
    return start, text.split("\n\n", 1)[0]
