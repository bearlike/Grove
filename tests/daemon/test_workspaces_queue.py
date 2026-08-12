"""``GET /workspaces/{id}/queue``: what the harness is holding, fetched on
demand.

The ``/todo`` route's sibling, and it inherits that route's two documented
refusals — 404 for a workspace with no session at all, a real 200 for a session
with nothing queued — plus a third the queue needs and todo does not:
``supported``, which tells "nothing waiting" from "no idea". A harness whose
queue Grove cannot observe must not report an empty one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _create_ws(daemon: TestClient, tmp_repo: Path, *, agent_name: str = "claude") -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": agent_name,
            "title": "queue test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return str(body["id"])


def test_an_observable_harness_with_nothing_queued_is_a_real_200(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """Empty AND supported — the client renders "nothing waiting", which is a
    different sentence from the one below."""
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.get(f"/workspaces/{ws_id}/queue")

    assert resp.status_code == 200
    assert resp.json() == {"messages": [], "supported": True}


def test_a_sessionless_workspace_is_404(daemon: TestClient, tmp_repo: Path) -> None:
    """The ``shell`` builtin is a generic kind: it mints no session id, so there
    is no session for the projection to read. Same refusal as ``/todo`` — and
    the resolution behind it is ``_todo_session_id``, NOT ``agent_session_id``,
    so a codex workspace (which mints nothing for its whole life) is not
    excluded by construction the way keying on the mint would exclude it."""
    ws_id = _create_ws(daemon, tmp_repo, agent_name="shell")

    resp = daemon.get(f"/workspaces/{ws_id}/queue")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


def test_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/queue")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
