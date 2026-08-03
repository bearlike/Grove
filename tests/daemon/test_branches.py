"""GET /branches?repo=<path>&scope=local|remote."""

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
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path) -> Iterator[TestClient]:
    # `tmp_repo` is DECLARED as a project, because every repo-dispatched route
    # answers only for a root the user registered — a repo with no workspace
    # yet becomes known exactly this way (`grove config add-project`), and
    # without it these reads 404 like `/workspaces` and `/sessions` already do.
    store = JsonWorkspaceStore()
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    app = build_app(cfg=cfg, store=store)
    with TestClient(app) as client:
        yield client


def test_branches_local(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=local")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    names = {item["name"] for item in body}
    assert "main" in names


def test_branches_remote_empty(daemon: TestClient, tmp_repo: Path) -> None:
    # tmp_repo from conftest has no remote configured
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=remote")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_branches_invalid_scope(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/branches?repo={tmp_repo}&scope=bogus")
    assert resp.status_code == 422


def test_branches_rejects_an_unregistered_repo(daemon: TestClient, tmp_path: Path) -> None:
    """`repo` selects CONFIGURATION, so it must name a registered root.

    Two silent failures close with the 404: an existing non-repo directory used
    to answer `200 []` — a typo'd path masquerading as "no branches" — and a
    nonexistent one reached `subprocess(cwd=…)` and surfaced as a bare 500,
    since `OSError` is not a `GroveError` and no handler sees it.
    """
    not_a_repo = tmp_path / "elsewhere"
    not_a_repo.mkdir()
    for candidate in (not_a_repo, tmp_path / "nope"):
        resp = daemon.get(f"/branches?repo={candidate}&scope=local")
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["error"] == "unknown_repo_root"
