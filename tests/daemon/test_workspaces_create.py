"""POST /workspaces dispatches to the right Manager via repo_root."""

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
    """Daemon wired against a real git repo + the in-memory FakeTmux."""
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def test_create_requires_repo_root(daemon: TestClient) -> None:
    resp = daemon.post("/workspaces", json={"agent_name": "claude", "title": "t"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "repo_root_required"


def test_create_dispatches_to_repo_manager(daemon: TestClient, tmp_repo: Path) -> None:
    payload = {
        "agent_name": "claude",
        "title": "Add login flow",
        "repo_root": str(tmp_repo),
        "branch_plan": {"kind": "auto"},
    }
    resp = daemon.post("/workspaces", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Add login flow"
    assert body["repo_root"] == str(tmp_repo)
    assert body["agent_name"] == "claude"
