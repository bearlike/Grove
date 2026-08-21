"""``grove config add-project`` — register the current repo from the shell."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import paths
from grove.tui.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_repo: Path) -> Path:
    del tmp_state_dir
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def test_cli_resolves_user_scope_tls_before_every_subcommand(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path
) -> None:
    del tmp_state_dir
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text(
        json.dumps({"tls": {"ca_path": "/deployment/ca.pem"}}), encoding="utf-8"
    )
    received: list[str] = []
    monkeypatch.setattr(
        "grove.tui.cli.use_system_trust_store", lambda path: received.append(path) or None
    )

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0, result.output
    assert received == ["/deployment/ca.pem"]


def test_add_project_registers_cwd_repo(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["config", "add-project"])

    assert result.exit_code == 0, result.output
    assert "added" in result.output
    data = json.loads(paths.user_config_path().read_text(encoding="utf-8"))
    assert data["projects"] == [str(project)]


def test_add_project_by_explicit_path(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["config", "add-project", str(project)])
    assert result.exit_code == 0, result.output


def test_add_project_second_call_is_a_no_op_message(runner: CliRunner, project: Path) -> None:
    runner.invoke(app, ["config", "add-project"])
    result = runner.invoke(app, ["config", "add-project"])

    assert result.exit_code == 0, result.output
    assert "already known" in result.output
    data = json.loads(paths.user_config_path().read_text(encoding="utf-8"))
    assert data["projects"] == [str(project)]


def test_add_project_outside_git_repo_fails_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_path: Path
) -> None:
    del tmp_state_dir
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)

    result = runner.invoke(app, ["config", "add-project"])

    assert result.exit_code == 1
    assert "not in a git repository" in result.output
