"""`core/agents/onboarding.py` — skill install + native `mcp add` registration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.agents.onboarding import (
    TOOLS,
    ClaudeTool,
    CodexTool,
    OnboardOutcome,
    install_skill,
    register_mcp,
)
from grove.core.errors import OnboardError


@pytest.fixture
def sandboxed_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Neither Claude nor Codex's config dir exists yet — the "not detected" case."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return tmp_path


# ─── tool registry ───────────────────────────────────────────────────────────


def test_tools_registry_has_claude_and_codex() -> None:
    assert set(TOOLS) == {"claude", "codex"}
    assert TOOLS["claude"] is ClaudeTool
    assert TOOLS["codex"] is CodexTool


# ─── path resolution ("if they exist") ───────────────────────────────────────


def test_claude_user_skills_dir_is_none_when_not_detected(sandboxed_home: Path) -> None:
    assert ClaudeTool.user_skills_dir() is None


def test_claude_user_skills_dir_resolves_when_config_dir_exists(sandboxed_home: Path) -> None:
    (sandboxed_home / ".claude").mkdir()
    assert ClaudeTool.user_skills_dir() == sandboxed_home / ".claude" / "skills"


def test_codex_user_skills_dir_is_none_when_not_detected(sandboxed_home: Path) -> None:
    assert CodexTool.user_skills_dir() is None


def test_codex_user_skills_dir_resolves_when_home_exists(sandboxed_home: Path) -> None:
    (sandboxed_home / ".codex").mkdir()
    assert CodexTool.user_skills_dir() == sandboxed_home / ".codex" / "skills"


def test_project_skills_dirs_are_unconditional(tmp_path: Path) -> None:
    assert ClaudeTool.project_skills_dir(tmp_path) == tmp_path / ".claude" / "skills"
    assert CodexTool.project_skills_dir(tmp_path) == tmp_path / ".codex" / "skills"


# ─── install_skill ────────────────────────────────────────────────────────────


def test_install_skill_user_scope_skips_when_not_detected(sandboxed_home: Path) -> None:
    outcome = install_skill(ClaudeTool, "user")
    assert outcome.status == "skipped"
    assert not (sandboxed_home / ".claude").exists()


def test_install_skill_user_scope_copies_when_detected(sandboxed_home: Path) -> None:
    (sandboxed_home / ".claude").mkdir()

    outcome = install_skill(ClaudeTool, "user")

    assert outcome.status == "ok"
    dest = sandboxed_home / ".claude" / "skills" / "using-grove" / "SKILL.md"
    assert dest.exists()
    assert "name: using-grove" in dest.read_text(encoding="utf-8")


def test_install_skill_project_scope_always_creates(tmp_path: Path) -> None:
    outcome = install_skill(CodexTool, "project", repo_root=tmp_path)

    assert outcome.status == "ok"
    dest = tmp_path / ".codex" / "skills" / "using-grove" / "SKILL.md"
    assert dest.exists()


def test_install_skill_project_scope_requires_repo_root() -> None:
    with pytest.raises(ValueError, match="repo_root"):
        install_skill(ClaudeTool, "project")


# ─── register_mcp ─────────────────────────────────────────────────────────────


def test_register_mcp_skips_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: None)
    outcome = register_mcp(ClaudeTool, "user")
    assert outcome.status == "skipped"


def test_register_mcp_codex_project_is_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/codex")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "grove.core.agents.onboarding.subprocess.run",
        lambda argv, **_kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    outcome = register_mcp(CodexTool, "project", repo_root=tmp_path)

    assert outcome.status == "unsupported"
    assert calls == []  # never shells out for an unsupported scope


def test_register_mcp_success_runs_expected_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/claude")
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(argv, 0, "registered\n", "")

    monkeypatch.setattr("grove.core.agents.onboarding.subprocess.run", fake_run)

    outcome = register_mcp(ClaudeTool, "project", repo_root=tmp_path)

    assert outcome == OnboardOutcome("claude", "project", "mcp", "ok", "registered")
    assert captured["argv"] == [
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
    assert captured["cwd"] == tmp_path


def test_register_mcp_user_scope_runs_with_no_cwd_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/claude")
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("grove.core.agents.onboarding.subprocess.run", fake_run)

    register_mcp(ClaudeTool, "user")

    assert captured["cwd"] is None


def test_register_mcp_raises_onboard_error_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/claude")
    monkeypatch.setattr(
        "grove.core.agents.onboarding.subprocess.run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 1, "", "already exists"),
    )

    with pytest.raises(OnboardError, match="already exists"):
        register_mcp(ClaudeTool, "user")


def test_register_mcp_project_scope_requires_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/claude")
    with pytest.raises(ValueError, match="repo_root"):
        register_mcp(ClaudeTool, "project")
