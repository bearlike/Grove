"""Onboards external coding-agent CLIs (Claude Code, Codex) onto Grove.

Two side effects, mirroring `grove/core/git.py` / `grove/core/tmux.py`: drop
Grove's bundled skills into a tool's skills directory, and register
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
from typing import ClassVar, Final, Literal, Protocol

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.codex import _CodexHome
from grove.core.errors import OnboardError

OnboardTarget = Literal["user", "project"]
AgentToolName = Literal["claude", "codex"]
OnboardAction = Literal["skill", "mcp"]
OnboardStatus = Literal["ok", "skipped", "unsupported"]

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


class BundledSkills:
    """The skills Grove ships as package data, and how they reach a tool.

    Which skills exist is read off the packaged directory rather than listed in
    code, so adding one is a new directory and no edit here. A hard-coded roster
    would be policy in code with nothing to gain, since the packaged tree already
    IS the roster. Do not restate the roster in prose either, here or in a
    docstring the site publishes: a count written down is a count that goes stale
    the next time a skill lands, and nothing fails when it does.

    All-classmethod: the state is the wheel's own layout, not anything per
    instance.
    """

    PACKAGE_DIR: Final = "skills"

    @classmethod
    def root(cls) -> Path:
        """The packaged skills directory inside the installed `grove` package."""
        return Path(str(importlib.resources.files("grove") / cls.PACKAGE_DIR))

    @classmethod
    def dirs(cls) -> tuple[Path, ...]:
        """Every packaged skill source directory, name-sorted for stable output."""
        return tuple(sorted((d for d in cls.root().iterdir() if d.is_dir()), key=lambda d: d.name))

    @classmethod
    def install_into(cls, base: Path) -> tuple[str, ...]:
        """Copy every bundled skill into ``base``, returning the names installed.

        Copies files only, never nested directories: a skill is a `SKILL.md` plus
        flat siblings, and a recursive copy would also carry `__pycache__` and
        friends into somebody else's config directory.
        """
        installed: list[str] = []
        for source in cls.dirs():
            dest = base / source.name
            dest.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest / item.name)
            installed.append(source.name)
        return tuple(installed)


def install_skill(
    tool: type[AgentTool], target: OnboardTarget, *, repo_root: Path | None = None
) -> OnboardOutcome:
    """Copy every bundled skill into ``tool``'s skills dir for ``target``.

    Stays ONE outcome across N skills. The outcome is what a caller renders per
    (tool, target, action), and splitting it per skill would turn one line of
    `grove skills install` output into a row per skill per tool per target for a
    result the user never acts on separately.
    """
    if target == "user":
        base = tool.user_skills_dir()
        if base is None:
            detail = f"{tool.name} not detected on this host"
            return OnboardOutcome(tool.name, target, "skill", "skipped", detail)
    else:
        if repo_root is None:
            raise ValueError("repo_root is required for target='project'")
        base = tool.project_skills_dir(repo_root)

    installed = BundledSkills.install_into(base)
    return OnboardOutcome(tool.name, target, "skill", "ok", f"{base} ({', '.join(installed)})")


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
    "TOOLS",
    "AgentTool",
    "AgentToolName",
    "BundledSkills",
    "ClaudeTool",
    "CodexTool",
    "OnboardOutcome",
    "OnboardTarget",
    "install_skill",
    "register_mcp",
]
