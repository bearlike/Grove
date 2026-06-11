"""Root-placement workspaces + the per-create skip-init flag.

A root workspace runs in the repo root itself: no dedicated git worktree, no
Grove-created branch, lifecycle limited to create / kill / respawn. These tests
pin the safety-critical invariant first — Grove never removes the repo root and
never deletes the user's live branch, not even on rollback — then the skip-init
override, the respawn init gating, status reconciliation, and the store
round-trip. Real git repo + FakeTmux, the same seams as
`test_workspace_lifecycle.py`.

Why the spies on `worktree_remove` / `branch_delete`: git itself refuses to
remove the main worktree or delete a checked-out branch, so a real-git
assertion alone would pass even if the gate were missing (git's own protection
would mask the bug). Asserting the calls are *never issued* pins the gate, not
git's safety net.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceStateError
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import (
    BranchProvenance,
    InitStatus,
    Placement,
    WorkspaceStatus,
)
from tests.conftest import FakeTmux


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _worktree_count(repo: Path) -> int:
    """Number of worktrees git knows about — 1 means just the main checkout."""
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return sum(1 for line in out.stdout.splitlines() if line.startswith("worktree "))


def _cfg(tmp_path: Path, **overrides: Any) -> GroveConfig:
    base: dict[str, Any] = {
        "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
        "tmux": {"session_prefix": "test-"},
    }
    base.update(overrides)
    return GroveConfig.model_validate(base)


def _spy(monkeypatch: pytest.MonkeyPatch, git: GitRepo, name: str) -> list[tuple[Any, ...]]:
    """Replace a GitRepo method with a recorder, returning the call log."""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(git, name, lambda *a, **k: calls.append((a, k)))
    return calls


def _root_request(title: str = "root task", *, skip_init: bool = False) -> CreateWorkspaceRequest:
    return CreateWorkspaceRequest(
        agent_name="claude",
        title=title,
        branch_plan=RootBranch(),
        skip_init=skip_init,
    )


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=_cfg(tmp_path), store=store)


def _init_manager(tmp_repo: Path, tmp_path: Path, **init: Any) -> WorkspaceManager:
    cfg = _cfg(tmp_path, init_script={"enabled": True, "inline": "true", **init})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


# ─── create: adopts the live checkout, makes nothing ────────────────────────


def test_create_root_adopts_repo_root_and_current_branch(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    state = manager.create(_root_request())

    assert state.placement is Placement.ROOT
    assert state.status == WorkspaceStatus.RUNNING
    assert Path(state.worktree_path).resolve() == tmp_repo.resolve()
    assert state.branch == "main"  # whatever HEAD already points to
    # USER_ATTACHED: the live branch is the user's, never Grove's to delete.
    assert state.branch_provenance == BranchProvenance.USER_ATTACHED
    assert state.tmux_session in fake_tmux.sessions
    assert (state.tmux_session, "claude") in fake_tmux.layouts
    assert any(e.kind == "created" and e.workspace_id == state.id for e in events)


def test_create_root_creates_no_worktree_and_no_branch(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    del fake_tmux
    branches_before = _branches(tmp_repo)
    manager.create(_root_request())
    # Only the main worktree exists; no branch was created.
    assert _worktree_count(tmp_repo) == 1
    assert _branches(tmp_repo) == branches_before


def test_create_root_never_calls_worktree_add(
    manager: WorkspaceManager, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    added = _spy(monkeypatch, manager._git, "worktree_add")
    manager.create(_root_request())
    assert added == []


# ─── kill: never touches the repo dir or the live branch ────────────────────


def test_kill_root_never_removes_repo_or_deletes_branch(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = manager.create(_root_request())
    removed = _spy(monkeypatch, manager._git, "worktree_remove")
    deleted = _spy(monkeypatch, manager._git, "branch_delete")
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    manager.kill(state.id)

    assert removed == []  # the repo root is never Grove's to remove
    assert deleted == []  # the live branch is never Grove's to delete
    assert tmp_repo.exists()
    assert (tmp_repo / "README.md").exists()
    assert "main" in _branches(tmp_repo)
    assert state.tmux_session not in fake_tmux.sessions
    assert all(s.id != state.id for s in manager.store.load_all())
    killed = next(e for e in events if e.kind == "killed")
    assert killed.detail["branch_deleted"] == "false"


def test_kill_root_ignores_explicit_delete_branch_true(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard override: even ``delete_branch=True`` cannot delete a root
    workspace's live branch."""
    del fake_tmux
    state = manager.create(_root_request())
    deleted = _spy(monkeypatch, manager._git, "branch_delete")
    manager.kill(state.id, delete_branch=True)
    assert deleted == []
    assert "main" in _branches(tmp_repo)


