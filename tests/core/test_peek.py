"""WorkspaceManager.peek — rich snapshot for the rail.

These tests pin the *contract* of peek (what fields and shapes the TUI
depends on) by exercising it through real git + a FakeTmux. Failures in
peek's helpers must degrade to zeros / empty rather than raise — peek is
a render helper and a missing pane must never crash the TUI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import InitStatus, WorkspaceStatus
from tests.conftest import FakeTmux


@pytest.fixture
def manager(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-", "agent_window_name": "agent"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout


# ─── happy path ──────────────────────────────────────────────────────────────


def test_peek_running_returns_full_snapshot(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="peek-running"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "claude>\nhello world"

    peek = manager.peek(state.id)

    assert peek.state.id == state.id
    # Reconciliation promotes RUNNING → ACTIVE (default fake activity = 0s,
    # under the 5s threshold).
    assert peek.state.status == WorkspaceStatus.ACTIVE
    assert peek.agent_snapshot == "claude>\nhello world"
    assert peek.snapshot_taken_at is not None
    assert peek.recent_commits  # at least the initial commit
    initial = peek.recent_commits[-1]
    assert initial.subject == "init"
    assert len(initial.sha) >= 7


def test_peek_returns_zeros_for_clean_new_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="clean"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = ""  # empty pane

    peek = manager.peek(state.id)

    assert peek.base_ahead == 0
    assert peek.base_behind == 0
    assert peek.diff_added == 0
    assert peek.diff_removed == 0
    assert peek.dirty_files == 0
    assert peek.agent_snapshot is None  # empty string → None on the contract


def test_peek_counts_dirty_files(manager: WorkspaceManager, tmp_repo: Path) -> None:
    del tmp_repo
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="dirty"))
    worktree = Path(state.worktree_path)
    (worktree / "new1.txt").write_text("x", encoding="utf-8")
    (worktree / "new2.txt").write_text("y", encoding="utf-8")

    peek = manager.peek(state.id)

    assert peek.dirty_files == 2


def test_peek_counts_diff_lines_against_base(manager: WorkspaceManager, tmp_repo: Path) -> None:
    del tmp_repo
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="diff"))
    worktree = Path(state.worktree_path)
    (worktree / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "add f", "--no-verify")

    peek = manager.peek(state.id)

    assert peek.base_ahead == 1
    assert peek.base_behind == 0
    assert peek.diff_added == 3
    assert peek.diff_removed == 0


def test_peek_recent_commits_newest_first(manager: WorkspaceManager, tmp_repo: Path) -> None:
    del tmp_repo
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="commits"))
    worktree = Path(state.worktree_path)
    for i in range(3):
        (worktree / f"x{i}").write_text(str(i), encoding="utf-8")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", f"step {i}", "--no-verify")

    peek = manager.peek(state.id)

    subjects = [c.subject for c in peek.recent_commits]
    # limit=3 → three newest. step 2, step 1, step 0. (Initial commit pushed off.)
    assert subjects == ["step 2", "step 1", "step 0"]


# ─── peek_pane (fast path used by the rail's pane-tick) ─────────────────────


def test_peek_pane_returns_snapshot_for_running(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fast-tick"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "hello\nworld"

    snap, captured_at = manager.peek_pane(state.id)

    assert snap == "hello\nworld"
    assert captured_at is not None


def test_peek_pane_returns_none_when_capture_empty(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="empty"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = ""  # blank pane

    snap, captured_at = manager.peek_pane(state.id)

    assert snap is None
    assert captured_at is None


def test_peek_pane_returns_none_for_paused(manager: WorkspaceManager, fake_tmux: FakeTmux) -> None:
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="zzz"))
    manager.pause(state.id)

    snap, captured_at = manager.peek_pane(state.id)

    assert snap is None
    assert captured_at is None


def test_peek_pane_returns_none_when_session_gone(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="ghost"))
    fake_tmux.sessions.discard(state.tmux_session)

    snap, captured_at = manager.peek_pane(state.id)

    # Reconciliation: peek_pane reconciles the loaded state; with the
    # session gone the status flips to OFFLINE and pane_target returns None,
    # so the fast path declines to capture.
    assert snap is None
    assert captured_at is None


# ─── pane_target fallback (sessions reorganized outside Grove) ──────────────


def test_pane_target_returns_session_agent_by_default(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The happy path: a Grove-built session has [shell, agent] and the
    target is the agent window verbatim."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="happy"))

    assert manager.pane_target(state.id) == f"{state.tmux_session}:agent"


def test_pane_target_falls_back_to_first_non_shell_when_agent_missing(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Workspaces created or reorganized externally may have any window
    layout. When `agent` is absent we prefer the first non-`shell`
    window — that's where the user's actual work tends to live (an
    `init` script, a `dev` window, a renamed agent)."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="reorg"))
    fake_tmux.windows[state.tmux_session] = ["shell", "init", "dev"]

    assert manager.pane_target(state.id) == f"{state.tmux_session}:init"


def test_pane_target_falls_back_to_shell_when_only_choice(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Last-resort fallback: only a `shell` window exists. We still
    target *something* so the rail can show the user their bare prompt
    rather than a misleading 'no output'."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="bare"))
    fake_tmux.windows[state.tmux_session] = ["shell"]

    assert manager.pane_target(state.id) == f"{state.tmux_session}:shell"


def test_pane_target_returns_none_when_session_has_no_windows(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="empty"))
    fake_tmux.windows[state.tmux_session] = []

    assert manager.pane_target(state.id) is None


def test_pane_target_returns_none_for_paused(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="zzz"))
    manager.pause(state.id)

    assert manager.pane_target(state.id) is None


def test_peek_pane_falls_back_to_non_shell_when_agent_missing(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """End-to-end: peek_pane uses the resolved fallback target when the
    `agent` window doesn't exist, returning the snapshot from there."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fallback"))
    fake_tmux.windows[state.tmux_session] = ["shell", "dev"]
    fake_tmux.snapshots[f"{state.tmux_session}:dev"] = "live work in progress"

    snap, captured_at = manager.peek_pane(state.id)

    assert snap == "live work in progress"
    assert captured_at is not None


