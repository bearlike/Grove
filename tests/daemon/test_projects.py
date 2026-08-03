"""GET /projects — the repo-discovery listing a client calls before it holds a path."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _daemon_for(cfg: GroveConfig) -> Iterator[TestClient]:
    app = build_app(cfg=cfg, store=JsonWorkspaceStore())
    with TestClient(app) as client:
        yield client


def test_projects_takes_no_query_params(daemon: TestClient) -> None:
    """The one listing that is NOT repo-scoped: it is what you call to LEARN a
    repo root, so requiring one would be circular."""
    resp = daemon.get("/projects")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_projects_lists_a_config_declared_repo(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """A declared repo with zero workspaces still lists. This is the empty-project
    visibility rule reaching the wire: a freshly added repo has no store row,
    and a client that only saw store-derived roots could never offer it."""
    cfg = daemon_test_config()
    cfg.projects = [str(tmp_repo)]
    for client in _daemon_for(cfg):
        resp = client.get("/projects")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["repo_root"] for r in rows] == [str(tmp_repo)]
        assert rows[0]["repo_name"] == tmp_repo.name
        # A top-level repo anchors its own cwd.
        assert rows[0]["cwd"] == str(tmp_repo)


def test_projects_row_shape_is_exactly_the_view(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    cfg = daemon_test_config()
    cfg.projects = [str(tmp_repo)]
    for client in _daemon_for(cfg):
        for row in client.get("/projects").json():
            assert set(row) == {"repo_root", "repo_name", "cwd"}


def test_projects_lists_a_nested_project_under_its_enclosing_repo(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """A declared SUBDIRECTORY lists as its own project while still anchoring at
    the real repo root, which is what lets one worktree family host several
    projects that differ only in where the agent starts."""
    nested = tmp_repo / "frontend"
    nested.mkdir()
    (nested / "app.js").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "nested", "--no-verify"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )

    cfg = daemon_test_config()
    cfg.projects = [str(nested)]
    for client in _daemon_for(cfg):
        rows = client.get("/projects").json()
        assert len(rows) == 1
        assert rows[0]["cwd"] == str(nested)
        assert rows[0]["repo_root"] == str(tmp_repo)


def test_projects_drops_a_declared_path_that_is_not_a_repo(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Best-effort by contract: a stale or non-git entry is skipped rather than
    failing the whole listing, so one bad config line cannot blind a client to
    every other project."""
    junk = tmp_path / "not-a-repo"
    junk.mkdir()
    cfg = daemon_test_config()
    cfg.projects = [str(junk), str(tmp_path / "does-not-exist")]
    for client in _daemon_for(cfg):
        resp = client.get("/projects")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []


def test_projects_is_sorted_stably(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The engine unions two set-derived scans, so order is not inherently
    stable. Sorting server-side lets a client diff two calls."""
    repos = []
    for name in ("zulu", "alpha", "mike"):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@x.y"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "README.md").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init", "--no-verify"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        repos.append(repo)

    cfg = daemon_test_config()
    cfg.projects = [str(r) for r in repos]
    for client in _daemon_for(cfg):
        names = [r["repo_name"] for r in client.get("/projects").json()]
        assert names == ["alpha", "mike", "zulu"]


def test_projects_requires_auth(tmp_state_dir: Path, fake_tmux: FakeTmux) -> None:
    """Project paths are host-private, so this listing sits behind the bearer
    like every other route except /healthz."""
    # AuthConfig is frozen, so build the enabled config rather than mutating one.
    cfg = GroveConfig.model_validate({"auth": {"enabled": True}})
    for client in _daemon_for(cfg):
        assert client.get("/projects").status_code == 401