# ─── pause / resume are refused for root ────────────────────────────────────


def test_pause_refused_for_root(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    del fake_tmux
    state = manager.create(_root_request())
    with pytest.raises(WorkspaceStateError, match="root workspaces"):
        manager.pause(state.id)


def test_resume_refused_for_root(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    del fake_tmux
    state = manager.create(_root_request())
    with pytest.raises(WorkspaceStateError, match="root workspaces"):
        manager.resume(state.id)


# ─── respawn works; reconciliation never orphans the repo root ──────────────


def test_root_reconciles_active_never_orphaned(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    del fake_tmux  # default activity = 0s → within threshold → ACTIVE
    state = manager.create(_root_request())
    listed = next(s for s in manager.list() if s.id == state.id)
    # repo root always exists, so a root workspace can never be ORPHANED.
    assert listed.status == WorkspaceStatus.ACTIVE


def test_respawn_root_restarts_session(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    state = manager.create(_root_request())
    fake_tmux.sessions.discard(state.tmux_session)
    assert next(s for s in manager.list() if s.id == state.id).status == WorkspaceStatus.OFFLINE

    respawned = manager.respawn(state.id)

    assert respawned.status == WorkspaceStatus.RUNNING
    assert state.tmux_session in fake_tmux.sessions


def test_respawn_root_never_reruns_init(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Root never re-runs init on respawn even with run_on_resume — init in the
    real repo root is a deliberate create-time choice, never unattended."""
    mgr = _init_manager(tmp_repo, tmp_path, run_on_resume=True)
    state = mgr.create(_root_request(skip_init=True))
    fake_tmux.init_calls.clear()
    fake_tmux.sessions.discard(state.tmux_session)

    mgr.respawn(state.id)

    assert fake_tmux.init_calls == []


def test_respawn_worktree_reruns_init_when_configured(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Contrast with root: a worktree workspace DOES re-run init on respawn."""
    mgr = _init_manager(tmp_repo, tmp_path, run_on_resume=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="wt"))
    fake_tmux.init_calls.clear()
    fake_tmux.sessions.discard(state.tmux_session)

    mgr.respawn(state.id)

    assert len(fake_tmux.init_calls) == 1


# ─── skip-init override ─────────────────────────────────────────────────────


def test_skip_init_records_skipped_even_when_enabled(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _init_manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="s", skip_init=True))
    assert state.init_status == InitStatus.SKIPPED
    assert fake_tmux.init_calls == []  # the script never ran


def test_init_runs_when_enabled_and_not_skipped(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _init_manager(tmp_repo, tmp_path)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="s"))
    assert state.init_status == InitStatus.OK
    assert len(fake_tmux.init_calls) == 1


def test_failed_root_init_rollback_preserves_repo_and_branch(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root create whose init fails with fail_fast must roll back WITHOUT
    removing the repo root or deleting the live branch."""
    mgr = _init_manager(tmp_repo, tmp_path, inline="false", fail_fast=True)
    fake_tmux.init_exit_code = 1
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    with pytest.raises(GroveError, match="fail_fast"):
        mgr.create(_root_request("boom"))

    assert removed == []
    assert deleted == []
    assert tmp_repo.exists()
    assert (tmp_repo / "README.md").exists()
    assert "main" in _branches(tmp_repo)
    assert mgr.store.load_all() == []  # the partial record is cleaned up


# ─── persistence: placement round-trips; legacy records default to worktree ─


def test_placement_round_trips_through_store(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    state = manager.create(_root_request())
    reloaded = JsonWorkspaceStore(path=tmp_path / "state.json").get(state.id)
    assert reloaded.placement is Placement.ROOT


def test_legacy_record_without_placement_loads_as_worktree(
    manager: WorkspaceManager, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A state.json written before `placement` existed loads as WORKTREE — the
    only shape Grove used to support, no migration needed (the branch_provenance
    precedent)."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="legacy"))
    store_path = tmp_path / "state.json"
    data = json.loads(store_path.read_text(encoding="utf-8"))
    for record in data["workspaces"].values():
        record.pop("placement", None)  # pretend the field never existed
    store_path.write_text(json.dumps(data), encoding="utf-8")

    reloaded = JsonWorkspaceStore(path=store_path).get(state.id)
    assert reloaded.placement is Placement.WORKTREE
