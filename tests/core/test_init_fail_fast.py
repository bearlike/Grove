"""One fail_fast decision, both init-failure shapes, across all three verbs.

`tmux.run_init_script` fails two ways: the script runs and exits non-zero, or
the call *raises* before the subprocess ever starts (mutually exclusive
`inline`+`path`, a missing script file) or on timeout. `create()` branched on
those separately and only the exit-code arm consulted `init_script.fail_fast`,
so a raise was unconditionally fatal — a user who had explicitly set
`fail_fast: false` still lost every workspace (worktree, branch, and record) to
a config typo. These pin both shapes through create / resume / respawn, plus
the init log a pre-execution raise leaves behind (it never reaches
`run_init_script`'s own log write, so the manager writes it).

Rollback is asserted on the git seams — was `worktree_remove` / `branch_delete`
*called* — not on survival alone: the ROOT-placement precedent in
`test_root_workspace.py` is that git's own refusals can make a survival-only
assertion pass with the gate missing. The spies here delegate to the real
methods so both the call log and the resulting repo state are honest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, TmuxError
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import InitStatus, WorkspaceStatus
from tests.conftest import FakeTmux

# The real message a user hit: a project config that set `path` while the user
# layer still carried `inline`. run_init_script raises this before running
# anything, so it is the exact text that must survive into the error a
# fail_fast create raises.
_CAUSE = "init_script: specify either `inline` or `path`, not both"


def _manager(
    tmp_repo: Path,
    tmp_path: Path,
    *,
    fail_fast: bool,
    run_on_resume: bool = False,
) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "init_script": {
                "enabled": True,
                "inline": "true",
                "fail_fast": fail_fast,
                "run_on_resume": run_on_resume,
            },
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )


def _spy(monkeypatch: pytest.MonkeyPatch, git: GitRepo, name: str) -> list[tuple[Any, ...]]:
    """Record calls to a GitRepo method while still performing them.

    Delegating (unlike `test_root_workspace._spy`, which stubs the method out)
    because these tests assert both that the destructive seam was or wasn't
    called AND what the repo looks like afterwards.
    """
    real = getattr(git, name)
    calls: list[tuple[Any, ...]] = []

    def recorder(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(git, name, recorder)
    return calls


# ─── create: a raise obeys fail_fast exactly like a non-zero exit ────────────


def test_create_init_raise_with_fail_fast_off_keeps_workspace(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug: `fail_fast: false` asked for the workspace to be KEPT, and a
    raising init destroyed it anyway."""
    mgr = _manager(tmp_repo, tmp_path, fail_fast=False)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="survives"))

    assert removed == []  # nothing was rolled back …
    assert deleted == []
    fresh = mgr.store.get(state.id)
    assert fresh.status == WorkspaceStatus.RUNNING  # … and the launch went ahead
    assert state.tmux_session in fake_tmux.sessions
    assert Path(fresh.worktree_path).is_dir()
    # The failure is recorded, not swallowed: FAILED with a log to read.
    assert fresh.init_status == InitStatus.FAILED
    assert fresh.init_log_path is not None
    assert _CAUSE in Path(fresh.init_log_path).read_text(encoding="utf-8")


