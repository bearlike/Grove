"""``grove doctor`` — the CLI rendering + exit-code layer over `HostPreflight`.

In-process via CliRunner; every subprocess/`which` call is stubbed at the
`grove.core.preflight` boundary — no docker, tmux, git, or network needed.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from grove.core import preflight as pf
from grove.core.config import GroveConfig
from grove.core.devcontainer import DevcontainerCli, DevcontainerProbe
from grove.tui.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _default_container_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the config doctor reads, instead of whatever this host has on disk.

    Containers are the DEFAULT runtime, so the devcontainer checks are required
    unless a test explicitly asks for host mode via `_host_mode`.
    """
    _configure(monkeypatch, container_enabled=True)


def _configure(monkeypatch: pytest.MonkeyPatch, *, container_enabled: bool) -> None:
    cfg = GroveConfig.model_validate({"container": {"enabled": container_enabled}})
    monkeypatch.setattr("grove.tui.cli_doctor.load_config", lambda *, repo_root: cfg)


def _host_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, container_enabled=False)


def _which_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pf.shutil, "which", lambda binary: f"/usr/bin/{binary}")


def _which_none_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pf.shutil, "which", lambda binary: None)


def _ok_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pf.HostPreflight,
        "_run",
        staticmethod(
            lambda argv: subprocess.CompletedProcess(
                args=argv, returncode=0, stdout='{"ServerVersion": "27.0.0"}', stderr=""
            )
        ),
    )


def _devcontainer_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        DevcontainerCli,
        "probe",
        lambda self: DevcontainerProbe(binary="devcontainer", available=False),
    )


def test_doctor_host_mode_exits_zero_even_with_devcontainer_cli_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace configured for host mode needs no container runtime.

    The check is still RENDERED — doctor reports the whole host, and knowing the
    CLI is missing is useful before you flip `container.enabled` — it just does
    not gate the exit code for a runtime this config never selects.
    """
    _host_mode(monkeypatch)
    _which_all_present(monkeypatch)
    _ok_run(monkeypatch)
    _devcontainer_absent(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "@devcontainers/cli" in result.output
    assert "FAIL" in result.output  # rendered even though it doesn't block exit
    assert "all required checks passed" in result.output


def test_doctor_fails_by_default_when_the_devcontainer_cli_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containers are the default runtime, so its checks gate the exit code."""
    _which_all_present(monkeypatch)
    _ok_run(monkeypatch)
    _devcontainer_absent(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "one or more required checks failed" in result.output


def test_doctor_host_mode_fails_when_tmux_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_mode(monkeypatch)
    monkeypatch.setattr(
        pf.shutil, "which", lambda binary: None if binary == "tmux" else f"/usr/bin/{binary}"
    )
    _ok_run(monkeypatch)
    _devcontainer_absent(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "one or more required checks failed" in result.output


def test_doctor_everything_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _which_none_present(monkeypatch)
    monkeypatch.setattr(
        pf.HostPreflight,
        "_run",
        staticmethod(lambda argv: None),
    )
    _devcontainer_absent(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "git" in result.output
    assert "tmux" in result.output


def test_doctor_json_output_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _host_mode(monkeypatch)
    _which_all_present(monkeypatch)
    _ok_run(monkeypatch)
    _devcontainer_absent(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    names = {c["name"] for c in payload["checks"]}
    assert {"docker daemon", "compose v2", "@devcontainers/cli", "git", "tmux"} <= names
    devcontainer_check = next(c for c in payload["checks"] if c["name"] == "@devcontainers/cli")
    assert devcontainer_check["ok"] is False
    assert devcontainer_check["hint"]


def test_an_unbuilt_tmux_payload_is_reported_as_a_warning_and_never_fails_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``optional`` scope, as a user sees it.

    A red FAIL directly above "all required checks passed" would teach an
    operator to distrust both lines, so the row renders as a warning — and the
    exit code has to stay 0, which is the half that actually matters.
    """
    _which_all_present(monkeypatch)
    _ok_run(monkeypatch)
    # Every REQUIRED check has to pass, or the exit code would be 1 for a
    # reason that has nothing to do with the row under test.
    monkeypatch.setattr(
        DevcontainerCli,
        "probe",
        lambda self: DevcontainerProbe(binary="devcontainer", available=True, version="0.88.0"),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "in-container tmux" in result.output
    assert "warn" in result.output
    assert "all required checks passed" in result.output
