"""`GET /watches?workspace=` — the read a workspace page makes for its own watches.

Driven through the real app, lifespan included, so the scheduler that answers is
the one the daemon builds. The registry file is redirected by the suite's
autouse path fixture, so nothing here reaches the developer's live watches.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from grove.core import paths
from grove.core.contracts.watches import HOST_ADDRESS
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config


def _workspace_rows(listing: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    """The rows a test registered; the daemon's own host-wide standing watches excluded."""
    host = HOST_ADDRESS.workspace_id
    return [row for row in listing["watches"] if row["recipient"]["workspace_id"] != host]  # type: ignore[index]


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
    assert {row["note"] for row in _workspace_rows(unscoped.json())} == {"mine", "theirs"}


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
    assert _workspace_rows(listed.json()) == []


def test_a_malformed_workspace_filter_is_refused_not_read_as_empty(tmp_path):
    """A typo'd id must not answer "this workspace has no watches"."""
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    with TestClient(app) as client:
        response = client.get("/watches", params={"workspace": "not-an-id"})

    assert response.status_code == 422


def _real_v1_log(path, rows: int = 40) -> None:
    """A version-1 log shaped like the real one: indented, one big document."""
    now = datetime.now(UTC)
    watches = {}
    for n in range(rows):
        watch_id = f"wch_{n:032x}"
        watches[watch_id] = {
            "id": watch_id,
            "recipient": {"workspace_id": MINE, "agent": ""},
            "predicate": {"kind": "timer", "at": (now + timedelta(hours=1)).isoformat()},
            "state": "pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
            "every": "PT30S",
            "note": "",
        }
    path.write_text(json.dumps({"version": 1, "watches": watches}, indent=2), encoding="utf-8")


def test_a_corrupt_watch_log_no_longer_stops_the_daemon_from_starting(tmp_path):
    """The 2026-10-15 crash loop, replayed through the real lifespan.

    One bit flipped a space to `$` in indentation whitespace, the single-document
    log stopped parsing, and `StandingWatches.reconcile_all` raised out of the
    lifespan: exit 3, 97 restarts. The daemon must come up and serve.
    """
    log_path = paths.user_watches_path()
    _real_v1_log(log_path)
    raw = log_path.read_bytes()
    flipped = raw.index(b"\n  ", len(raw) // 2) + 1
    log_path.write_bytes(raw[:flipped] + b"$" + raw[flipped + 1 :])

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/watches", json=_timer(MINE, "after")).status_code == 201

    aside = [p for p in log_path.parent.iterdir() if ".unreadable-" in p.name]
    assert len(aside) == 1 and b"$" in aside[0].read_bytes()


def test_a_healthy_v1_log_migrates_at_startup_and_keeps_every_watch(tmp_path):
    log_path = paths.user_watches_path()
    _real_v1_log(log_path, rows=12)

    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore(tmp_path / "state.json"))
    with TestClient(app) as client:
        listed = client.get("/watches", params={"workspace": MINE}).json()["watches"]

    assert len(listed) == 12
    assert log_path.read_text(encoding="utf-8").startswith('{"format":"grove-records"')