def test_create_init_raise_with_fail_fast_on_rolls_back_and_names_the_cause(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    init_logs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail_fast=True stays loud: full rollback, and the raise's own message —
    the only thing that explains a pre-execution failure — reaches the user."""
    mgr = _manager(tmp_repo, tmp_path, fail_fast=True)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    with pytest.raises(GroveError) as excinfo:
        mgr.create(CreateWorkspaceRequest(agent_name="claude", title="dies"))

    assert len(removed) == 1
    assert len(deleted) == 1
    assert mgr.store.load_all() == []
    assert list((tmp_path / "trees").glob("*")) == []
    message = str(excinfo.value)
    assert _CAUSE in message
    assert "fail_fast=True" in message
    # …and the log survives the rollback so the rail can show it.
    assert len(list(init_logs.glob("*-init.log"))) == 1


@pytest.mark.parametrize("fail_fast", [True, False])
def test_create_init_raise_writes_a_diagnosable_init_log(
    fail_fast: bool,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    init_logs: Path,
) -> None:
    """A raise happens before run_init_script writes anything, so the manager
    writes the cause itself — otherwise the failure leaves no trail at all."""
    mgr = _manager(tmp_repo, tmp_path, fail_fast=fail_fast)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    request = CreateWorkspaceRequest(agent_name="claude", title="logged")

    if fail_fast:
        with pytest.raises(GroveError):
            mgr.create(request)
    else:
        mgr.create(request)

    logs = list(init_logs.glob("*-init.log"))
    assert len(logs) == 1
    body = logs[0].read_text(encoding="utf-8")
    assert _CAUSE in body
    # Same section shape run_init_script writes, so the peek rail renders it
    # identically and `_init_failure_detail`'s tail shows the cause.
    assert body.startswith("--- stdout ---")
    assert "--- stderr ---" in body


# ─── create: the exit-code arm is unchanged (regression guards) ──────────────


def test_create_init_nonzero_with_fail_fast_off_keeps_workspace(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=False)
    fake_tmux.init_exit_code = 7
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="soft-fail"))

    assert removed == []
    assert deleted == []
    fresh = mgr.store.get(state.id)
    assert fresh.status == WorkspaceStatus.RUNNING
    assert fresh.init_status == InitStatus.FAILED
    assert state.tmux_session in fake_tmux.sessions


def test_create_init_nonzero_with_fail_fast_on_rolls_back(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=True)
    fake_tmux.init_exit_code = 3
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    with pytest.raises(GroveError) as excinfo:
        mgr.create(CreateWorkspaceRequest(agent_name="claude", title="hard-fail"))

    assert len(removed) == 1
    assert len(deleted) == 1
    assert mgr.store.load_all() == []
    message = str(excinfo.value)
    assert "init script exited 3" in message
    assert "fail_fast=True" in message


# ─── resume / respawn parity: the same decision, their own cleanup ───────────


def test_resume_init_raise_with_fail_fast_off_keeps_the_worktree(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=False, run_on_resume=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="unpause"))
    mgr.pause(state.id)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")

    resumed = mgr.resume(state.id)

    assert removed == []  # the recreated worktree stays
    assert resumed.status == WorkspaceStatus.RUNNING
    assert resumed.init_status == InitStatus.FAILED
    assert Path(resumed.worktree_path).is_dir()


def test_resume_init_raise_with_fail_fast_on_drops_the_recreated_worktree(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=True, run_on_resume=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="unpause-dies"))
    mgr.pause(state.id)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    with pytest.raises(GroveError) as excinfo:
        mgr.resume(state.id)

    assert len(removed) == 1
    assert deleted == []  # resume never deletes the branch — only kill does
    assert _CAUSE in str(excinfo.value)
    assert mgr.store.get(state.id).status == WorkspaceStatus.PAUSED


def test_respawn_init_raise_with_fail_fast_off_relaunches(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=False, run_on_resume=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="respawn-soft"))
    fake_tmux.sessions.discard(state.tmux_session)  # the session vanished → OFFLINE
    fake_tmux.init_raises = TmuxError(_CAUSE)

    respawned = mgr.respawn(state.id)

    assert respawned.status == WorkspaceStatus.RUNNING
    assert respawned.init_status == InitStatus.FAILED
    assert state.tmux_session in fake_tmux.sessions  # relaunched despite the init


def test_respawn_init_raise_with_fail_fast_on_aborts_before_relaunch(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, fail_fast=True, run_on_resume=True)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="respawn-hard"))
    fake_tmux.sessions.discard(state.tmux_session)
    fake_tmux.init_raises = TmuxError(_CAUSE)
    removed = _spy(monkeypatch, mgr._git, "worktree_remove")
    deleted = _spy(monkeypatch, mgr._git, "branch_delete")

    with pytest.raises(GroveError) as excinfo:
        mgr.respawn(state.id)

    # Respawn touches tmux only — its abort destroys nothing on the git side.
    assert removed == []
    assert deleted == []
    assert _CAUSE in str(excinfo.value)
    assert state.tmux_session not in fake_tmux.sessions
