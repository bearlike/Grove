"""The wedged-tmux-server incident, as three regression guards.

On 2026-09-21 every workspace create on the reference host failed with
``could not start tmux session: ['server exited unexpectedly']``. The server had
NOT exited: it was alive in ``do_poll``, still ``LISTEN``ing on its socket,
holding zero sessions and burning zero CPU, wedged for 22 hours by a
control-mode client that outlived the daemon that spawned it.

Three separate defects had to line up, so there are three guards:

1. a control-mode observer is spawned ``start_new_session=True`` and is
   therefore reparented to ``systemd --user`` instead of dying with the daemon;
2. tmux reports a wedged server with the same words as a crashed one, so the
   failure read as a crash and sent the reader hunting for the wrong thing;
3. ``_revive_for_steer`` remembered nothing, so it re-ran a full respawn per
   steer against a runtime that could not possibly answer.

Each test is written from the incident's own inputs rather than from the code's
branches, because the fallback paths here are invisible to any test that does
not construct the exact state in which falling back is wrong.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import grove
from grove.core.manager import _ReviveBreaker
from grove.core.tmux import diagnose_server_failure

# --------------------------------------------------------------------------
# 1. A control-mode observer must not outlive Grove.
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux-only")
def test_die_with_parent_reaps_a_detached_child_when_its_parent_dies() -> None:
    """The mechanism itself, proven against a REAL orphaning.

    ``start_new_session=True`` is what makes this necessary: it detaches the
    child so no process-group signal reaches it, which is precisely why the
    wedged client survived. A test that merely asserts ``preexec_fn`` was
    passed would pass against a no-op body, so this kills a real parent and
    checks the real child.
    """
    script = textwrap.dedent(
        """
        import subprocess, sys, time
        sys.path.insert(0, %r)
        from grove.core.process import die_with_parent
        p = subprocess.Popen(
            ["sleep", "120"], start_new_session=True, preexec_fn=die_with_parent
        )
        print(p.pid, flush=True)
        time.sleep(120)
        """
    ) % str(_src_root())
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert parent.stdout is not None
    child_pid = int(parent.stdout.readline().strip())
    assert _alive(child_pid), "child should be running while its parent lives"

    parent.kill()
    parent.wait(timeout=5)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _alive(child_pid):
            break
        time.sleep(0.05)
    else:  # pragma: no cover - only on a regression
        _force_kill(child_pid)
        pytest.fail(
            "detached child outlived its parent — a control-mode observer would "
            "again survive the daemon and can wedge the tmux server"
        )


@pytest.mark.parametrize(
    ("module", "owner", "function"),
    [
        ("grove.core.pane_events", "TmuxControlPaneSource", "_start_host"),
        ("grove.core.pane_events", "TmuxControlPaneSource", "_start_prefixed"),
        (
            "grove.core.runtime_events",
            "AsyncioRuntimeEventTransport",
            "tmux_control",
        ),
    ],
)
def test_every_control_mode_spawn_asks_to_die_with_grove(
    module: str, owner: str, function: str
) -> None:
    """All three ``-C attach-session`` sites, not just the one that wedged.

    A census rather than a behavioural check, because these spawn real tmux
    clients. It is the cheap half of the guard above: that one proves the
    mechanism works, this one proves nobody adds a fourth observer without it.

    The owning CLASS is named explicitly because ``tmux_control`` also exists as
    a Protocol member whose body is ``...`` — resolving by function name alone
    finds the stub, asserts against an empty body and fails for a reason that
    has nothing to do with the guard.
    """
    mod = importlib.import_module(module)
    source = inspect.getsource(getattr(getattr(mod, owner), function))
    assert "start_new_session=True" in source, "fixture is stale: not a detached spawn"
    assert "preexec_fn=die_with_parent" in source, (
        f"{module}.{owner}.{function} spawns a detached tmux control client that "
        "can outlive the daemon and wedge the server's single-threaded accept loop"
    )


# --------------------------------------------------------------------------
# 2. A wedged server must not be reported as a crashed one.
# --------------------------------------------------------------------------


def test_a_live_socket_turns_the_crash_message_into_a_wedge_diagnosis(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident's own message, with the socket the incident actually had.

    This is the fixture that can only pass if the diagnosis exists: a real
    ``server exited unexpectedly`` AND a socket still on disk, which is exactly
    the combination that proves the server is alive and not answering.
    """
    socket_dir = tmp_path / f"tmux-{os.getuid()}"
    socket_dir.mkdir()
    (socket_dir / "default").touch()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

    result = diagnose_server_failure("['server exited unexpectedly']")

    assert "wedged server, not a crashed one" in result
    assert "kill-server" in result, "the message must name the remedy"
    assert "Restarting Grove will not help" in result


