"""PATCH /workspaces/{id} — partial metadata update (title and/or description)."""

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
    del tmp_state_dir, fake_tmux
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def created_ws(daemon: TestClient, tmp_repo: Path) -> str:
    body = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "initial",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()
    return body["id"]


def test_patch_renames_title(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"title": "renamed"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["description"] is None


def test_patch_sets_description(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"description": "ticket #1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "initial"
    assert body["description"] == "ticket #1"


def test_patch_clears_description_with_empty_string(daemon: TestClient, created_ws: str) -> None:
    daemon.patch(f"/workspaces/{created_ws}", json={"description": "first"})
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"description": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] is None


def test_patch_both_fields(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.patch(
        f"/workspaces/{created_ws}",
        json={"title": "renamed", "description": "why"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["description"] == "why"


def test_patch_unknown_id_returns_404(daemon: TestClient) -> None:
    resp = daemon.patch("/workspaces/does-not-exist", json={"title": "x"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_patch_empty_body_returns_422(daemon: TestClient, created_ws: str) -> None:
    """At least one of title/description must be provided."""
    resp = daemon.patch(f"/workspaces/{created_ws}", json={})
    assert resp.status_code == 422


def test_patch_rejects_extra_fields(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.patch(
        f"/workspaces/{created_ws}",
        json={"title": "renamed", "agent_name": "claude"},
    )
    assert resp.status_code == 422


def test_patch_rejects_empty_title(daemon: TestClient, created_ws: str) -> None:
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"title": ""})
    assert resp.status_code == 422


def test_patch_does_not_change_worktree_or_session(daemon: TestClient, created_ws: str) -> None:
    before = daemon.get(f"/workspaces/{created_ws}").json()
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"title": "renamed"})
    after = resp.json()
    assert after["worktree_path"] == before["worktree_path"]
    assert after["tmux_session"] == before["tmux_session"]
    assert after["branch"] == before["branch"]


def test_patch_returns_updated_view_with_description_field(
    daemon: TestClient, created_ws: str
) -> None:
    resp = daemon.patch(f"/workspaces/{created_ws}", json={"title": "renamed"})
    assert resp.status_code == 200
    assert "description" in resp.json()


def test_create_with_description_persists_through_view(daemon: TestClient, tmp_repo: Path) -> None:
    payload = {
        "agent_name": "claude",
        "title": "with-desc",
        "description": "see ticket",
        "repo_root": str(tmp_repo),
        "branch_plan": {"kind": "auto"},
    }
    resp = daemon.post("/workspaces", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "see ticket"
