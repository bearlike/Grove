"""RepoRegistry lazy-instantiates one WorkspaceManager per repo_root."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import GroveConfig, load_config
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
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

    assert mgr.config.find_agent("Project Only") is not None
    assert mgr.config.init_script.enabled is True


def test_registry_without_loader_uses_shared_cfg(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """No `config_loader` → every Manager shares the injected cfg. This is
    exactly the path that made the daemon blind to project config, so it must
    stay an explicit opt-in, not the default."""
    _write_project_config(
        tmp_repo, {"agents": [{"name": "Project Only", "command": "claude", "kind": "claude_code"}]}
    )
    registry = RepoRegistry(cfg=load_config(repo_root=None), store=JsonWorkspaceStore())
    mgr = registry.get(tmp_repo)
    assert mgr.config.find_agent("Project Only") is None


# ─── known_roots() union: config-declared empty projects stay visible ───────


def _init_git_repo(path: Path) -> Path:
    """Init a bare-minimum git repo at `path`; return its resolved root."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    return path.resolve()


def _persist_workspace(store: JsonWorkspaceStore, repo_root: Path) -> None:
    """Save one minimal WorkspaceState so `repo_root` is store-derived."""
    now = datetime.now(UTC)
    store.save(
        WorkspaceState(
            id=f"ws-{repo_root.name}",
            title="t",
            repo_root=str(repo_root),
            branch="b",
            base_branch="main",
            worktree_path=f"{repo_root}/.worktrees/ws",
            tmux_session="grove-ws",
            agent_name="claude",
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )


def test_known_roots_includes_declared_only_repo(tmp_state_dir: Path, tmp_path: Path) -> None:
    """A config-declared repo with ZERO workspaces still appears."""
    empty_repo = _init_git_repo(tmp_path / "empty")
    cfg = GroveConfig(projects=[str(empty_repo)])
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json"))

    assert empty_repo in registry.known_roots()


def test_known_roots_expands_tilde_in_declared_path(
    tmp_state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~` in a declared path expands at consume time (user-config ergonomics)."""
    home_repo = _init_git_repo(tmp_path / "home" / "proj")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = GroveConfig(projects=["~/proj"])
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json"))

    assert home_repo in registry.known_roots()


def test_known_roots_dedupes_declared_against_store(tmp_state_dir: Path, tmp_path: Path) -> None:
    """A repo that is BOTH store-derived and config-declared appears once."""
    repo = _init_git_repo(tmp_path / "shared")
    store = JsonWorkspaceStore(tmp_path / "state.json")
    _persist_workspace(store, repo)
    cfg = GroveConfig(projects=[str(repo)])
    registry = RepoRegistry(cfg=cfg, store=store)

    roots = registry.known_roots()
    assert roots.count(repo) == 1


def test_known_roots_collapses_symlinked_declared_path(tmp_state_dir: Path, tmp_path: Path) -> None:
    """A declared symlink collapses to its target via `.resolve()` — no dupe."""
    repo = _init_git_repo(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(repo)
    store = JsonWorkspaceStore(tmp_path / "state.json")
    _persist_workspace(store, repo)
    cfg = GroveConfig(projects=[str(link)])
    registry = RepoRegistry(cfg=cfg, store=store)

    roots = registry.known_roots()
    assert roots.count(repo) == 1


def test_known_roots_drops_nonexistent_and_nongit_declared_paths(
    tmp_state_dir: Path, tmp_path: Path
) -> None:
    """A declared path that doesn't exist or isn't a git repo is silently dropped."""
    missing = tmp_path / "ghost"
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    cfg = GroveConfig(projects=[str(missing), str(plain_dir)])
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json"))

    assert registry.known_roots() == []


def test_known_roots_unchanged_when_projects_empty(tmp_state_dir: Path, tmp_path: Path) -> None:
    """Existing behavior holds when `projects` is unset: store-derived only."""
    repo = _init_git_repo(tmp_path / "repo")
    store = JsonWorkspaceStore(tmp_path / "state.json")
    _persist_workspace(store, repo)
    registry = RepoRegistry(cfg=GroveConfig(), store=store)

    assert registry.known_roots() == [repo]
