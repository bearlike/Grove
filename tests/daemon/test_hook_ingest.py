"""Native Claude Code http-hook ingest route.

`POST /hooks/agent-events` is the push half of the sidecar: the daemon's own
`ClaudeHook.settings()` renders an http handler pointing here, carrying
the same-host ingest token as its bearer. The auth gate is independent of the
`SessionStore` pairing bearer used by other routes. A valid hook is admitted to
the maintained projection for its named session; it never initiates a fleet
poll.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from grove.core.activity import ActivityService
from grove.core.activity_runtime import ActivityRuntime
from grove.core.admission import Admission
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


# ─── targeted admission ───────────────────────────────────────────────────────


def test_ingest_admits_its_named_session_without_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hook is a targeted projection hint, never a full-fleet refresh."""
    admitted: list[str] = []

    def _hook(self: ActivityRuntime, session_id: str) -> Admission:
        del self
        admitted.append(session_id)
        return Admission.ACCEPTED

    def _unexpected_poll(self: ActivityService) -> None:
        del self
        pytest.fail("hook ingest must not start a fleet poll")

    monkeypatch.setattr(ActivityRuntime, "hook", _hook)
    monkeypatch.setattr(ActivityService, "poll_once", _unexpected_poll)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={"session_id": "session-42"})

    assert resp.status_code == 204
    assert admitted == ["session-42"]


@pytest.mark.parametrize("admission", [Admission.FULL, Admission.NOT_READY, Admission.CLOSED])
def test_ingest_refuses_unadmitted_hook(
    monkeypatch: pytest.MonkeyPatch, admission: Admission
) -> None:
    """A 204 means the targeted session hint entered the projection runtime."""
    calls: list[str] = []

    def _hook(self: ActivityRuntime, session_id: str) -> Admission:
        del self
        calls.append(session_id)
        return admission

    monkeypatch.setattr(ActivityRuntime, "hook", _hook)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        resp = client.post("/hooks/agent-events", json={"session_id": "session-42"})

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "activity_intake_unavailable"
    assert calls == ["session-42"]
