"""#968: a host workspace tmux server runs in its own systemd user scope that kill ends."""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core import tmux
from grove.core.scope import Scope


def test_the_scope_is_named_after_the_workspace_socket() -> None:
    assert Scope.for_socket("grove-abc123def456").unit == "grove-abc123def456.scope"


def test_wrap_runs_the_command_in_a_delegated_collectable_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Scope, "available", staticmethod(lambda: True))
    argv = Scope.for_socket("grove-x").wrap(["tmux", "-L", "grove-x", "new-session", "-d"])
    assert argv == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--unit=grove-x.scope",
        "-p",
        "Delegate=yes",
        "--",
        "tmux",
        "-L",
        "grove-x",
        "new-session",
        "-d",
    ]


def test_without_systemd_the_command_runs_unchanged_and_teardown_is_approximate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Scope, "available", staticmethod(lambda: False))
    scope = Scope.for_socket("grove-x")
    assert scope.wrap(["tmux", "new-session"]) == ["tmux", "new-session"]
    assert not scope.exact
    assert scope.stop() is False


def test_an_unreachable_user_bus_is_no_scope_even_on_a_systemd_host(
    tmp_path: Path,
) -> None:
    """A daemon started without ``XDG_RUNTIME_DIR`` on a systemd host must not
    wrap its tmux start in ``systemd-run --user``, which fails to reach the bus
    and turns every create into a 500."""
    (tmp_path / "bus").touch()
    assert not Scope.user_bus_reachable({})
    assert not Scope.user_bus_reachable({"XDG_RUNTIME_DIR": str(tmp_path / "missing")})
    assert Scope.user_bus_reachable({"XDG_RUNTIME_DIR": str(tmp_path)})
    assert Scope.user_bus_reachable({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"})


@pytest.mark.parametrize(("socket", "stopped"), [("grove-x", ["grove-x.scope"]), ("", [])])
def test_ending_a_private_server_stops_its_scope_and_the_default_server_never_is(
    monkeypatch: pytest.MonkeyPatch, socket: str, stopped: list[str]
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(Scope, "stop", lambda self: seen.append(self.unit) or True)
    monkeypatch.setattr(tmux, "_run_tmux", lambda *a, **k: None)
    server = tmux.TmuxServer(socket=socket)
    if socket:
        tmux.kill_server(server)
    else:
        with pytest.raises(tmux.TmuxError):
            tmux.kill_server(server)
    assert seen == stopped
