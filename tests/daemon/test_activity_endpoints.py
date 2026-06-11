"""GET /activity snapshot + GET /events SSE stream, and the _SseHub fan-out.

Endpoint shape/framing/auth/OpenAPI go through TestClient; the bounded-queue
drop-oldest, the Last-Event-ID replay window, and the sync->async delta bridge
are unit-tested on the hub directly (the deterministic seam for those).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.config import GroveConfig
from grove.core.contracts.activity import DashboardEvent
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from grove.daemon._sse import _SseHub
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
        status=WorkspaceStatus.PAUSED,  # PAUSED → reconcile is trivial, no tmux
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client(tmp_state_dir: Path) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    store.save(_state("b1", str(tmp_state_dir / "repo-b")))
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as test_client:
        yield test_client


def _service(tmp_path: Path) -> ActivityService:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    cfg = GroveConfig.model_validate({"auth": {"enabled": False}})
    return ActivityService(registry=RepoRegistry(cfg=cfg, store=store))


def _event(seq: int, kind: str = "workspace_changed") -> DashboardEvent:
    return DashboardEvent(kind=kind, seq=seq, workspace_id="w")  # type: ignore[arg-type]


# ─── GET /activity ──────────────────────────────────────────────────────────


def test_activity_returns_snapshot_shape(client: TestClient) -> None:
    resp = client.get("/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_workspaces"] == 2
    assert "needs_attention" in body
    assert "generated_at" in body
    assert {p["repo_name"] for p in body["projects"]} == {"repo-a", "repo-b"}


# ─── GET /events framing ────────────────────────────────────────────────────


async def _first_sse_frame(app: FastAPI, path: str) -> tuple[dict[str, Any], str]:
    """Drive the ASGI app directly, capture the first SSE body chunk, disconnect.

    An infinite SSE stream deadlocks the sync TestClient and buffers under httpx's
    ASGI transport, so we speak ASGI by hand: feed the request, let the response
    start + first ``http.response.body`` arrive, then return ``http.disconnect`` so
    Starlette cancels the generator and ``app(...)`` returns. Deterministic, with a
    timeout backstop.
    """
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
        # Starlette's disconnect watcher polls here; once the first frame is out we
        # report the client as gone so the stream tears down promptly.
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


def test_pane_endpoint_shape(client: TestClient) -> None:
    resp = client.get("/workspaces/a1/pane")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_id"] == "a1"
    assert body["ansi"] is None  # PAUSED fixture → no live pane (best-effort, no raise)
    assert "taken_at" in body


def test_pane_endpoint_unknown_workspace_404(client: TestClient) -> None:
    assert client.get("/workspaces/nope/pane").status_code == 404


async def test_events_emits_snapshot_first(tmp_state_dir: Path) -> None:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    store.save(_state("b1", str(tmp_state_dir / "repo-b")))
    app = build_app(cfg=daemon_test_config(), store=store)

    start, frame_text = await _first_sse_frame(app, "/events")

    assert start["status"] == 200
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["x-accel-buffering"] == "no"

    frame = frame_text.splitlines()
    event_line = next(item for item in frame if item.startswith("event:"))
    data_line = next(item for item in frame if item.startswith("data:"))
    assert event_line == "event: snapshot"
    payload = json.loads(data_line[len("data:") :].strip())
    assert payload["kind"] == "snapshot"
    assert payload["snapshot"]["total_workspaces"] == 2


def test_events_and_activity_require_auth(tmp_state_dir: Path) -> None:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    app = build_app(cfg=GroveConfig(), store=store)  # auth enabled (default)
    with TestClient(app) as authed:
        assert authed.get("/activity").status_code == 401
        assert authed.get("/events").status_code == 401


def test_openapi_documents_activity_routes_and_views(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/activity" in spec["paths"]
    assert "/events" in spec["paths"]
    schemas = spec["components"]["schemas"]
    # Both must appear so the webapp codegen picks the wire types up.
    assert "DashboardSnapshotView" in schemas
    assert "DashboardEvent" in schemas


# ─── _SseHub fan-out / replay (deterministic, no event loop needed) ─────────


def test_hub_fanout_drops_oldest_when_queue_full(tmp_path: Path) -> None:
    hub = _SseHub(_service(tmp_path), queue_size=2)
    queue = hub.register()
    for seq in (1, 2, 3, 4):
        hub._publish(_event(seq))  # full at 2 → oldest evicted
    delivered = [queue.get_nowait().seq for _ in range(queue.qsize())]
    assert delivered == [3, 4]


def test_hub_replay_window(tmp_path: Path) -> None:
    hub = _SseHub(_service(tmp_path), ring_size=3)
    for seq in (1, 2, 3, 4, 5):
        hub._publish(_event(seq))  # ring keeps 3, 4, 5
    # A small gap is replayable; a gap wider than the buffer is not (→ full snapshot).
    assert hub.can_replay(4) is True
    assert [e.seq for e in hub.replay_since(4)] == [5]
    assert hub.can_replay(1) is False


async def test_hub_bridges_poll_deltas_to_queue(tmp_path: Path) -> None:
    """poll_once emits on the caller's thread; the hub marshals onto the loop."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("p1", str(tmp_path / "repo-p")))
    cfg = GroveConfig.model_validate({"auth": {"enabled": False}})
    service = ActivityService(registry=RepoRegistry(cfg=cfg, store=store))

    hub = _SseHub(service)
    hub.start(asyncio.get_running_loop())
    queue = hub.register()

    service.poll_once()  # first poll: fingerprint changed from empty → emits a delta
    await asyncio.sleep(0)  # let the scheduled _publish run on the loop

    event = queue.get_nowait()
    assert event.kind in ("session_activity", "workspace_changed")
    assert event.seq >= 1
    hub.stop()


def test_event_from_delta_carries_workspace_none_for_lifecycle() -> None:
    delta = DashboardDelta(
        kind="workspace_changed", seq=3, workspace_id="x", detail={"event": "killed"}
    )
    event = DashboardEvent.from_delta(delta)
    assert event.kind == "workspace_changed"
    assert event.workspace is None
    assert event.detail["event"] == "killed"
