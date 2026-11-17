"""POST /projects and GET /projects/candidates — the wire half of the onboarding wizard."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    root.mkdir()
    return root


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    fake_tmux: FakeTmux,
    projects_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    for key, value in {
        "GIT_AUTHOR_NAME": "Grove Test",
        "GIT_AUTHOR_EMAIL": "test@grove.local",
        "GIT_COMMITTER_NAME": "Grove Test",
        "GIT_COMMITTER_EMAIL": "test@grove.local",
    }.items():
        monkeypatch.setenv(key, value)
    cfg = daemon_test_config()
    cfg.projects_root = str(projects_root)
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as client:
        yield client


def test_create_returns_the_registered_project(daemon: TestClient, projects_root: Path) -> None:
    resp = daemon.post("/projects", json={"kind": "create", "name": "weather-bot"})
    assert resp.status_code == 201, resp.text
    made = str((projects_root / "weather-bot").resolve())
    assert resp.json() == {"repo_root": made, "repo_name": "weather-bot", "cwd": made}


def test_an_existing_target_is_a_named_422(daemon: TestClient, projects_root: Path) -> None:
    (projects_root / "taken").mkdir()
    resp = daemon.post("/projects", json={"kind": "create", "name": "taken"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "project_target_refused"


def test_a_failed_clone_is_a_named_422_with_gits_message(
    daemon: TestClient, tmp_path: Path
) -> None:
    resp = daemon.post(
        "/projects", json={"kind": "clone", "url": str(tmp_path / "missing.git"), "name": "x"}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "project_git_failed"
    assert "missing.git" in resp.json()["detail"]["message"]


def test_a_malformed_body_is_rejected_by_the_schema(daemon: TestClient) -> None:
    resp = daemon.post("/projects", json={"kind": "create", "name": "../escape"})
    assert resp.status_code == 422


def test_candidates_exclude_a_project_just_added(daemon: TestClient, projects_root: Path) -> None:
    """The scan reads the registry per call, so a project registered through
    the config watch leaves the list with no restart."""
    for name in ("alpha", "beta"):
        subprocess.run(["git", "init", "-q", str(projects_root / name)], check=True)
    before = daemon.get("/projects/candidates").json()
    assert before["root"] == str(projects_root.resolve())
    assert [c["name"] for c in before["candidates"]] == ["alpha", "beta"]

    added = daemon.post("/projects", json={"kind": "adopt", "path": str(projects_root / "alpha")})
    assert added.status_code == 201, added.text
    # Registration reaches the registry through the user-config watch, the one
    # path every surface shares, so the exclusion is observed on that edge.
    deadline = time.monotonic() + 10
    names: list[str] = []
    while time.monotonic() < deadline:
        names = [c["name"] for c in daemon.get("/projects/candidates").json()["candidates"]]
        if "alpha" not in names:
            break
        time.sleep(0.2)
    assert names == ["beta"]


def test_a_repo_gated_route_knows_the_project_as_soon_as_post_answers(
    daemon: TestClient, projects_root: Path
) -> None:
    """The client selects a project the moment POST answers, so the very next
    repo-gated read must not race the config watch into `unknown_repo_root`."""
    resp = daemon.post("/projects", json={"kind": "create", "name": "fresh"})
    assert resp.status_code == 201, resp.text
    agents = daemon.get("/agents", params={"repo": resp.json()["repo_root"]})
    assert agents.status_code == 200, agents.text
