"""``grove agent`` — several agents inside ONE workspace's container.

A thin CLI shell over the engine's multi-agent seams on ``WorkspaceManager``,
grouped under a single noun rather than sprinkled as flags across the existing
verbs. Two reasons for the grouping, both about not paying for a capability you
do not use: every existing verb keeps its exact signature and behaviour for a
single-agent workspace (the overwhelming majority), and a user who never wants a
second agent never encounters an ``--agent`` flag on ``grove message``.

The engine seams underneath are the generic ones — ``peek_pane(agent=…)`` and
``send_message(agent=…)`` are the same verbs addressed at a named agent, not
parallel implementations — so a daemon route or an MCP tool can adopt them later
without moving anything.

``attach`` follows the ``grove shell`` / ``grove attach`` shape exactly: resolve,
gate, then hand the terminal over with ``os.execvp`` — this process *becomes*
the exec, so there is nothing to bridge and nothing belongs in ``grove.client``.
"""

from __future__ import annotations

import os

import typer

from grove.core import build
from grove.core.container_agent import ContainerAgent
from grove.tui.cli_complete import Complete
from grove.tui.cli_workspace import clean_exit, resolve_workspace

agent_app = typer.Typer(
    name="agent",
    help="Run several agents inside one containerized workspace's container.",
    no_args_is_help=True,
)

_WORKSPACE_ARG = typer.Argument(
    ...,
    help="Workspace id or unique id prefix (see `grove ls`).",
    autocompletion=Complete.workspaces,
)
_NAME_ARG = typer.Argument(
    ...,
    help="Agent name (see `grove agent list`).",
    autocompletion=Complete.container_agent_names,
)


def _render(agents: tuple[ContainerAgent, ...]) -> None:
    """One line per agent: name, role, attachment, idle time."""
    if not agents:
        typer.echo("no agents running in this container")
        return
    width = max(len(a.name) for a in agents)
    for agent in agents:
        role = "primary" if agent.primary else "extra"
        idle = f"{agent.idle_seconds}s idle" if agent.idle_seconds is not None else "idle unknown"
        attached = "attached" if agent.attached else "detached"
        typer.echo(f"{agent.name:<{width}}  {role:<7}  {attached:<8}  {idle}")


@agent_app.command("list")
def list_agents(workspace: str = _WORKSPACE_ARG) -> None:
    """List the agents running inside a workspace's container.

    Read live from the container's own tmux server — nothing about an extra
    agent is stored on the workspace, so this is always what is actually there.

    \b
      grove agent list a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        _render(manager.container_agents(state.id))


@agent_app.command("add")
def add_agent(
    workspace: str = _WORKSPACE_ARG,
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Configured agent to run (default: the workspace's own).",
        autocompletion=Complete.agents,
    ),
    name: str | None = typer.Option(
        None, "--name", "-n", help="Name for the new agent (default: the next free <session>-N)."
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model id, forwarded verbatim.",
        autocompletion=Complete.models,
    ),
    prompt: str | None = typer.Option(
        None, "--prompt", "-p", help="Initial prompt the agent boots already working on."
    ),
) -> None:
    """Start ANOTHER agent inside a workspace's container.

    The new agent runs beside the workspace's own, in its own persistent
    in-container tmux session, configured through exactly the same launch seams
    (session id, hook settings, channels, model, prompt, hermetic env). It is
    started detached — `grove agent attach` when you want to watch it.

    \b
      grove agent add a1b2
      grove agent add a1b2 --agent codex --name reviewer
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        started = manager.add_container_agent(
            state.id, agent=agent, name=name, model=model, initial_prompt=prompt
        )
    typer.secho(f"started agent {started.name}", fg=typer.colors.GREEN)
    typer.echo(f"attach with: grove agent attach {state.id} {started.name}")


@agent_app.command("kill")
def kill_agent(
    workspace: str = _WORKSPACE_ARG,
    name: str = _NAME_ARG,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """End one additional agent's session inside a workspace's container.

    Refuses the workspace's own agent — that one is `grove pause` / `grove
    respawn` / `grove kill`, which also deal with the container and the record.

    \b
      grove agent kill a1b2 agent-2
    """
    if not yes:
        typer.confirm(f"end agent {name!r} in this container?", abort=True)
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.kill_container_agent(state.id, name)
    typer.secho(f"ended agent {name}", fg=typer.colors.GREEN)


@agent_app.command("peek")
def peek_agent(
    workspace: str = _WORKSPACE_ARG,
    name: str = _NAME_ARG,
    lines: int = typer.Option(40, "--lines", "-l", help="How many trailing lines to print."),
) -> None:
    """Print the tail of one in-container agent's pane.

    \b
      grove agent peek a1b2 agent-2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        snapshot, _ = manager.peek_pane(state.id, agent=name)
    if not snapshot:
        typer.echo(f"agent {name} has printed nothing yet")
        return
    typer.echo("\n".join(snapshot.splitlines()[-lines:]))


@agent_app.command("message")
def message_agent(
    workspace: str = _WORKSPACE_ARG,
    name: str = _NAME_ARG,
    text: str = typer.Argument(..., help="Text to type into that agent and submit."),
) -> None:
    """Steer one in-container agent — type text into its pane and submit it.

    \b
      grove agent message a1b2 agent-2 "run the tests"
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.send_message(state.id, text, agent=name)
    typer.secho(f"sent to {name}", fg=typer.colors.GREEN)


@agent_app.command("attach")
def attach_agent(workspace: str = _WORKSPACE_ARG, name: str = _NAME_ARG) -> None:
    """Attach this terminal to one in-container agent.

    *Replaces* this process with a `devcontainer exec … -- tmux new-session -A`
    onto that agent's session, so detaching (Ctrl-b d) leaves it running.

    \b
      grove agent attach a1b2 agent-2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        argv = manager.container_agent_argv(state.id, name)
    # Outside clean_exit: exec replaces this process, so it never returns and
    # raises no GroveError — the `grove shell` / `grove attach` convention.
    os.execvp(argv[0], argv)


def register(app: typer.Typer) -> None:
    """Graft the ``grove agent`` group onto the top-level app."""
    app.add_typer(agent_app, name="agent")


__all__ = ["agent_app", "register"]
