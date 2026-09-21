"""Nobody is left standing on a worktree that is about to be unlinked.

The regression these cover is measured, not hypothetical: on the reference
host 2026-09-18, nine shells orphaned onto deleted Grove worktrees were
spinning ~7.6 of 8 cores between them, 9.5 CPU-days burned. `kill-session`
only SIGHUPs each pane's own process group, so a prompt-forked subshell
survives, reparents to `systemd --user`, and re-globs a path that no longer
resolves forever.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from grove.core.process import reap_cwd_holders

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc cwd scan")


def _spawn_in(cwd: Path, *, ignore_term: bool = False) -> subprocess.Popen[bytes]:
    """A process parked in `cwd`, optionally deaf to SIGTERM."""
    code = "import signal,time\n"
    if ignore_term:
        code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    code += "print('ready', flush=True)\nwhile True: time.sleep(1)\n"
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=cwd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    proc.stdout.readline()  # started, and cwd is in place
    return proc


def _wait_gone(proc: subprocess.Popen[bytes], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_reaps_a_process_sitting_in_the_doomed_directory(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    proc = _spawn_in(worktree)
    try:
        assert proc.pid in reap_cwd_holders(worktree)
        assert _wait_gone(proc), "holder should have been signalled"
    finally:
        proc.kill()
        proc.wait()


def test_escalates_to_sigkill_when_sigterm_is_declined(tmp_path: Path) -> None:
    """The survivors are wedged mid-rc and never handle a catchable signal."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    proc = _spawn_in(worktree, ignore_term=True)
    try:
        reap_cwd_holders(worktree, timeout=0.5)
        assert _wait_gone(proc), "a SIGTERM-deaf holder must still be reclaimed"
        assert proc.poll() == -signal.SIGKILL
    finally:
        proc.kill()
        proc.wait()


def test_leaves_processes_outside_the_directory_alone(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    sibling = tmp_path / "worktree-next-door"
    worktree.mkdir()
    sibling.mkdir()
    # A prefix that merely SHARES leading characters must not match.
    proc = _spawn_in(sibling)
    try:
        assert proc.pid not in reap_cwd_holders(worktree)
        assert proc.poll() is None, "a bystander was signalled"
    finally:
        proc.kill()
        proc.wait()


def test_matches_a_nested_cwd(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    nested = worktree / "webapp" / "src"
    nested.mkdir(parents=True)
    proc = _spawn_in(nested)
    try:
        assert proc.pid in reap_cwd_holders(worktree)
        assert _wait_gone(proc)
    finally:
        proc.kill()
        proc.wait()


def test_is_a_no_op_for_an_empty_directory(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    assert reap_cwd_holders(worktree) == ()


def test_never_signals_the_caller(tmp_path: Path) -> None:
    """The scan skips its own pid.

    Asserted against a directory this test OWNS. The earlier version passed
    `Path.cwd()` -- the repo root -- which is a live reap of every process
    sitting in the working tree: the test runner's own parent shell, the
    editor, any sibling `uv`/`git`. It proved the self-skip and then SIGTERMed
    the session running it, so a full-suite run died here with no failure
    report, which is exactly how it stayed unnoticed. A process parked in
    `tmp_path` exercises the same branch and can only take itself down.
    """
    holder = _spawn_in(tmp_path)
    try:
        assert os.getpid() not in reap_cwd_holders(tmp_path)
        assert _wait_gone(holder), "the holder itself is still reaped"
    finally:
        holder.kill()
        holder.wait()