def test_peek_pane_falls_back_to_shell_as_last_resort(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="bare"))
    fake_tmux.windows[state.tmux_session] = ["shell"]
    fake_tmux.snapshots[f"{state.tmux_session}:shell"] = "$ "

    snap, _ = manager.peek_pane(state.id)

    assert snap == "$ "


# ─── reconciliation + degraded states ────────────────────────────────────────


def test_peek_reconciles_running_to_offline_when_session_is_gone(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Persisted RUNNING + tmux session vanished + worktree intact → OFFLINE."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="ghost"))
    fake_tmux.sessions.discard(state.tmux_session)

    peek = manager.peek(state.id)

    assert peek.state.status == WorkspaceStatus.OFFLINE
    assert peek.agent_snapshot is None  # no live pane to capture
    assert peek.snapshot_taken_at is None


def test_peek_reconciles_running_to_idle_when_pane_quiet(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Pane activity older than threshold → IDLE, but snapshot still captured."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="quiet"))
    fake_tmux.activity_seconds_ago[f"{state.tmux_session}:agent"] = 90
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "claude>"

    peek = manager.peek(state.id)

    assert peek.state.status == WorkspaceStatus.IDLE
    # IDLE is still LIVE_STATUSES; the rail keeps showing the pane.
    assert peek.agent_snapshot == "claude>"


def test_peek_reconciles_running_to_orphaned_when_worktree_missing(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """Worktree dir deleted externally → ORPHANED (cleanup needed)."""
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="orph"))
    shutil.rmtree(state.worktree_path, ignore_errors=True)

    peek = manager.peek(state.id)

    assert peek.state.status == WorkspaceStatus.ORPHANED
    # No live capture for orphaned workspaces — pane_target rejects them.
    assert peek.agent_snapshot is None


def test_peek_no_snapshot_for_paused_workspace(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="zzz"))
    manager.pause(state.id)

    peek = manager.peek(state.id)

    assert peek.state.status == WorkspaceStatus.PAUSED
    assert peek.agent_snapshot is None
    assert peek.dirty_files == 0  # worktree was removed on pause


# ─── init-status persistence (the diagnosis-from-rail story) ─────────────────


def test_init_status_skipped_when_init_disabled(
    manager: WorkspaceManager,
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="no-init"))

    fresh = manager.store.get(state.id)
    assert fresh.init_status == InitStatus.SKIPPED
    assert fresh.init_duration_ms is None
    assert fresh.init_log_path is None


def test_init_status_ok_when_init_succeeds(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "init_script": {"enabled": True, "inline": "true", "fail_fast": True},
        }
    )
    state_path = tmp_path / "state.json"
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=state_path),
    )
    fake_tmux.init_exit_code = 0
    fake_tmux.init_stdout = "all good\n"

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="ok-init"))

    fresh = manager.store.get(state.id)
    assert fresh.init_status == InitStatus.OK
    assert fresh.init_duration_ms is not None
    assert fresh.init_duration_ms >= 0
    # Log file should have been written by FakeTmux.run_init_script
    assert fresh.init_log_path is not None
    assert Path(fresh.init_log_path).read_text(encoding="utf-8").startswith("--- stdout ---")


def test_init_status_failed_when_init_nonzero_and_fail_fast_off(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "init_script": {"enabled": True, "inline": "false", "fail_fast": False},
        }
    )
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )
    fake_tmux.init_exit_code = 7
    fake_tmux.init_stderr = "boom\n"

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="bad-init"))

    fresh = manager.store.get(state.id)
    assert fresh.init_status == InitStatus.FAILED
    assert fresh.init_duration_ms is not None
    assert fresh.init_log_path is not None


def test_init_log_cleaned_on_kill(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "init_script": {"enabled": True, "inline": "true", "fail_fast": True},
        }
    )
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )
    fake_tmux.init_exit_code = 0

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="killme"))
    fresh = manager.store.get(state.id)
    log = Path(fresh.init_log_path or "")
    assert log.exists()

    manager.kill(state.id)

    assert not log.exists()
