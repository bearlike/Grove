"""``grove web`` — serve the dashboard bundled in the wheel on the user's Node.

A source checkout has no bundle, and a user may have no Node or an old one;
each of those must end in a sentence naming the fix rather than an exec of a
missing file. The exec seam is patched, so no Node process ever starts here.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.tui import cli_web
from grove.tui.cli import app
from grove.tui.cli_web import WebappLauncher


def _fake_node(tmp_path: Path, major: str) -> Path:
    """An executable that answers the version probe the way Node would."""
    node = tmp_path / "node"
    node.write_text(f"#!/bin/sh\necho {major}\n")
    node.chmod(node.stat().st_mode | stat.S_IEXEC)
    return node


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "_webapp"
    bundle.mkdir()
    (bundle / "server.js").write_text("")
    return bundle


def test_missing_bundle_names_how_to_get_one(tmp_path: Path) -> None:
    launcher = WebappLauncher(bundle_dir=tmp_path / "absent")
    with pytest.raises(cli_web.GroveError, match="make webapp-bundle"):
        launcher.server_script()


@pytest.mark.parametrize(("major", "accepted"), [("21", False), ("22", True), ("24", True)])
def test_node_floor_is_22(tmp_path: Path, major: str, accepted: bool) -> None:
    launcher = WebappLauncher(node=str(_fake_node(tmp_path, major)))
    if accepted:
        assert launcher.node_binary() == launcher.node
    else:
        with pytest.raises(cli_web.GroveError, match=f"version {major}"):
            launcher.node_binary()


def test_the_bundled_node_wins_over_the_one_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = _fake_node(tmp_path, "24")
    monkeypatch.setattr(cli_web, "bundled_node", lambda: str(bundled))
    monkeypatch.setattr(cli_web.shutil, "which", lambda _name: "/usr/bin/node-from-path")
    assert WebappLauncher().node_binary() == str(bundled)


def test_path_node_is_only_the_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    on_path = _fake_node(tmp_path, "22")
    monkeypatch.setattr(cli_web, "bundled_node", lambda: None)
    monkeypatch.setattr(cli_web.shutil, "which", lambda _name: str(on_path))
    assert WebappLauncher().node_binary() == str(on_path)


def test_no_node_anywhere_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_web, "bundled_node", lambda: None)
    monkeypatch.setattr(cli_web.shutil, "which", lambda _name: None)
    with pytest.raises(cli_web.GroveError, match="nodejs-wheel"):
        WebappLauncher().node_binary()


def test_bundled_node_is_the_real_binary_not_the_python_launcher() -> None:
    node = cli_web.bundled_node()
    assert node is not None, "nodejs-wheel is a base dependency"
    with open(node, "rb") as binary:
        assert binary.read(2) != b"#!", f"{node} is a script, not the Node binary"
    assert WebappLauncher.node_major(node) is not None


def test_check_reports_without_serving(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    node = _fake_node(tmp_path, "24")
    monkeypatch.setattr(cli_web, "WebappLauncher", lambda: WebappLauncher(bundle, str(node)))
    monkeypatch.setattr(os, "execvpe", lambda *_: pytest.fail("--check must not serve"))
    result = CliRunner().invoke(app, ["web", "--check"])
    assert result.exit_code == 0, result.output
    assert str(bundle) in result.output
    assert str(node) in result.output


def test_web_execs_the_bundled_server_with_host_port_and_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _bundle(tmp_path)
    node = _fake_node(tmp_path, "22")
    monkeypatch.setattr(cli_web, "WebappLauncher", lambda: WebappLauncher(bundle, str(node)))
    seen: dict[str, object] = {}

    def _exec(path: str, argv: list[str], env: dict[str, str]) -> None:
        seen.update(path=path, argv=argv, env=env)

    monkeypatch.setattr(os, "execvpe", _exec)
    result = CliRunner().invoke(
        app, ["web", "--host", "0.0.0.0", "--port", "3100", "--daemon-url", "http://d:1"]
    )

    assert result.exit_code == 0, result.output
    assert seen["argv"] == [str(node), str(bundle / "server.js")]
    env = seen["env"]
    assert isinstance(env, dict)
    assert (env["HOSTNAME"], env["PORT"], env["GROVE_DAEMON_URL"]) == (
        "0.0.0.0",
        "3100",
        "http://d:1",
    )


def test_web_without_a_bundle_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_web, "WebappLauncher", lambda: WebappLauncher(tmp_path / "absent"))
    monkeypatch.setattr(os, "execvpe", lambda *_: pytest.fail("must not exec without a bundle"))
    result = CliRunner().invoke(app, ["web"])
    assert result.exit_code != 0
    assert "make webapp-bundle" in result.output
