"""`GET /watches?workspace=` — the read a workspace page makes for its own watches.

Driven through the real app, lifespan included, so the scheduler that answers is
the one the daemon builds. The registry file is redirected by the suite's
autouse path fixture, so nothing here reaches the developer's live watches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

MINE = "a" * 32
THEIRS = "b" * 32


def _timer(recipient: str, note: str) -> dict[str, object]:
    at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    return {
        "recipient": {"workspace_id": recipient},
        "predicate": {"kind": "timer", "at": at},
        "note": note,
    }


def test_the_workspace_filter_returns_only_that_workspaces_watches(tmp_path):
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    with TestClient(app) as client:
        assert client.post("/watches", json=_timer(MINE, "mine")).status_code == 201
        assert client.post("/watches", json=_timer(THEIRS, "theirs")).status_code == 201

        scoped = client.get("/watches", params={"workspace": MINE})
        unscoped = client.get("/watches")

    assert scoped.status_code == 200
    assert [row["note"] for row in scoped.json()["watches"]] == ["mine"]
    assert {row["note"] for row in unscoped.json()["watches"]} == {"mine", "theirs"}


def test_a_ci_watch_on_a_forge_the_daemon_cannot_read_is_refused(tmp_path):
    """The incident end to end: the agent is told at registration, not in an hour.

    The test config enables no ticket provider, so no forge's checks are readable.
    """
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    ci = {
        "recipient": {"workspace_id": MINE},
        "predicate": {
            "kind": "ci",
            "provider": "github",
            "owner": "acme",
            "repo": "widgets",
            "head_sha": "1a5058d841f060ecbcd70ee92c42698f14aed329",
        },
    }
    with TestClient(app) as client:
        refused = client.post("/watches", json=ci)
        listed = client.get("/watches")

    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "watch_unobservable"
    assert "tickets.github" in refused.json()["detail"]["message"]
    assert listed.json()["watches"] == []


def test_a_malformed_workspace_filter_is_refused_not_read_as_empty(tmp_path):
    """A typo'd id must not answer "this workspace has no watches"."""
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    with TestClient(app) as client:
        response = client.get("/watches", params={"workspace": "not-an-id"})

    assert response.status_code == 422
