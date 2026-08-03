"""``grove code`` — open a containerized workspace in VS Code.

A thin CLI shell over :mod:`grove.client.vscode`, mirroring the shape of
`attach_workspace` in ``cli_workspace.py``: resolve an id-prefix, gate on a
precondition the engine can't express (no container = nothing to attach
to), hand off to an external process. TUI screens and webapp buttons for
the same verb are deliberately out of scope — this is the CLI surface.
"""

from __future__ import annotations

import asyncio

import typer

from grove.client.vscode import ContainerTarget, VsCodeAttach
from grove.core import GroveError, build
from grove.core.workspace import WorkspaceState
from grove.tui.cli_workspace import clean_exit, resolve_workspace

# Mirrors cli_workspace.py's private ``_WORKSPACE_ARG`` verbatim rather than
# importing it — that name is underscore-prefixed on purpose (an internal
# seam, per the root CLAUDE.md "leading underscore = don't import" rule),
# and the definition is one line.
_WORKSPACE_ARG = typer.Argument(
    ...,
    help="Workspace id or unique id prefix (see `grove ls`).",
)


def _container_target(state: WorkspaceState) -> ContainerTarget:
    """The workspace's container identity, or a message saying why there isn't one.

    ``container is None`` is exactly "this is a host workspace" — VS Code
    attach is a container-only verb, and that is a precondition the engine has
    no verb-agnostic way to express, so it is checked here.
    """
    if state.container is None:
        raise GroveError(
            "this workspace has no container — `grove code` opens a containerized "
            "workspace in VS Code; a host workspace already has a shell (`grove attach`)"
        )
    return state.container


def code_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Open a workspace's container in VS Code (Dev Containers-compatible attach).

    Resolves the id-prefix, builds the ``vscode-remote://attached-container+…``
    URI from the workspace's container state, and launches ``code``
    (falling back to ``code-insiders``) pointed at it — detached, so this
    command returns immediately. Requires a containerized workspace; a host
    workspace has no container to attach to and this errors cleanly instead.

    \b
      grove code a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        target = _container_target(state)
        attach = VsCodeAttach(target)
        asyncio.run(attach.start())
    typer.secho(f"opened {state.id} ({state.title}) in VS Code", fg=typer.colors.GREEN)


def register(app: typer.Typer) -> None:
    """Graft ``grove code`` onto the top-level app, flat like the other verbs."""
    app.command("code")(code_workspace)


__all__ = ["register"]
