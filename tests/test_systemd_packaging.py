"""systemd unit templates and the Makefile recipes that render them.

The Makefile recipes substitute ``@PLACEHOLDER@`` tokens via ``sed``;
this test pins the rendered output so future template / Makefile drift
fails loudly. Uses ``make systemd-print`` (the dry-run target) so we
never write to ``~/.config/systemd/user`` from the test suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from grove.mcp.server import McpServerConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "packaging" / "systemd"


@pytest.fixture(autouse=True)
def _require_make() -> None:
    if shutil.which("make") is None:
        pytest.skip("make not on PATH")


def _run_print(
    *,
    with_webapp: bool,
    with_mcp: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> str:
    env = os.environ.copy()
    if with_webapp:
        env["WITH_WEBAPP"] = "1"
    if with_mcp:
        env["WITH_MCP"] = "1"
    # Pin deterministic values regardless of host PATH so tests don't
    # depend on whether a real grove / grove-mcp / npm is installed in CI.
    env.setdefault("GROVE_BIN", "/usr/local/bin/grove")
    env.setdefault("MCP_BIN", "/usr/local/bin/grove-mcp")
    env.setdefault("NPM_BIN", "/usr/local/bin/npm")
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["make", "-s", "systemd-print"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"make exited {result.returncode}: {result.stderr}"
    return result.stdout


def test_templates_exist() -> None:
    """Every *.service.in template ships with the repo."""
    assert (TEMPLATE_DIR / "grove-daemon.service.in").is_file()
    assert (TEMPLATE_DIR / "grove-webapp.service.in").is_file()
    assert (TEMPLATE_DIR / "grove-mcp.service.in").is_file()


def test_default_renders_daemon_only() -> None:
    """Without a companion gate the print target emits only the daemon unit."""
    out = _run_print(with_webapp=False)
    assert "─── grove-daemon.service ───" in out
    assert "─── grove-webapp.service ───" not in out
    assert "─── grove-mcp.service ───" not in out


def test_daemon_unit_substitutes_grove_bin_and_port() -> None:
    """ExecStart and the port placeholders are filled from env overrides."""
    out = _run_print(
        with_webapp=False,
        env_overrides={"GROVE_BIN": "/opt/grove/bin/grove", "DAEMON_PORT": "7777"},
    )
    assert "ExecStart=/opt/grove/bin/grove daemon serve --host 127.0.0.1 --port 7777" in out
    assert "@GROVE_BIN@" not in out
    assert "@DAEMON_PORT@" not in out


def test_with_webapp_renders_both_units_and_wires_dependency() -> None:
    """WITH_WEBAPP=1 adds the webapp unit and links it to the daemon."""
    out = _run_print(with_webapp=True)
    assert "─── grove-daemon.service ───" in out
    assert "─── grove-webapp.service ───" in out
    # Webapp depends on the daemon via Wants= (not Requires=) — the rule
    # is documented in packaging/systemd/README.md and tested here so
    # nobody silently tightens it to Requires= and turns daemon hiccups
    # into webapp outages.
    assert "Wants=grove-daemon.service" in out
    assert "Requires=grove-daemon.service" not in out


def test_webapp_unit_passes_daemon_url_via_env() -> None:
    """The webapp ExecStart inherits GROVE_DAEMON_URL pointing at the daemon port."""
    out = _run_print(
        with_webapp=True,
        env_overrides={"DAEMON_PORT": "7421", "WEBAPP_PORT": "3030"},
    )
    assert "Environment=GROVE_DAEMON_URL=http://127.0.0.1:7421" in out
    # Webapp listens on its own port, baked into the ExecStart line.
    assert "--port 3030" in out


def test_webapp_unit_default_host_is_lan_reachable() -> None:
    """Default WEBAPP_HOST is 0.0.0.0 (LAN-reachable, the whole point of the webapp)."""
    out = _run_print(with_webapp=True)
    assert "--hostname 0.0.0.0" in out


def test_daemon_unit_bakes_install_time_path() -> None:
    """The daemon runs user-authored init scripts; under systemd --user a bare
    PATH makes pyenv/nvm/asdf toolchains invisible and rolls creates back. The
    unit must bake DAEMON_PATH (default: the installing shell's PATH) into
    Environment=PATH=.
    """
    out = _run_print(
        with_webapp=False,
        env_overrides={"DAEMON_PATH": "/opt/toolchain/bin:/usr/bin"},
    )
    assert "Environment=PATH=/opt/toolchain/bin:/usr/bin" in out
    assert "@DAEMON_PATH@" not in out


def test_daemon_unit_uses_killmode_process() -> None:
    """The daemon forks the shared tmux server into its cgroup, so the unit must
    set KillMode=process — the systemd default (control-group) tears the server
    and every session down on each `systemctl restart` (= every update). Not
    `mixed`: its final SIGKILL still hits the cgroup.
    """
    out = _run_print(with_webapp=False)
    # Assert on active directive lines only — the WHY comment names the
    # control-group default it replaces, so a raw substring scan of the whole
    # unit would false-positive on the prose.
    directives = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("KillMode=")]
    assert directives == ["KillMode=process"]


def test_with_mcp_renders_mcp_unit_and_wires_dependency() -> None:
    """WITH_MCP=1 adds the MCP unit, gated the same way the webapp is."""
    out = _run_print(with_webapp=False, with_mcp=True)
    assert "─── grove-daemon.service ───" in out
    assert "─── grove-mcp.service ───" in out
    assert "─── grove-webapp.service ───" not in out
    # Same `Wants=` (not `Requires=`) rule as the webapp: the MCP server is a
    # daemon client, and a daemon blip must not tear the service down.
    assert "Wants=grove-daemon.service" in out
    assert "Requires=grove-daemon.service" not in out


def test_mcp_unit_execs_the_grove_mcp_script_over_streamable_http() -> None:
    """`grove-mcp` is its own console script, not a `grove` subcommand, so it
    renders from MCP_BIN rather than GROVE_BIN — and the network transport is
    baked in (stdio needs no unit; the client spawns it per connection).
    """
    out = _run_print(
        with_webapp=False,
        with_mcp=True,
        env_overrides={"MCP_BIN": "/opt/grove/bin/grove-mcp", "MCP_PORT": "7500"},
    )
    assert (
        "ExecStart=/opt/grove/bin/grove-mcp --transport streamable-http "
        "--host 127.0.0.1 --port 7500" in out
    )


def test_mcp_unit_default_host_is_loopback() -> None:
    """Default MCP_HOST is 127.0.0.1 — deliberately unlike WEBAPP_HOST=0.0.0.0.
    The webapp is read-only; the MCP surface can create/kill/message workspaces,
    so widening the bind must stay an explicit operator decision.
    """
    out = _run_print(with_webapp=False, with_mcp=True)
    assert "--host 127.0.0.1 --port 7431" in out
    assert "--host 0.0.0.0" not in out


def test_mcp_unit_sources_the_token_environment_file() -> None:
    """The inbound bearer (GROVE_MCP_TOKEN) arrives via an optional
    EnvironmentFile. The leading `-` is load-bearing: a missing file must not be
    a unit load error, so an absent token surfaces as the server's own
    fail-closed exit in the journal instead.
    """
    out = _run_print(with_webapp=False, with_mcp=True)
    assert "EnvironmentFile=-%h/.config/grove/mcp.env" in out


def test_mcp_unit_does_not_bake_a_path() -> None:
    """Only the daemon needs the install-time PATH bake — it runs user-authored
    init scripts. The MCP server shells out to nothing, so a PATH line here
    would be cargo-culted surface that silently goes stale.
    """
    out = _run_print(with_webapp=False, with_mcp=True)
    mcp_unit = out.split("─── grove-mcp.service ───", 1)[1]
    assert not [ln for ln in mcp_unit.splitlines() if ln.strip().startswith("Environment=PATH=")]


def test_mcp_unit_defaults_track_the_cli_defaults() -> None:
    """The Makefile's MCP_HOST/MCP_PORT and `McpServerConfig`'s defaults are the
    same policy expressed in two languages — a Makefile cannot import Python, so
    nothing but this test stops them drifting apart.

    Drift here is quiet and nasty: the unit would serve on one port while every
    doc, `--help` string, and client config still named the other.
    """
    out = _run_print(with_webapp=False, with_mcp=True)
    expected = f"--host {McpServerConfig.DEFAULT_BIND_HOST} --port {McpServerConfig.DEFAULT_PORT}"
    assert expected in out, f"unit does not carry the CLI defaults ({expected})"


def test_no_unsubstituted_placeholders_remain() -> None:
    """No @TOKEN@ should survive in any rendered unit."""
    out = _run_print(with_webapp=True, with_mcp=True)
    assert "@" not in out.replace("https://github.com/bearlike/Grove", ""), out
