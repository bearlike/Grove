"""POST /workspaces dispatches to the right Manager via repo_root."""

from __future__ import annotations

import subprocess
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
    # Worktree placement is the default and surfaces on the wire.
    assert body["placement"] == "worktree"


def test_create_root_workspace_over_http(daemon: TestClient, tmp_repo: Path) -> None:
    """The root variant + skip_init flow through the wire unchanged: the daemon
    passes the request to the manager and surfaces `placement` on the view."""
    payload = {
        "agent_name": "claude",
        "title": "root over http",
        "repo_root": str(tmp_repo),
        "branch_plan": {"kind": "root"},
        "skip_init": True,
    }
    resp = daemon.post("/workspaces", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["placement"] == "root"
    assert Path(body["worktree_path"]).resolve() == tmp_repo.resolve()
    assert body["branch"] == "main"  # adopts the live checkout


def test_create_by_project_identity_resolves_this_nodes_own_path(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """A hub names a project by remote; each node resolves its own path for it."""
    subprocess.run(
        ["git", "remote", "add", "origin", "https://git.example.com/org/repo.git"],
        cwd=tmp_repo,
        check=True,
    )
    cfg = daemon_test_config().model_copy(update={"projects": [str(tmp_repo)]})
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as client:
        named = {"remote": "git.example.com/org/repo", "default_branch": "main"}
        payload = {"agent_name": "claude", "title": "by identity", "project": named}
        created = client.post("/workspaces", json=payload)
        assert created.status_code == 200, created.text
        assert created.json()["repo_root"] == str(tmp_repo)
        missing = client.post(
            "/workspaces",
            json={**payload, "project": {**named, "remote": "git.example.com/org/other"}},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["error"] == "project_not_on_node"
