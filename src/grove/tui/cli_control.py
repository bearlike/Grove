"""``grove control`` — trigger a running session's controls from a shell.

The CLI face of the same three engine verbs the web Controls tab, the TUI's
native-controls modal and the daemon's ``/controls`` routes already call:
``session_controls`` (what can be triggered), ``invoke_control`` (a slash
command, a skill, ``compact``, or a ``grove:<name>`` command from ``macros``)
and ``switch_model``. Nothing is re-implemented here, so a terminal workspace
gets its keystroke, a native one goes through the daemon to its owner, and a
``grove:`` command keeps its in-order, wait-for-each-step guarantee.

**The workspace is an option, not the first argument**, because the common
caller is an agent acting on its own session: ``grove control run compact``
resolves ``GROVE_PHASE_FILE`` first and the cwd second, through the same
``resolve_or_infer_workspace`` every other self-addressed verb uses. A human
pointing at somebody else's session passes ``-w <id>``.

Every trigger is a dispatch, never a wait: returning means the session has the
command, the way ``grove message`` returns once the text is delivered. A
compaction takes minutes and its progress belongs to the session's transcript,
not to a shell blocked on it.
"""

from __future__ import annotations

import typer

from grove.core.contracts.sessions import SessionControlsView
from grove.tui.cli_complete import Complete
from grove.tui.cli_workspace import clean_exit, resolve_or_infer_workspace

control_app = typer.Typer(
    name="control",
    help=(
        "Trigger a running session's controls: compact, switch model, run a slash "
        "command, a skill or a configured `grove:` command."
    ),
    no_args_is_help=True,
)

_WORKSPACE_OPTION = typer.Option(
    None,
    "--workspace",
    "-w",
    help="Workspace id or unique prefix. Default: your own session, else the one at the cwd.",
    autocompletion=Complete.workspaces,
)


@control_app.command("ls")
def list_controls(
    workspace: str | None = _WORKSPACE_OPTION,
    json_output: bool = typer.Option(False, "--json", help="Emit the controls as JSON."),
) -> None:
    """List what a session can be sent: commands, skills and its model catalog.

    Every name printed here is a valid argument to `grove control run`, and every
    model id to `grove control model`.

    \b
      grove control ls
      grove control ls -w a1b2 --json
    """
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        view = SessionControlsView.from_controls(manager.session_controls(state.id))
    if json_output:
        typer.echo(view.model_dump_json(indent=2))
        return
    typer.secho(f"{state.id}  {state.title}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  model:  {view.current_model or '(not reported)'}")
    for heading, rows in (("commands", view.commands), ("skills", view.skills)):
        if rows:
            typer.echo(f"  {heading}:")
            for row in rows:
                typer.echo(f"    {row.name}" + (f"  — {row.detail}" if row.detail else ""))
    if view.models:
        typer.echo("  models: " + ", ".join(view.models))


@control_app.command("run")
def run_control(
    name: str = typer.Argument(
        ...,
        help="A control name as `grove control ls` prints it: `compact`, a slash "
        "command, a skill, or `grove:<name>` for a configured Grove command.",
    ),
    workspace: str | None = _WORKSPACE_OPTION,
) -> None:
    """Trigger one control in a running session and return at once.

    \b
      grove control run compact
      grove control run grove:compact-fast -w a1b2
      grove control run review
    """
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        manager.invoke_control(state.id, name)
    typer.secho(f"sent /{name.strip().lstrip('/')} to {state.id} ({state.title})", fg="green")


@control_app.command("model")
def switch_model(
    model: str = typer.Argument(
        ...,
        help="Model id, forwarded verbatim (see `grove control ls`).",
        autocompletion=Complete.models,
    ),
    workspace: str | None = _WORKSPACE_OPTION,
) -> None:
    """Switch a running session's model.

    \b
      grove control model anthropic-opus-5-5
      grove control model sonnet -w a1b2
    """
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        manager.switch_model(state.id, model)
    typer.secho(f"switched {state.id} ({state.title}) to {model.strip()}", fg="green")


def register(app: typer.Typer) -> None:
    """Graft the ``grove control`` group onto the top-level app."""
    app.add_typer(control_app, name="control")


__all__ = ["control_app", "register"]
