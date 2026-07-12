"""Onboards external coding-agent CLIs (Claude Code, Codex) onto Grove.

Two side effects, mirroring `grove/core/git.py` / `grove/core/tmux.py`: drop
the bundled `using-grove` skill into a tool's skills directory, and register
Grove's own MCP server via that tool's *native* `mcp add` command — never a
hand-rolled `.mcp.json` / `config.toml` write. `ClaudeTool` / `CodexTool` are
the two real implementations of `AgentTool`; path resolution reuses the
existing config-dir cascades (`_ClaudeHome.config_dirs`, `_CodexHome.base_dir`)
so a relocated `CLAUDE_CONFIG_DIR`/`CODEX_HOME` is honoured for free.
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.codex import _CodexHome
from grove.core.errors import OnboardError

OnboardTarget = Literal["user", "project"]
AgentToolName = Literal["claude", "codex"]
OnboardAction = Literal["skill", "mcp"]
OnboardStatus = Literal["ok", "skipped", "unsupported"]

SKILL_NAME = "using-grove"
MCP_SERVER_NAME = "grove"
MCP_SERVER_COMMAND = "grove-mcp"


class AgentTool(Protocol):
    """One external coding-agent CLI Grove can onboard.

    Two real implementations (`ClaudeTool`, `CodexTool`) — a Protocol is
    warranted per the provider-boundary rule: this normalizes each tool's
    *shape* (where its skills live, how its `mcp add` is invoked), never its
    behavior.
    """

    name: ClassVar[AgentToolName]
    binary: ClassVar[str]

    @staticmethod
    def installed() -> bool:
        """Whether this tool's CLI binary is reachable on PATH."""
        ...

    @staticmethod
    def user_skills_dir() -> Path | None:
        """The host's user-scope skills dir, or ``None`` if this tool has
        never been used on this host (its config dir doesn't exist) — the
        "if they exist" gate for user-scope skill installation."""
        ...

    @staticmethod
    def project_skills_dir(repo_root: Path) -> Path:
        """The project-scope skills dir for ``repo_root``."""
        ...

    @staticmethod
    def mcp_add_argv(*, target: OnboardTarget, name: str, command: str) -> list[str] | None:
        """The argv for this tool's native MCP-registration command, or
        ``None`` if the tool's CLI has no support for ``target``."""
        ...


class ClaudeTool:
    """Claude Code: ``claude mcp add --scope <user|project> ...``."""

    name: ClassVar[AgentToolName] = "claude"
    binary: ClassVar[str] = "claude"

    @staticmethod
    def installed() -> bool:
        return shutil.which(ClaudeTool.binary) is not None

    @staticmethod
    def user_skills_dir() -> Path | None:
        for base in _ClaudeHome.config_dirs():
            if base.exists():
                return base / "skills"
        return None

    @staticmethod
    def project_skills_dir(repo_root: Path) -> Path:
        return repo_root / ".claude" / "skills"

    @staticmethod
    def mcp_add_argv(*, target: OnboardTarget, name: str, command: str) -> list[str] | None:
        # `--scope` accepts exactly "user"/"project"/"local"; Grove exposes
        # the first two (verified against code.claude.com/docs/en/mcp).
        return [
            "claude",
            "mcp",
            "add",
            "--scope",
            target,
            "--transport",
            "stdio",
            name,
            "--",
            command,
        ]


class CodexTool:
    """Codex CLI: ``codex mcp add`` — user scope only (no project flag)."""

    name: ClassVar[AgentToolName] = "codex"
    binary: ClassVar[str] = "codex"

    @staticmethod
    def installed() -> bool:
        return shutil.which(CodexTool.binary) is not None

    @staticmethod
    def user_skills_dir() -> Path | None:
        base = _CodexHome.base_dir()
        return base / "skills" if base.exists() else None

    @staticmethod
    def project_skills_dir(repo_root: Path) -> Path:
        return repo_root / ".codex" / "skills"

    @staticmethod
    def mcp_add_argv(*, target: OnboardTarget, name: str, command: str) -> list[str] | None:
        if target == "project":
            # `codex mcp add` always writes $CODEX_HOME/config.toml — verified
            # against developers.openai.com/codex/cli/reference (2026-07):
            # no --cd/-C/project flag exists on this subcommand.
            return None
        return ["codex", "mcp", "add", name, "--", command]


TOOLS: dict[AgentToolName, type[AgentTool]] = {"claude": ClaudeTool, "codex": CodexTool}


@dataclass(frozen=True, slots=True)
class OnboardOutcome:
    """One (tool, target, action) result — the atomic unit every caller renders."""

    tool: AgentToolName
    target: OnboardTarget
    action: OnboardAction
    status: OnboardStatus
    detail: str


def _bundled_skill_dir() -> Path:
    """The packaged `using-grove` skill source, shipped as package data."""
    return Path(str(importlib.resources.files("grove") / "skills" / SKILL_NAME))


def install_skill(
    tool: type[AgentTool], target: OnboardTarget, *, repo_root: Path | None = None
) -> OnboardOutcome:
    """Copy the bundled skill into ``tool``'s skills dir for ``target``."""
    if target == "user":
        base = tool.user_skills_dir()
        if base is None:
            detail = f"{tool.name} not detected on this host"
            return OnboardOutcome(tool.name, target, "skill", "skipped", detail)
    else:
        if repo_root is None:
            raise ValueError("repo_root is required for target='project'")
        base = tool.project_skills_dir(repo_root)

    dest = base / SKILL_NAME
    dest.mkdir(parents=True, exist_ok=True)
    for item in _bundled_skill_dir().iterdir():
        if item.is_file():
            shutil.copy2(item, dest / item.name)
    return OnboardOutcome(tool.name, target, "skill", "ok", str(dest))


def register_mcp(
    tool: type[AgentTool], target: OnboardTarget, *, repo_root: Path | None = None
) -> OnboardOutcome:
    """Register Grove as an MCP server via ``tool``'s own ``mcp add``."""
    if not tool.installed():
        detail = f"{tool.name} CLI not found on PATH"
        return OnboardOutcome(tool.name, target, "mcp", "skipped", detail)

    argv = tool.mcp_add_argv(target=target, name=MCP_SERVER_NAME, command=MCP_SERVER_COMMAND)
    if argv is None:
        detail = f"{tool.name} mcp add has no {target} scope"
        return OnboardOutcome(tool.name, target, "mcp", "unsupported", detail)

    if target == "project" and repo_root is None:
        raise ValueError("repo_root is required for target='project'")
    cwd = repo_root if target == "project" else None

    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise OnboardError(f"{' '.join(argv)} failed: {detail}")
    return OnboardOutcome(tool.name, target, "mcp", "ok", result.stdout.strip() or "registered")


__all__ = [
    "MCP_SERVER_COMMAND",
    "MCP_SERVER_NAME",
    "SKILL_NAME",
    "TOOLS",
    "AgentTool",
    "AgentToolName",
    "ClaudeTool",
    "CodexTool",
    "OnboardOutcome",
    "OnboardTarget",
    "install_skill",
    "register_mcp",
]
