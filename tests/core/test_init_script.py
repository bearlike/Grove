"""Real-subprocess tests for `tmux.run_init_script`.

These exercise actual `bash -c ...` invocation but stay inside grove.core
(no tmux). They live in tests/core because they don't require tmux on
the host — only `bash`/`sh`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

from grove.core import tmux as tmux_mod
from grove.core.config import InitScriptConfig
from grove.core.errors import TmuxError


def _detect_real_posix_shell() -> Literal["bash", "sh"] | None:
    """Return the first shell whose `-c` actually executes commands.

    `shutil.which("bash")` alone is misleading on Windows GitHub runners:
    they ship a `bash.exe` that's the WSL launcher, and with no WSL
    distribution installed it prints "no installed distributions" and
    exits 1 for *every* invocation — it never runs the user's command.
    The probe runs a deterministic echo and verifies the output, so a
    stub shell can't pass.
    """
    for shell in ("bash", "sh"):
        if shutil.which(shell) is None:
            continue
        try:
            result = subprocess.run(
                [shell, "-c", "echo grove-shell-probe"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        if result.returncode == 0 and "grove-shell-probe" in result.stdout:
            return shell
    return None


_REAL_POSIX_SHELL = _detect_real_posix_shell()

pytestmark = pytest.mark.skipif(
    _REAL_POSIX_SHELL is None,
    reason=(
        "POSIX shell required — Windows GitHub runners ship a WSL `bash.exe` "
        "stub that exits 1 on every command when no distro is installed"
    ),
)


def _shell() -> Literal["bash", "sh"]:
    """The detected real POSIX shell. The pytestmark above guarantees non-None."""
    assert _REAL_POSIX_SHELL is not None
    return _REAL_POSIX_SHELL


def test_disabled_returns_zero_without_running(tmp_path: Path) -> None:
    cfg = InitScriptConfig(enabled=False, inline="false")  # would fail if run
    assert tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path) == 0


def test_inline_script_creates_marker(tmp_path: Path) -> None:
    cfg = InitScriptConfig(
        enabled=True, shell=_shell(), inline="touch marker.txt", timeout_seconds=10
    )
    rc = tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)
    assert rc == 0
    assert (tmp_path / "marker.txt").exists()


def test_path_script_runs_relative_to_repo_root(tmp_path: Path) -> None:
    script = tmp_path / "init.sh"
    script.write_text(f"#!/usr/bin/env {_shell()}\ntouch ran.txt\n", encoding="utf-8")
    script.chmod(0o755)
    cfg = InitScriptConfig(enabled=True, shell=_shell(), path="init.sh", timeout_seconds=10)
    rc = tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)
    assert rc == 0
    assert (tmp_path / "ran.txt").exists()


def test_extra_env_is_visible_to_script(tmp_path: Path) -> None:
    cfg = InitScriptConfig(
        enabled=True,
        shell=_shell(),
        inline='echo "$GROVE_REPO" > out.txt',
        timeout_seconds=10,
    )
    rc = tmux_mod.run_init_script(
        cfg,
        worktree=tmp_path,
        repo_root=tmp_path,
        extra_env={"GROVE_REPO": "/some/repo"},
    )
    assert rc == 0
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "/some/repo"


def test_nonzero_exit_returns_exit_code(tmp_path: Path) -> None:
    cfg = InitScriptConfig(enabled=True, shell=_shell(), inline="exit 7", timeout_seconds=10)
    rc = tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)
    assert rc == 7


def test_inline_and_path_together_raise(tmp_path: Path) -> None:
    cfg = InitScriptConfig(
        enabled=True,
        shell=_shell(),
        inline="true",
        path="init.sh",
        timeout_seconds=10,
    )
    with pytest.raises(TmuxError, match=r"inline.*path"):
        tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)


def test_timeout_raises(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("sleep semantics differ on Windows")
    cfg = InitScriptConfig(enabled=True, shell=_shell(), inline="sleep 5", timeout_seconds=1)
    with pytest.raises(TmuxError, match="timeout"):
        tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)


def test_missing_path_raises(tmp_path: Path) -> None:
    cfg = InitScriptConfig(
        enabled=True, shell=_shell(), path="does-not-exist.sh", timeout_seconds=10
    )
    with pytest.raises(TmuxError, match="not found"):
        tmux_mod.run_init_script(cfg, worktree=tmp_path, repo_root=tmp_path)
