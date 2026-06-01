"""RepoRegistry lazy-instantiates one WorkspaceManager per repo_root."""

from __future__ import annotations

import subprocess
from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.daemon.repos import RepoRegistry


def test_registry_creates_manager_on_first_access(tmp_state_dir: Path, tmp_repo: Path) -> None:
    cfg = GroveConfig()
    store = JsonWorkspaceStore()
    registry = RepoRegistry(cfg=cfg, store=store)

    mgr = registry.get(tmp_repo)
    assert isinstance(mgr, WorkspaceManager)
    assert mgr.repo_root == tmp_repo


def test_registry_caches_manager_per_repo(tmp_state_dir: Path, tmp_repo: Path) -> None:
    cfg = GroveConfig()
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore())

    a = registry.get(tmp_repo)
    b = registry.get(tmp_repo)
    assert a is b


def test_registry_distinct_managers_for_distinct_repos(tmp_state_dir: Path, tmp_path: Path) -> None:
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for repo in (repo_a, repo_b):
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

    cfg = GroveConfig()
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore())

    mgr_a = registry.get(repo_a.resolve())
    mgr_b = registry.get(repo_b.resolve())
    assert mgr_a is not mgr_b
    assert mgr_a.repo_root != mgr_b.repo_root
