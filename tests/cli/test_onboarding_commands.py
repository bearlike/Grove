"""``grove skills install`` / ``grove mcp install`` / ``grove config init --with-onboarding``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.tui.cli import app

CLAUDE_PROJECT_MCP_ADD_ARGV = [
    "claude",
    "mcp",
    "add",
    "--scope",
    "project",
    "--transport",
    "stdio",
    "grove",
    "--",
    "grove-mcp",
]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_repo: Path) -> Path:
    """cwd inside a real repo; Grove state + Claude/Codex homes all sandboxed
    under tmp_path so nothing here can touch the real host."""
    del tmp_state_dir
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(Path, "home", lambda: tmp_repo.parent)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return tmp_repo


@pytest.fixture
def fake_mcp_add(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub `claude`/`codex mcp add` subprocess calls; records argv.

    `subprocess` is one shared module object across the whole process, so
    patching `<module>.subprocess.run` affects every module that does `import
    subprocess` — including `grove.core.git`'s own `detect_root` calls this
    same CLI invocation makes. Pass anything that isn't a `claude`/`codex`
    invocation through to the real `subprocess.run` rather than faking git too.
    """
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[0] not in {"claude", "codex"}:
            return real_run(argv, **kwargs)  # type: ignore[call-overload]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "registered\n", "")

    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr("grove.core.agents.onboarding.subprocess.run", fake_run)
    return calls


# ─── grove skills install ────────────────────────────────────────────────────


def test_skills_install_project_claude_only(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["skills", "install", "--target", "project", "--agent", "claude"])

    assert result.exit_code == 0, result.output
    assert (project / ".claude" / "skills" / "using-grove" / "SKILL.md").exists()
    assert not (project / ".codex").exists()


def test_skills_install_target_all_skips_undetected_user_scope(
    runner: CliRunner, project: Path
) -> None:
    result = runner.invoke(app, ["skills", "install", "--target", "all", "--agent", "all"])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output  # user scope: neither tool detected in the sandboxed home
    assert (project / ".claude" / "skills" / "using-grove" / "SKILL.md").exists()
    assert (project / ".codex" / "skills" / "using-grove" / "SKILL.md").exists()


def test_skills_install_target_project_outside_repo_fails_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_path: Path
) -> None:
    del tmp_state_dir
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)

    result = runner.invoke(app, ["skills", "install", "--target", "project"])

    assert result.exit_code == 1
    assert "not in a git repository" in result.output


def test_skills_install_prompts_when_target_omitted(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["skills", "install", "--agent", "claude"], input="y\nn\n")

    assert result.exit_code == 0, result.output
    dest = project / ".claude" / "skills" / "using-grove" / "SKILL.md"
    assert not dest.exists()  # answered 'y' to user (skipped: not detected), 'n' to project


# ─── grove mcp install ────────────────────────────────────────────────────────


def test_mcp_install_project_scope_invokes_claude_mcp_add(
    runner: CliRunner, project: Path, fake_mcp_add: list[list[str]]
) -> None:
    del project
    result = runner.invoke(app, ["mcp", "install", "--target", "project", "--agent", "claude"])

    assert result.exit_code == 0, result.output
    assert fake_mcp_add == [CLAUDE_PROJECT_MCP_ADD_ARGV]


def test_mcp_install_codex_project_scope_is_unsupported(
    runner: CliRunner, project: Path, fake_mcp_add: list[list[str]]
) -> None:
    del project
    result = runner.invoke(app, ["mcp", "install", "--target", "project", "--agent", "codex"])

    assert result.exit_code == 0, result.output
    assert "unsupported" in result.output
    assert fake_mcp_add == []


# ─── grove config init --with-onboarding ─────────────────────────────────────


def test_config_init_with_onboarding_runs_skill_and_mcp_for_project(
    runner: CliRunner, project: Path, fake_mcp_add: list[list[str]]
) -> None:
    result = runner.invoke(app, ["config", "init", "--with-onboarding"])

    assert result.exit_code == 0, result.output
    assert (project / ".claude" / "skills" / "using-grove" / "SKILL.md").exists()
    assert (project / ".codex" / "skills" / "using-grove" / "SKILL.md").exists()
    assert CLAUDE_PROJECT_MCP_ADD_ARGV in fake_mcp_add


def test_config_init_without_flag_does_not_touch_skills_or_mcp(
    runner: CliRunner, project: Path, fake_mcp_add: list[list[str]]
) -> None:
    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0, result.output
    assert not (project / ".claude").exists()
    assert fake_mcp_add == []