def test_an_absent_socket_leaves_the_message_alone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely exited server unlinks its socket — that case must pass through.

    The counterpart the parametrization above cannot supply: without it the
    diagnosis could unconditionally claim "wedged" and still look correct.
    """
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    assert diagnose_server_failure("['server exited unexpectedly']") == (
        "['server exited unexpectedly']"
    )


def test_an_unrelated_failure_is_never_rewritten(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with a live socket present, only THIS failure gets the diagnosis."""
    socket_dir = tmp_path / f"tmux-{os.getuid()}"
    socket_dir.mkdir()
    (socket_dir / "default").touch()
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

    assert diagnose_server_failure("no space left on device") == "no space left on device"


# --------------------------------------------------------------------------
# 3. A futile auto-revive must stop costing a respawn per steer.
# --------------------------------------------------------------------------


def test_the_breaker_trips_after_repeated_failures_and_reopens_on_cooldown() -> None:
    """The measured shape: six attempts in seven seconds became three.

    Time is injected rather than slept, and the cooldown is re-tested with a
    single probe attempt, which is what lets a transient cause recover without
    a daemon restart.
    """
    now = [1_000.0]
    breaker = _ReviveBreaker(max_failures=3, cooldown_seconds=60.0, clock=lambda: now[0])

    for _ in range(3):
        assert breaker.allows("ws")
        breaker.record_failure("ws")

    assert not breaker.allows("ws"), "a fourth respawn must not run"

    now[0] += 30.0
    assert not breaker.allows("ws"), "still inside the cooldown"

    now[0] += 31.0
    assert breaker.allows("ws"), "one probe attempt after the cooldown"


def test_a_success_clears_the_count_so_the_breaker_measures_consecutive_futility() -> None:
    """Two failures then a success must not leave the workspace one from tripping."""
    breaker = _ReviveBreaker(max_failures=3, cooldown_seconds=60.0)
    breaker.record_failure("ws")
    breaker.record_failure("ws")
    breaker.record_success("ws")

    breaker.record_failure("ws")
    assert breaker.allows("ws"), "a success must reset the consecutive-failure count"


def test_the_breaker_is_keyed_per_workspace() -> None:
    """One unrevivable workspace must not stop its healthy neighbours reviving."""
    breaker = _ReviveBreaker(max_failures=2, cooldown_seconds=60.0)
    breaker.record_failure("broken")
    breaker.record_failure("broken")

    assert not breaker.allows("broken")
    assert breaker.allows("healthy")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _src_root() -> Path:
    return Path(grove.__file__).resolve().parent.parent


def _alive(pid: int) -> bool:
    """Is this process still RUNNING — treating a zombie as dead.

    ``os.kill(pid, 0)`` is the obvious probe and it is wrong here: a
    killed-but-unreaped process is a zombie, which stays in the process table
    and still accepts a signal, so the naive check reports it alive forever.
    That is not hypothetical — it is why this test passed on a developer host
    and failed on CI, whose container PID 1 does not reap, so an orphaned
    grandchild nobody parents is never cleaned up.

    ``Z`` is dead for every purpose this test cares about: the process runs no
    code and holds no memory, so it cannot wedge a tmux server. Waiting for the
    entry to vanish would be waiting on somebody else's reaper.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    except ValueError:  # pragma: no cover - a malformed stat is not a live process
        return False
    # "pid (comm) state ..." — comm may contain spaces and parentheses, so the
    # state field is read after the LAST ')' rather than by splitting on space.
    tail = stat.rpartition(")")[2].split()
    return bool(tail) and tail[0] != "Z"


def _force_kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, 9)
