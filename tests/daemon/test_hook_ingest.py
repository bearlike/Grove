"""Native Claude Code http-hook ingest route (#171).

`POST /hooks/agent-events` is the push half of the #18 sidecar: the daemon's
own `ClaudeHook.settings()` renders an http handler pointing here, carrying
the same-host ingest token as its bearer. Two concerns, two test groups: the
auth gate (a DIFFERENT mechanism from the `SessionStore` pairing bearer every
other route uses) and the immediate-refresh effect (the route awaits
`ActivityService.poll_once()` before returning, so it can never race the
lifespan's own ~2s poll tick).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from grove.core.activity import ActivityService
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
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
    """The whole point (#171): the route's `poll_once()` call is AWAITED, so it
    has already run by the time the 204 comes back.

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
        service: ActivityService, interval: float, stop_event: asyncio.Event
    ) -> None:
        del service, interval
        await stop_event.wait()

    monkeypatch.setattr("grove.daemon.app._poll_loop", _noop_poll_loop)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={"session_id": "s"})
    assert resp.status_code == 204
    assert calls == [None]  # exactly one refresh, caused by this request alone
