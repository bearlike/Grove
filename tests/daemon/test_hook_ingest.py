"""Native Claude Code http-hook ingest route.

`POST /hooks/agent-events` is the push half of the sidecar: the daemon's own
`ClaudeHook.settings()` renders an http handler pointing here, carrying
the same-host ingest token as its bearer. Two concerns, two test groups: the
auth gate (a DIFFERENT mechanism from the `SessionStore` pairing bearer every
other route uses) and the immediate-refresh effect (the route awaits
`ActivityService.poll_once()` before returning, so it can never race the
lifespan's own ~2s poll tick).
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from grove.core.activity import ActivityService
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from grove.daemon._audience import _PollAudience
from tests.daemon.conftest import daemon_test_config

# ─── auth gate ───────────────────────────────────────────────────────────────


def test_ingest_requires_a_hook_token_when_auth_is_on() -> None:
    app = build_app(cfg=GroveConfig(), store=JsonWorkspaceStore())  # auth.enabled defaults True
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={"session_id": "s"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_invalid"


def test_ingest_accepts_the_shared_hook_token() -> None:
    app = build_app(cfg=GroveConfig(), store=JsonWorkspaceStore())
    token = ClaudeHook.ensure_ingest_token()
    with TestClient(app) as client:
        resp = client.post(
            "/hooks/agent-events",
            json={"session_id": "s"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204


def test_ingest_rejects_a_wrong_token() -> None:
    app = build_app(cfg=GroveConfig(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post(
            "/hooks/agent-events",
            json={"session_id": "s"},
            headers={"Authorization": "Bearer definitely-not-it"},
        )
    assert resp.status_code == 401


def test_ingest_rejects_a_missing_session_id() -> None:
    """The wire model still validates the one field it cares about (422)."""
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={})
    assert resp.status_code == 422


def test_ingest_permissively_accepts_unknown_hook_fields() -> None:
    """``extra="allow"``: a real Claude Code payload carries cwd/tool_name/etc,
    none of which this route needs to justify a refresh."""
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post(
            "/hooks/agent-events",
            json={
                "session_id": "s",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": "/tmp/work",
            },
        )
    assert resp.status_code == 204


# ─── immediate refresh ───────────────────────────────────────────────────────


def test_ingest_awaits_poll_once_before_responding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: the route's `poll_once()` call is AWAITED, so it has
    already run by the time the 204 comes back.

    The lifespan's own background poll loop is disabled here (a no-op
    replacement for `_poll_loop`) so the call count is attributable ONLY to
    this request — otherwise a coincidental startup tick from the real ~2s
    loop could land in the same window and make the assertion pass for the
    wrong reason.
    """
    calls: list[None] = []
    original = ActivityService.poll_once

    def _spy(self: ActivityService) -> None:
        calls.append(None)
        original(self)

    monkeypatch.setattr(ActivityService, "poll_once", _spy)

    async def _noop_poll_loop(
        service: ActivityService,
        interval: float,
        stop_event: asyncio.Event,
        audience: _PollAudience,
    ) -> None:
        del service, interval, audience
        await stop_event.wait()

    monkeypatch.setattr("grove.daemon.app._poll_loop", _noop_poll_loop)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={"session_id": "s"})
    assert resp.status_code == 204
    assert calls == [None]  # exactly one refresh, caused by this request alone


# ─── single-flight coalescing ──────────────────────────────────────────────────


async def test_concurrent_hook_events_share_one_inflight_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hook POST arriving while a poll triggered by an EARLIER hook POST is
    still in flight must await that SAME run, not schedule its own full-fleet
    `poll_once()`. Claude Code dispatches hook events with no debounce, so an
    active agent's own tool-call cadence routinely fires several POSTs close
    together — with no guard, each independently schedules a full-fleet scan
    onto the executor thread pool, all running
    at once. Deterministic throughout: no wall-clock sleeps, every hand-off
    is an explicit event/await.
    """
    poll_started = threading.Event()
    release_poll = threading.Event()
    call_count = 0
    lock = threading.Lock()

    def _slow_poll_once(self: ActivityService) -> None:
        nonlocal call_count
        del self
        with lock:
            call_count += 1
        poll_started.set()
        assert release_poll.wait(timeout=5), "test bug: never released"

    monkeypatch.setattr(ActivityService, "poll_once", _slow_poll_once)

    async def _noop_poll_loop(
        service: ActivityService,
        interval: float,
        stop_event: asyncio.Event,
        audience: _PollAudience,
    ) -> None:
        del service, interval, audience
        await stop_event.wait()

    monkeypatch.setattr("grove.daemon.app._poll_loop", _noop_poll_loop)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    loop = asyncio.get_running_loop()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.post("/hooks/agent-events", json={"session_id": "a"}))
        # Bridged through the executor so awaiting it ALSO yields the loop,
        # letting `first` actually progress to its own executor dispatch —
        # a bare `poll_started.wait()` here would block the loop thread and
        # deadlock instead.
        assert await loop.run_in_executor(None, poll_started.wait, 5.0)

        second = asyncio.create_task(client.post("/hooks/agent-events", json={"session_id": "b"}))
        # One event-loop turn suffices for `second` to reach the coalescer's
        # join point: FastAPI dispatch runs synchronously down to the route
        # body, and joining an in-flight run's only await is the shielded
        # wait on that run's future — no thread hand-off on that path.
        await asyncio.sleep(0)

        release_poll.set()
        resp1, resp2 = await asyncio.gather(first, second)

    assert resp1.status_code == 204
    assert resp2.status_code == 204
    assert call_count == 1
