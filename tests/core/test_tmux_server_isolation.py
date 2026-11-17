"""Each host workspace runs on its own tmux server (fault isolation).

One tmux server used to hold every workspace on the host, and four segfaults
in tmux 3.4's control-mode write path ended 34-54 panes each. These pin the
pieces that keep a workspace on ITS server, and the legacy records on the
shared one, so a refactor cannot quietly route a read to the wrong server
(which reads exactly like a dead session).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from grove.core import tmux
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import HostAttachView
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import DEFAULT_SERVER, HostAttach, TmuxServer
from grove.hook_producer import tmux_pane_identity
from tests.conftest import FakeTmux


def test_default_server_command_is_unchanged() -> None:
    assert DEFAULT_SERVER.command == ("tmux",)


def test_private_server_command_names_its_socket() -> None:
    assert TmuxServer(socket="grove-abc").command == ("tmux", "-L", "grove-abc")


def test_a_private_server_starts_with_groves_conf_not_the_users(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user's resurrect/continuum would restore their environment into it."""
    created: list[dict[str, object]] = []

    class _Server:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr(tmux.libtmux, "Server", _Server)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    TmuxServer(socket="grove-abc", conf=str(tmp_path / "c.conf")).libtmux()
    assert created == [{"socket_name": "grove-abc", "config_file": str(tmp_path / "c.conf")}]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs a real tmux")
def test_a_private_server_takes_the_users_styling_but_keeps_groves_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``tmux-user.conf`` styles every workspace server and cannot undo Grove's settings."""
    user_conf = tmp_path / 'it\'s "Application Support" $HOME #{x}' / "tmux-user.conf"
    user_conf.parent.mkdir()
    user_conf.write_text("set -g status-style bg=magenta\nset -g exit-empty off\n")
    monkeypatch.setattr(tmux.paths, "tmux_user_conf_path", lambda: user_conf)
    conf = tmp_path / "server.conf"
    socket = f"grove-test-{os.getpid()}"

    def show(option: str) -> str:
        argv = ["tmux", "-L", socket, "show", "-gv", option]
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()

    for text, styled in ((tmux.server_conf_text(), True), (tmux.GROVE_SERVER_CONF, False)):
        conf.write_text(text)
        subprocess.run(["tmux", "-L", socket, "-f", str(conf), "new-session", "-d"], check=True)
        try:
            assert ("magenta" in show("status-style")) is styled
            assert show("exit-empty") == "on"
        finally:
            subprocess.run(["tmux", "-L", socket, "kill-server"], check=False)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs a real tmux")
def test_a_missing_user_conf_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tmux.paths, "tmux_user_conf_path", lambda: tmp_path / "absent.conf")
    conf = tmp_path / "server.conf"
    conf.write_text(tmux.server_conf_text())
    socket = f"grove-test-missing-{os.getpid()}"
    started = subprocess.run(
        ["tmux", "-L", socket, "-f", str(conf), "new-session", "-d"], check=False
    )
    subprocess.run(["tmux", "-L", socket, "kill-server"], check=False)
    assert started.returncode == 0


@pytest.mark.parametrize(
    ("socket", "session"),
    [("", "grove-x-20261010"), ("grove-abc123", "grove-x-20261010")],
)
def test_handle_round_trips_and_keeps_legacy_identities_bare(socket: str, session: str) -> None:
    server = TmuxServer(socket=socket)
    handle = server.handle(session)
    # An identity that existed before isolation must not change shape.
    assert (handle == session) is (socket == "")
    parsed_server, parsed_session = TmuxServer.parse_handle(handle)
    assert (parsed_server.socket, parsed_session) == (socket, session)


def test_attach_on_default_server_outside_tmux_is_a_plain_attach() -> None:
    assert HostAttach("s", inside_outer_tmux=False).terminal_argv() == [
        "tmux",
        "attach",
        "-t",
        "s",
    ]


