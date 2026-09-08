"""Explicit runtime replacement waits for the captured provider, not its pane."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from grove.core.errors import ProcessError
from grove.core.process import ProcessTree

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process identities")


def test_process_tree_waits_for_a_provider_ignoring_hup(tmp_path: Path) -> None:
    stopped = tmp_path / "stopped"
    child_code = (
        "import signal,time,sys\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "def stop(*args):\n"
        " time.sleep(.2)\n"
        f" Path({str(stopped)!r}).write_text('stopped')\n"
        " sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    parent_code = (
        "import subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}], stdout=subprocess.PIPE)\n"
        "p.stdout.readline()\n"
        "print(p.pid, flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True
    )
    child: int | None = None
    try:
        assert parent.stdout is not None
        child = int(parent.stdout.readline())
        tree = ProcessTree.capture((parent.pid,))
        assert {pid for pid, _ in tree.identities} == {parent.pid, child}
        # tmux's HUP removes the pane/worker first, but the provider can survive.
        parent.send_signal(signal.SIGHUP)
        parent.wait(timeout=5)
        os.kill(child, signal.SIGHUP)
        tree.terminate_and_wait()
        assert stopped.read_text() == "stopped"
        assert ProcessTree.capture((child,)).identities == ()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)


def test_process_tree_refuses_to_overlap_a_provider_that_does_not_exit() -> None:
    code = (
        "import signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    try:
        assert child.stdout is not None
        child.stdout.readline()
        tree = ProcessTree.capture((child.pid,))
        with pytest.raises(ProcessError, match="refusing to launch another owner"):
            tree.terminate_and_wait(timeout=0.1)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait(timeout=5)


def test_stop_timeout_keeps_ancestry_available_for_retry() -> None:
    child_code = (
        "import signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    parent_code = (
        "import subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}], stdout=subprocess.PIPE)\n"
        "p.stdout.readline()\n"
        "print(p.pid, flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True
    )
    child: int | None = None
    try:
        assert parent.stdout is not None
        child = int(parent.stdout.readline())
        for _ in range(2):
            tree = ProcessTree.capture((parent.pid,), include_roots=False)
            assert [pid for pid, _ in tree.identities] == [child]
            with pytest.raises(ProcessError, match="refusing to launch another owner"):
                tree.terminate_and_wait(timeout=0.1)
            assert parent.poll() is None
    finally:
        if child is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
        parent.kill()
        parent.wait(timeout=5)


def test_process_lookup_race_is_an_exited_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def vanished(*args: object, **kwargs: object) -> str:
        raise ProcessLookupError("process exited while reading stat")

    monkeypatch.setattr(Path, "read_text", vanished)
    assert ProcessTree._identity(tmp_path) is None


def test_process_tree_never_signals_a_reused_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ProcessTree(((os.getpid(), "not-this-incarnation"),))

    def unexpected_signal(*args: object) -> None:
        pytest.fail("reused PID was signalled")

    monkeypatch.setattr(os, "kill", unexpected_signal)
    tree.terminate_and_wait()
