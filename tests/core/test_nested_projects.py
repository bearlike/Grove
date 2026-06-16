"""Nested-subdirectory projects (issue #101).

A configured project may be a *subdirectory* of a git repo, not a repo root of
its own. Two concerns are separated here:

- **Listing** — ``RepoRegistry.known_projects()`` surfaces each nested subdir as
  a distinct project (its own ``cwd``) anchored to the enclosing repo's root,
  while ``known_roots()`` stays the repo seam and collapses them to that one
  enclosing root.
- **Placement vs cwd** — a workspace created with ``project_cwd`` set to a nested
  path starts its agent session in that subdir, while the git worktree and
  branch are still anchored at the true repo root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.daemon.repos import RepoRegistry
from tests.conftest import FakeTmux


def _add_subdir(repo: Path, rel: str) -> Path:
    """Create + commit ``rel`` inside ``repo`` so it exists in every worktree."""
    sub = repo / rel
    sub.mkdir(parents=True, exist_ok=True)
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"add {rel}", "--no-verify"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return sub.resolve()


# ─── registry listing ────────────────────────────────────────────────────────


def test_known_projects_surfaces_nested_subdir_as_distinct(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Two subdirs of one repo each list as a distinct project, both anchored at
    the enclosing repo root — not collapsed to the single repo."""
    homelab = _add_subdir(tmp_repo, "homelab")
    pa = _add_subdir(tmp_repo, "PA")
    cfg = GroveConfig(projects=[str(tmp_repo), str(homelab), str(pa)])
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_state_dir / "s.json"))

    cwds = {p.cwd for p in registry.known_projects()}
    assert cwds == {tmp_repo.resolve(), homelab, pa}
    assert all(p.repo_root == tmp_repo.resolve() for p in registry.known_projects())


def test_known_roots_collapses_nested_declared_to_enclosing_repo(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """``known_roots()`` stays the repo seam: a nested-only declaration resolves
    to the enclosing repo root (so Manager dispatch + repo validation work),
    appearing exactly once."""
    homelab = _add_subdir(tmp_repo, "homelab")
    cfg = GroveConfig(projects=[str(homelab)])
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_state_dir / "s.json"))

    assert registry.known_roots() == [tmp_repo.resolve()]
    # The nested subdir still lists as its own project.
    assert {p.cwd for p in registry.known_projects()} == {homelab}


# ─── workspace cwd vs worktree placement ─────────────────────────────────────


def _manager(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def test_create_with_project_cwd_anchors_worktree_at_repo_root(
    tmp_state_dir: Path, tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The agent session starts in the nested subdir of the worktree, while the
    worktree dir and branch are created at the repo-root level."""
    _add_subdir(tmp_repo, "services/api")
    mgr = _manager(tmp_repo, tmp_path)

    state = mgr.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="t", project_cwd=tmp_repo / "services" / "api"
        )
    )

    # Subpath persisted as a POSIX relative path from the worktree root.
    assert state.project_subpath == "services/api"
    # Worktree placed under the configured trees dir (repo-root level), NOT
    # nested under the subdir.
    worktree = Path(state.worktree_path)
    assert worktree.parent == (tmp_path / "trees")
    # The agent session's cwd is the subdir *inside* the worktree.
    assert fake_tmux.session_cwds[state.tmux_session] == worktree / "services" / "api"
    assert fake_tmux.layout_worktrees[state.tmux_session] == worktree / "services" / "api"


def test_create_without_project_cwd_defaults_to_worktree_root(
    tmp_state_dir: Path, tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """No ``project_cwd`` → historical behavior: empty subpath, cwd == worktree."""
    mgr = _manager(tmp_repo, tmp_path)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="t"))

    assert state.project_subpath == ""
    assert fake_tmux.session_cwds[state.tmux_session] == Path(state.worktree_path)


def test_create_rejects_project_cwd_outside_repo(
    tmp_state_dir: Path, tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """A ``project_cwd`` outside the repo root is a loud error before any side effect."""
    mgr = _manager(tmp_repo, tmp_path)

    with pytest.raises(GroveError, match="not within repo"):
        mgr.create(
            CreateWorkspaceRequest(
                agent_name="claude", title="t", project_cwd=tmp_path / "elsewhere"
            )
        )


def test_resume_restores_nested_cwd(
    tmp_state_dir: Path, tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """Resume recreates the session in the persisted nested subdir, not the
    worktree root."""
    _add_subdir(tmp_repo, "services/api")
    mgr = _manager(tmp_repo, tmp_path)
    state = mgr.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="t", project_cwd=tmp_repo / "services" / "api"
        )
    )
    mgr.pause(state.id)
    fake_tmux.session_cwds.clear()

    mgr.resume(state.id)

    worktree = Path(state.worktree_path)
    assert fake_tmux.session_cwds[state.tmux_session] == worktree / "services" / "api"
