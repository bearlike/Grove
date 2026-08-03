"""``GET /workspaces/{id}/todo``: the latest-todo projection, bounded to one
workspace and resolved through the same manager seam the issueops publisher
calls in-process. Same fetch-on-demand shape as the sibling session-history
routes — never on the SSE stream.
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
            "title": "todo test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return str(body["id"])


def test_get_todo_returns_an_empty_list_with_no_transcript_yet(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create_ws(daemon, tmp_repo)

    resp = daemon.get(f"/workspaces/{ws_id}/todo")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_get_todo_sessionless_workspace_is_404(daemon: TestClient, tmp_repo: Path) -> None:
    """The ``shell`` builtin agent is a generic kind — it mints no session id
    at all, so there is nothing for the projection to read."""
    ws_id = _create_ws(daemon, tmp_repo, agent_name="shell")

    resp = daemon.get(f"/workspaces/{ws_id}/todo")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


def test_get_todo_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.get("/workspaces/nope/todo")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
