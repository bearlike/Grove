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

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "packaging" / "systemd"


@pytest.fixture(autouse=True)
def _require_make() -> None:
    if shutil.which("make") is None:
        pytest.skip("make not on PATH")


def _run_print(*, with_webapp: bool, env_overrides: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    if with_webapp:
        env["WITH_WEBAPP"] = "1"
    # Pin deterministic values regardless of host PATH so tests don't
    # depend on whether a real grove / npm is installed in CI.
    env.setdefault("GROVE_BIN", "/usr/local/bin/grove")
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
    """The two *.service.in templates ship with the repo."""
    assert (TEMPLATE_DIR / "grove-daemon.service.in").is_file()
    assert (TEMPLATE_DIR / "grove-webapp.service.in").is_file()


def test_default_renders_daemon_only() -> None:
    """Without WITH_WEBAPP the print target emits only the daemon unit."""
    out = _run_print(with_webapp=False)
    assert "─── grove-daemon.service ───" in out
    assert "─── grove-webapp.service ───" not in out


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


def test_no_unsubstituted_placeholders_remain() -> None:
    """No @TOKEN@ should survive in either rendered unit."""
    out = _run_print(with_webapp=True)
    assert "@" not in out.replace("https://github.com/bearlike/Grove", ""), out
