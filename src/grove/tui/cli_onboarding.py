"""``grove skills`` / ``grove mcp`` — onboard Claude Code / Codex onto Grove.

Thin CLI shell over the side effects in `core/agents/onboarding.py`: this
module owns only the two CLI concerns the engine deliberately stays out of —
resolving `--target`/`--agent` (including the interactive y/n questionnaire
when a flag is omitted) and rendering the resulting `OnboardOutcome`s. Reused
verbatim by `grove config init --with-onboarding` (`run_onboarding`) so the
three entry points (`grove skills install`, `grove mcp install`, `grove
config init`) can't drift.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import cast

import typer

from grove.core.agents import onboarding
from grove.core.agents.onboarding import (
    TOOLS,
    AgentToolName,
    OnboardAction,
    OnboardOutcome,
    OnboardTarget,
)
from grove.core.errors import GroveError
from grove.core.git import detect_root
from grove.tui.cli_workspace import clean_exit


class TargetChoice(StrEnum):
    user = "user"
    project = "project"
    all = "all"


class AgentChoice(StrEnum):
    claude = "claude"
    codex = "codex"
    all = "all"


_STATUS_COLOR = {
    "ok": typer.colors.GREEN,
    "skipped": typer.colors.YELLOW,
    "unsupported": typer.colors.YELLOW,
}

skills_app = typer.Typer(
    name="skills",
    help="Install the bundled Grove fleet-orchestration skill for Claude Code / Codex.",
    no_args_is_help=True,
)
mcp_app = typer.Typer(
    name="mcp",
    help="Register Grove itself as an MCP server with Claude Code / Codex.",
    no_args_is_help=True,
)


def resolve_targets(
    target: TargetChoice | None, *, repo_root: Path | None, question: str
) -> list[OnboardTarget]:
    """`--target` value -> concrete target list; unset -> ask two y/n questions.

    "all" (or the interactive default) only ever includes "project" when
    ``repo_root`` is known — there is nothing to scope a project install to
    otherwise.
    """
    if target == TargetChoice.all:
        return ["user", "project"] if repo_root is not None else ["user"]
    if target is not None:
        return [cast("OnboardTarget", target.value)]

    targets: list[OnboardTarget] = []
    if typer.confirm(f"{question} — install to your USER skills/MCP config?", default=True):
        targets.append("user")
    if repo_root is not None and typer.confirm(
        f"{question} — install to this PROJECT ({repo_root})?", default=False
    ):
        targets.append("project")
    return targets


def resolve_agents(agent: AgentChoice) -> list[AgentToolName]:
    return list(TOOLS) if agent == AgentChoice.all else [cast("AgentToolName", agent.value)]


def render_outcomes(outcomes: list[OnboardOutcome]) -> None:
    for outcome in outcomes:
        head = f"[{outcome.tool}/{outcome.target}] {outcome.action}: {outcome.status}"
        typer.secho(f"{head} — {outcome.detail}", fg=_STATUS_COLOR[outcome.status])


def run_onboarding(
    *,
    actions: list[OnboardAction],
    targets: list[OnboardTarget],
    agents: list[AgentToolName],
    repo_root: Path | None,
) -> list[OnboardOutcome]:
    """The shared (agent x target x action) fan-out both CLI commands and
    ``grove config init --with-onboarding`` run — one funnel, so they can't drift.
    """
    outcomes: list[OnboardOutcome] = []
    for agent_name in agents:
        tool = TOOLS[agent_name]
        for target in targets:
            if "skill" in actions:
                outcomes.append(onboarding.install_skill(tool, target, repo_root=repo_root))
            if "mcp" in actions:
                outcomes.append(onboarding.register_mcp(tool, target, repo_root=repo_root))
    return outcomes


# Module-level singletons, not inline `typer.Option(...)` defaults: ruff's B008
# only exempts typer's own calls for primitive-typed params, and both commands
# share the identical pair of flags — one definition, not two near-copies.
_TARGET_OPTION = typer.Option(
    None, "--target", "-t", help="user | project | all. Omit to be asked."
)
_AGENT_OPTION = typer.Option(AgentChoice.all, "--agent", "-a", help="claude | codex | all.")


@skills_app.command("install")
def skills_install(
    target: TargetChoice | None = _TARGET_OPTION,
    agent: AgentChoice = _AGENT_OPTION,
) -> None:
    """Copy the bundled using-grove skill into Claude/Codex skill directories."""
    repo_root = detect_root(Path.cwd())
    with clean_exit():
        if target == TargetChoice.project and repo_root is None:
            raise GroveError("not in a git repository (needed for --target project)")
        targets = resolve_targets(target, repo_root=repo_root, question="using-grove skill")
        outcomes = run_onboarding(
            actions=["skill"], targets=targets, agents=resolve_agents(agent), repo_root=repo_root
        )
    render_outcomes(outcomes)


@mcp_app.command("install")
def mcp_install(
    target: TargetChoice | None = _TARGET_OPTION,
    agent: AgentChoice = _AGENT_OPTION,
) -> None:
    """Register this Grove install as an MCP server via `claude`/`codex mcp add`."""
    repo_root = detect_root(Path.cwd())
    with clean_exit():
        if target == TargetChoice.project and repo_root is None:
            raise GroveError("not in a git repository (needed for --target project)")
        targets = resolve_targets(target, repo_root=repo_root, question="Grove MCP server")
        outcomes = run_onboarding(
            actions=["mcp"], targets=targets, agents=resolve_agents(agent), repo_root=repo_root
        )
    render_outcomes(outcomes)


def register(app: typer.Typer) -> None:
    app.add_typer(skills_app, name="skills")
    app.add_typer(mcp_app, name="mcp")
