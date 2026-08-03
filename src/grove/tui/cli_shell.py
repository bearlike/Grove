"""``grove shell`` — an interactive shell INSIDE a containerized workspace.

A thin CLI shell over :class:`~grove.core.container_shell.ContainerShell`, the
same shape as ``cli_code.py`` and ``attach_workspace``: resolve an id-prefix,
gate on a precondition the engine can't express verb-agnostically, hand the
terminal to an external process with ``os.execvp``.

**Why this is a CLI verb over an engine seam and not a ``grove.client``
transport.** ``client/`` holds two kinds of thing: ``AttachSession``, which
bridges a PTY to xterm.js for the daemon/webapp, and ``vscode.py``, a
launch-and-forget external editor a remote surface could also trigger. This is
neither. Nothing is bridged — the CLI process *becomes* the exec, exactly as
``grove attach`` becomes ``tmux attach``, and ``grove attach`` is likewise a CLI
verb with no ``client/`` counterpart. A ``ContainerShellAttach`` class over
there would be an abstraction with one implementation and no stream to share,
which is the YAGNI trap this codebase names explicitly. If a remote surface ever
needs this, the composition it needs is already in ``core`` and reachable
without moving anything.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from grove.core import GroveError, build
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_shell import ContainerShell
from grove.core.workspace import WorkspaceState, ensure_can_attach
from grove.tui.cli_workspace import clean_exit, resolve_workspace

# Mirrors cli_workspace.py's private ``_WORKSPACE_ARG`` verbatim rather than
# importing it — that name is underscore-prefixed on purpose (an internal
# seam, per the root CLAUDE.md "leading underscore = don't import" rule),
# and the definition is one line.
_WORKSPACE_ARG = typer.Argument(
    ...,
    help="Workspace id or unique id prefix (see `grove ls`).",
)


def _container_target(state: WorkspaceState) -> ContainerRuntimeState:
    """The workspace's container, or a message saying why there isn't one.

    ``container is None`` is exactly "this is a host workspace", and a host
    workspace's shell is `grove attach` — there is no namespace to cross. The
    engine has no verb-agnostic way to express a container-only precondition, so
    it is checked here (the ``grove code`` shape).
    """
    if state.container is None:
        raise GroveError(
            "this workspace has no container — `grove shell` enters a containerized "
            "workspace's container; a host workspace's shell is `grove attach`"
        )
    return state.container


def shell_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Open an interactive shell inside a containerized workspace's container.

    Resolves the id-prefix, then *replaces* this process with
    ``devcontainer exec … -- tmux new-session -A -s shell <shell>``, landing at
    the agent's own working directory. The shell runs under the container's tmux
    so it **persists between visits** — leave it and come back to the same shell,
    with its history and anything still running. Detach with the usual tmux key
    (Ctrl-b d); with no reachable in-container tmux it degrades to a plain
    interactive shell that ends when you leave.

    Which shell is `container.shell` (a chain, tried in order — an image may
    have no bash). Requires a containerized workspace with a live container.

    \b
      grove shell a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        container = _container_target(state)
        # The engine's own attach precondition, reused rather than restated: a
        # container that is gone reconciles to OFFLINE, and execing into
        # it would surface as an opaque CLI failure instead of Grove's own
        # "respawn it first".
        ensure_can_attach(state)
        argv = ContainerShell(
            container=container,
            cfg=manager.config,
            worktree=Path(state.worktree_path),
            cwd=state.agent_cwd,
        ).argv()
    # Outside clean_exit: exec replaces this process, so it never returns and
    # raises no GroveError. `devcontainer` is resolved off PATH by the same
    # convention `grove attach` resolves tmux — a missing binary surfaces as the
    # OS's own exec error.
    os.execvp(argv[0], argv)


def register(app: typer.Typer) -> None:
    """Graft ``grove shell`` onto the top-level app, flat like the other verbs."""
    app.command("shell")(shell_workspace)


__all__ = ["register"]
