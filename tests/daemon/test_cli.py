"""``grove daemon serve``'s parent-death linkage — see grove.daemon.cli.

Pins the CONTRACT a refactor must preserve: the ``--print-port`` invocation
(LocalTransport's exact signature, per its own module docstring) arms
PR_SET_PDEATHSIG so the daemon dies with its spawning parent even when that
parent never gets to run its own cleanup (SIGKILL, OOM-kill, closed
terminal); omitting the flag must not touch the OS at all.

Deliberate seam choice: every test below drives the PUBLIC ``serve()`` typer
command and patches only the real OS boundary it crosses — ``os.getppid``,
``os._exit``, ``ctypes.CDLL`` — never the private ``_arm_parent_death_signal``
helper by name. That helper has no other caller and no observable effect
besides these syscalls, so the syscall boundary IS the seam; patching the
helper directly would just be a longer-winded way to assert "the private
function ran" (the repo's own no-no) without pinning anything `serve()`'s
callers can actually see. Not exercised here: a REAL orphan surviving a REAL
SIGKILLed parent — that needs an actual subprocess tree and belongs, if
anywhere, in an end-to-end smoke test, not a unit test of this seam.
"""

from __future__ import annotations

import ctypes
import signal
import types

import pytest
import uvicorn

from grove.daemon import cli as daemon_cli


class _FakeServer:
    """Stands in for `uvicorn.Server` so `serve()` never tries to bind a real socket."""

    def __init__(self, config: object) -> None:
        self.config = config

    async def serve(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the `uvicorn` module itself, not a `daemon_cli.uvicorn` attribute:
    # `serve()` imports uvicorn inside its own body (deferred so mounting the
    # `daemon` subcommand stops costing every `grove` invocation ~125 ms), so
    # there is no module-scope name to rebind. Patching the real module still
    # intercepts, because the deferred import resolves the same object out of
    # `sys.modules`.
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)


def test_serve_arms_pdeathsig_for_print_port_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon_cli.sys, "platform", "linux")
    calls: list[tuple[int, int, int, int, int]] = []

    class _FakeLibc:
        def prctl(self, *args: int) -> int:
            calls.append(args)  # type: ignore[arg-type]
            return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: _FakeLibc())
    monkeypatch.setattr(daemon_cli.os, "getppid", lambda: 4242)  # stable — no race
    exited: list[int] = []
    monkeypatch.setattr(daemon_cli.os, "_exit", exited.append)

    daemon_cli.serve(host="127.0.0.1", port=7421, print_port=True)

    assert calls == [(1, signal.SIGTERM, 0, 0, 0)]  # PR_SET_PDEATHSIG == 1
    assert exited == []


def test_serve_does_not_touch_os_without_print_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """The systemd-managed invocation carries no ``--print-port`` and must be untouched."""
    monkeypatch.setattr(daemon_cli.sys, "platform", "linux")

    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("ctypes.CDLL must not be touched when --print-port is absent")

    monkeypatch.setattr(ctypes, "CDLL", _boom)

    daemon_cli.serve(host="127.0.0.1", port=7421, print_port=False)  # must not raise


def test_serve_exits_if_parent_already_gone_when_arming(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fork->exec->import window is real: if the parent pid changed by the
    time prctl() runs, no SIGTERM is ever coming — exit now instead of
    becoming the exact orphan this function exists to prevent."""
    monkeypatch.setattr(daemon_cli.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: types.SimpleNamespace(prctl=lambda *a: 0))
    ppids = iter([4242, 1])  # reparented to init between the two getppid() calls
    monkeypatch.setattr(daemon_cli.os, "getppid", lambda: next(ppids))
    exited: list[int] = []
    monkeypatch.setattr(daemon_cli.os, "_exit", exited.append)

    daemon_cli.serve(host="127.0.0.1", port=7421, print_port=True)

    assert exited == [1]


def test_serve_logs_and_skips_reexec_check_on_prctl_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed prctl() (permission/seccomp denial) must be logged, not silently
    ignored — silently unarmed is exactly the failure this exists to prevent."""
    monkeypatch.setattr(daemon_cli.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: types.SimpleNamespace(prctl=lambda *a: -1))
    getppid_calls: list[int] = []

    def _getppid() -> int:
        getppid_calls.append(1)
        return 4242

    monkeypatch.setattr(daemon_cli.os, "getppid", _getppid)
    exited: list[int] = []
    monkeypatch.setattr(daemon_cli.os, "_exit", exited.append)

    daemon_cli.serve(host="127.0.0.1", port=7421, print_port=True)

    assert exited == []  # a failed arm is a logged miss, not a crash
    assert len(getppid_calls) == 1  # no point re-checking ppid for a signal never armed


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_serve_noop_off_linux(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """No prctl exists off Linux — must degrade to a clean no-op, never raise."""
    monkeypatch.setattr(daemon_cli.sys, "platform", platform)

    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("ctypes.CDLL must not be touched off Linux")

    monkeypatch.setattr(ctypes, "CDLL", _boom)

    daemon_cli.serve(host="127.0.0.1", port=7421, print_port=True)  # must not raise