def test_attach_on_default_server_inside_tmux_switches_the_client() -> None:
    assert HostAttach("s", inside_outer_tmux=True).terminal_argv() == [
        "tmux",
        "switch-client",
        "-t",
        "s",
    ]


def test_attach_on_private_server_inside_tmux_opens_a_window_instead_of_switching() -> None:
    """`switch-client` cannot cross servers; nesting needs TMUX cleared."""
    argv = HostAttach(
        "s", inside_outer_tmux=True, read_only=True, tmux_socket="grove-abc"
    ).terminal_argv()
    assert argv[:4] == ["tmux", "new-window", "-n", "s"]
    assert argv[4] == "env -u TMUX tmux -L grove-abc attach -r -t s"


def test_wire_attach_argv_reaches_the_private_server() -> None:
    view = HostAttachView.from_instruction(
        HostAttach("s", inside_outer_tmux=False, tmux_socket="grove-abc")
    )
    assert view.tmux_socket == "grove-abc"
    assert view.attach_argv() == ["tmux", "-L", "grove-abc", "attach", "-t", "s"]


def test_wire_attach_from_an_older_daemon_decodes_to_the_default_server() -> None:
    view = HostAttachView.model_validate({"tmux_session": "s", "inside_outer_tmux": False})
    assert view.attach_argv() == ["tmux", "attach", "-t", "s"]


@pytest.mark.parametrize(
    ("tmux_env", "expected"),
    [
        ("", "%3"),
        ("/tmp/tmux-1000/default,123,0", "%3"),
        ("/tmp/tmux-1000/grove-abc123,456,0", "grove-abc123:%3"),
    ],
)
def test_hook_pane_identity_is_unique_across_servers(
    monkeypatch: pytest.MonkeyPatch, tmux_env: str, expected: str
) -> None:
    """Pane ids restart at %0 on every server, and adoption matches on them."""
    monkeypatch.setenv("TMUX_PANE", "%3")
    if tmux_env:
        monkeypatch.setenv("TMUX", tmux_env)
    else:
        monkeypatch.delenv("TMUX", raising=False)
    assert tmux_pane_identity() == expected


def _manager(tmp_repo: Path, tmp_path: Path) -> tuple[WorkspaceManager, JsonWorkspaceStore]:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store), store


def test_host_create_runs_on_its_own_server_and_reads_follow_it(
    fake_tmux: FakeTmux, tmp_repo: Path, tmp_path: Path
) -> None:
    manager, _ = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="iso"))

    assert state.tmux_socket == TmuxServer.for_workspace(state.id)
    assert fake_tmux.session_servers[state.tmux_session].socket == state.tmux_socket
    # The record round-trips through the store with its server.
    assert manager.get(state.id).tmux_socket == state.tmux_socket
    assert manager.pane_target(state.id) is not None
    (live,) = manager.list()
    pane = manager.pane_for(live)
    assert pane is not None and pane.command == ("tmux", "-L", state.tmux_socket)
    attach = manager.attach(state.id)
    assert isinstance(attach, HostAttach) and attach.tmux_socket == state.tmux_socket

    manager.kill(state.id)
    assert state.tmux_session not in fake_tmux.sessions


def test_a_record_written_before_isolation_stays_on_the_default_server(
    fake_tmux: FakeTmux, tmp_repo: Path, tmp_path: Path
) -> None:
    """A live pre-isolation workspace must never be moved: moving kills its agent."""
    manager, store = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="legacy"))
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    rows = raw["workspaces"] if isinstance(raw, dict) and "workspaces" in raw else raw
    row = rows[state.id] if isinstance(rows, dict) else next(r for r in rows if r["id"] == state.id)
    del row["tmux_socket"]
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    store.invalidate()
    # The live session sits where a pre-isolation daemon put it.
    fake_tmux.session_servers[state.tmux_session] = DEFAULT_SERVER

    (legacy,) = manager.list()
    assert legacy.tmux_socket == ""
    pane = manager.pane_for(legacy)
    assert pane is not None and pane.command == tmux.DEFAULT_TMUX_COMMAND
