"""RepoRegistry lazy-instantiates one WorkspaceManager per repo_root."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from grove.core.config import GroveConfig, load_config
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


def _write_project_config(repo_root: Path, payload: dict[str, object]) -> None:
    grove_dir = repo_root / ".grove"
    grove_dir.mkdir(parents=True, exist_ok=True)
    (grove_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_registry_resolves_project_scoped_agent_and_init_script(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A `config_loader` makes each Manager see its OWN repo cascade, so a
    project-only agent and a project-enabled init_script are honored — the
    daemon's global config (no `repo_root`) never saw `<repo>/.grove/config.json`.
    Regression for #46 (create rejected a project agent) and #47 (init skipped).
    """
    _write_project_config(
        tmp_repo,
        {
            "agents": [{"name": "Project Only", "command": "claude", "kind": "claude_code"}],
            "init_script": {"enabled": True, "inline": "true"},
        },
    )
    # The daemon's global config — loaded with no repo_root — does NOT define
    # the project agent and leaves init_script disabled (the default).
    global_cfg = load_config(repo_root=None)
    assert global_cfg.find_agent("Project Only") is None

    registry = RepoRegistry(cfg=global_cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    mgr = registry.get(tmp_repo)

    assert mgr.config.find_agent("Project Only") is not None  # #46
    assert mgr.config.init_script.enabled is True  # #47


def test_registry_without_loader_uses_shared_cfg(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """No `config_loader` → every Manager shares the injected cfg (the legacy
    behavior tests rely on). This is exactly the path that made the daemon blind
    to project config, so it must stay an explicit opt-in, not the default."""
    _write_project_config(
        tmp_repo, {"agents": [{"name": "Project Only", "command": "claude", "kind": "claude_code"}]}
    )
    registry = RepoRegistry(cfg=load_config(repo_root=None), store=JsonWorkspaceStore())
    mgr = registry.get(tmp_repo)
    assert mgr.config.find_agent("Project Only") is None
