"""End-to-end integration: real tmux, real git, real subprocesses.

Skipped by default. Run with `pytest -m integration` on a machine that
has tmux + git on PATH (i.e., Linux or macOS native, or Windows-via-WSL).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import (
    build_workspace_layout,
    create_session,
    fit_window_to_client,
    has_session,
    kill_session,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_tmux,
    pytest.mark.skipif(
        shutil.which("tmux") is None or shutil.which("git") is None,
        reason="tmux and git must be installed",
    ),
]


@pytest.fixture
def real_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@grove.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Grove Integration"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("integration\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo.resolve()


def _real_manager(real_repo: Path, tmp_path: Path) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "grove-it/",
            },
            "tmux": {
                "session_prefix": "grove-it-",
                "history_limit": 1000,
            },
            "agents": [
                # Use a no-op command that holds a tmux pane open without spawning a real agent.
                {"name": "shell", "command": "sh -c 'while :; do sleep 30; done'"},
            ],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=real_repo, cfg=cfg, store=store)


def _worktree_paths(repo: Path) -> set[Path]:
    """Resolved-Path set from `git worktree list`. Cross-platform safe — see
    `tests/core/test_workspace_lifecycle.py::_worktrees` for the rationale."""
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    paths: set[Path] = set()
    for line in out.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line[len("worktree ") :].strip()).resolve())
    return paths


def test_full_lifecycle_with_real_tmux_and_git(real_repo: Path, tmp_path: Path) -> None:
    manager = _real_manager(real_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="it1"))
    try:
        # Real branch and worktree exist
        assert Path(state.worktree_path).resolve() in _worktree_paths(real_repo)

        branches = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            cwd=real_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert state.branch in branches

        # Real tmux session exists. Give the layout helper a beat to settle.
        for _ in range(20):
            if has_session(state.tmux_session):
                break
            time.sleep(0.05)
        assert has_session(state.tmux_session)

        # Pause: worktree gone, branch retained, tmux gone.
        manager.pause(state.id)
        assert Path(state.worktree_path).resolve() not in _worktree_paths(real_repo)
        branches = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            cwd=real_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert state.branch in branches
        assert not has_session(state.tmux_session)

        # Resume: back to running.
        manager.resume(state.id)
        assert has_session(state.tmux_session)
    finally:
        # Always tear down so a partial test doesn't leak tmux sessions / worktrees.
        with contextlib.suppress(Exception):
            manager.kill(state.id)
        assert not has_session(state.tmux_session)
        branches_after = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            cwd=real_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert state.branch not in branches_after


def _window_size_option(session: str) -> str:
    """The current window's `window-size` option, as real tmux reports it."""
    return subprocess.run(
        ["tmux", "show-options", "-w", "-t", session, "-v", "window-size"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_fit_window_to_client_clears_the_manual_pin(real_repo: Path) -> None:
    """Attach sizing must UNDO `window-size: manual`, not merely avoid setting it.

    A pinned window is a one-way door: tmux stops re-fitting it to the client
    forever — through later attaches and terminal resizes alike — so it keeps
    whatever height was forced on it. Force the client's FULL height (what
    `#{client_height}` reports, status-bar rows included) and the window ends up
    taller than the rows tmux can show above the status bar; the pane's last
    rows — the agent's footer — get painted under it and are simply never seen.

    Sessions Grove didn't create (claude-squad imports) arrive already pinned,
    so clearing is the load-bearing half. Verified against real tmux: this is
    the invariant a fake subprocess cannot prove, since `manual` is a state tmux
    itself holds, and `resize-window` sets it as a documented side effect rather
    than something we pass a flag for.
    """
    session = "grove-it-fit-window"
    with contextlib.suppress(Exception):
        kill_session(session)
    create_session(session, real_repo)
    try:
        # create_session hands sizing to tmux...
        assert _window_size_option(session) == "latest"

        # ...and an explicit resize — what attach used to do, and what a
        # claude-squad import ships with — takes it away again.
        subprocess.run(
            ["tmux", "resize-window", "-t", session, "-x", "100", "-y", "40"],
            check=True,
            capture_output=True,
        )
        assert _window_size_option(session) == "manual"

        fit_window_to_client(session)

        assert _window_size_option(session) == "latest"
    finally:
        kill_session(session)


def test_shell_command_really_reaches_window_zeros_pane(real_repo: Path, tmp_path: Path) -> None:
    """The producer half of "window 0 crosses into the container".

    A fake layout can only record that the argument was threaded; whether tmux
    actually runs it in the SHELL window (rather than in the agent window, or
    nowhere) is a fact only real tmux holds — and an unwired producer is a
    failure this subsystem has shipped before. A marker file stands in for
    the ``devcontainer exec`` a container workspace would run here, so the
    test needs no container to prove the wiring.
    """
    cfg = GroveConfig.model_validate({"tmux": {"session_prefix": "grove-it-"}})
    session = "grove-it-shell-window"
    marker = tmp_path / "shell-window-ran"
    with contextlib.suppress(Exception):
        kill_session(session)
    create_session(session, real_repo)
    try:
        build_workspace_layout(
            session,
            cfg=cfg,
            worktree=real_repo,
            command="sleep 30",
            shell_command=f"touch {marker}",
        )
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.1)

        assert marker.exists(), "window 0 never ran the shell command"
    finally:
        kill_session(session)


def test_agent_command_starts_without_waiting_for_a_fresh_prompt(
    real_repo: Path, tmp_path: Path
) -> None:
    """The agent window launches atomically even when shell startup is slow."""
    cfg = GroveConfig.model_validate({"tmux": {"session_prefix": "grove-it-"}})
    session = "grove-it-agent-window"
    marker = tmp_path / "agent-window-ran"
    with contextlib.suppress(Exception):
        kill_session(session)
    create_session(session, real_repo)
    try:
        build_workspace_layout(
            session,
            cfg=cfg,
            worktree=real_repo,
            command=f"touch {marker}; sleep 30",
        )
        for _ in range(50):
            if marker.exists():
                break
            time.sleep(0.1)

        assert marker.exists(), "the agent command waited unsubmitted at a fresh prompt"
    finally:
        kill_session(session)


def test_init_script_real_subprocess_creates_marker(real_repo: Path, tmp_path: Path) -> None:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "grove-it/",
            },
            "tmux": {"session_prefix": "grove-it-", "history_limit": 1000},
            "agents": [
                {"name": "shell", "command": "sh -c 'while :; do sleep 30; done'"},
            ],
            "init_script": {
                "enabled": True,
                "shell": "sh",
                "inline": "touch grove-init-ran.txt",
                "timeout_seconds": 10,
            },
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    manager = WorkspaceManager(repo_root=real_repo, cfg=cfg, store=store)

    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="it-init"))
    try:
        marker = Path(state.worktree_path) / "grove-init-ran.txt"
        assert marker.exists(), "init script should have created the marker file"
    finally:
        with contextlib.suppress(Exception):
            manager.kill(state.id)
