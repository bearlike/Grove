"""GET /workspaces/{id}/attach and /peek."""

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
def daemon(tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def ws_id(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "attach-peek-test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


def test_attach_returns_instruction(daemon: TestClient, ws_id: str) -> None:
    resp = daemon.get(f"/workspaces/{ws_id}/attach")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tmux_session" in body
    assert "inside_outer_tmux" in body


def test_peek_returns_view(daemon: TestClient, ws_id: str) -> None:
    resp = daemon.get(f"/workspaces/{ws_id}/peek")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "state",
        "base_ahead",
        "base_behind",
        "diff_added",
        "diff_removed",
        "dirty_files",
        "recent_commits",
    ):
        assert key in body, f"missing {key} in WorkspacePeekView"
    assert body["state"]["id"] == ws_id
