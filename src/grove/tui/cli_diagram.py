"""Managed diagram commands: thin local shells over ``WorkspaceManager``.

The CLI intentionally owns no XML or file handling. It resolves a workspace as
other id-addressed commands do, then asks the manager to perform the same
revisioned operation exposed through the daemon and MCP surfaces.
"""

from __future__ import annotations

from pathlib import Path

import typer

from grove.core import GroveError
from grove.core.contracts.diagrams import (
    DiagramDocumentView,
    DiagramOpenRequest,
    DiagramPreviewView,
    DiagramStopRequest,
    DiagramUpdateRequest,
)
from grove.tui.cli_workspace import clean_exit, resolve_or_infer_workspace

diagram_app = typer.Typer(
    name="diagram",
    help="Open, read, conditionally update, or stop a managed .drawio document.",
    no_args_is_help=True,
)

_INPUT_ARGUMENT = typer.Argument(..., help="UTF-8 XML file to conditionally save.")
_REVISION_OPTION = typer.Option(..., "--revision", help="Revision from the last read/open/update.")
_MAX_XML_BYTES = 5 * 1024 * 1024
_SESSION_ID_OPTION = typer.Option(
    ..., "--session-id", help="Active diagram session id from the last response."
)


def _emit(document: DiagramDocumentView) -> None:
    """Emit the shared document contract verbatim for scripts and agents."""
    typer.echo(document.model_dump_json(indent=2))


def _emit_preview(preview: DiagramPreviewView) -> None:
    """Emit local image location and revision metadata without duplicating image bytes."""
    typer.echo(
        preview.model_copy(update={"content_base64": ""}).model_dump_json(
            indent=2, exclude={"content_base64"}
        )
    )


def _validate_input(path: Path) -> str:
    """Read bounded UTF-8 XML before constructing the managed update request."""
    try:
        with path.open("rb") as source:
            content = source.read(_MAX_XML_BYTES + 1)
    except OSError as exc:
        raise GroveError(f"could not read diagram XML from {path}: {exc}") from exc
    if len(content) > _MAX_XML_BYTES:
        raise GroveError(f"diagram XML input exceeds {_MAX_XML_BYTES} bytes: {path}")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GroveError(f"diagram XML input is not UTF-8: {path}") from exc


@diagram_app.command("open")
def open_diagram(
    path: str = typer.Argument(..., help="Existing workspace-relative .drawio path."),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id/prefix. Omit to infer from the current worktree.",
    ),
) -> None:
    """Open an existing ``.drawio`` file for managed collaboration."""
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        _emit(manager.open_diagram(state.id, DiagramOpenRequest(path=path)))


@diagram_app.command("read")
def read_diagram(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id/prefix. Omit to infer from the current worktree.",
    ),
) -> None:
    """Read the current acknowledged XML, session identity, and revision."""
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        _emit(manager.read_diagram(state.id))


@diagram_app.command("preview")
def read_diagram_preview(
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id/prefix. Omit to infer from the current worktree.",
    ),
) -> None:
    """Print the current first-page preview's attachment path and revision metadata."""
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        _emit_preview(manager.read_diagram_preview(state.id))


@diagram_app.command("update")
def update_diagram(
    input: Path = _INPUT_ARGUMENT,
    revision: str = _REVISION_OPTION,
    session_id: str = _SESSION_ID_OPTION,
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id/prefix. Omit to infer from the current worktree.",
    ),
) -> None:
    """Conditionally save XML; preserve the input when a conflict is reported."""
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        _emit(
            manager.update_diagram(
                state.id,
                DiagramUpdateRequest(
                    session_id=session_id,
                    expected_revision=revision,
                    xml=_validate_input(input),
                ),
            )
        )


@diagram_app.command("stop")
def stop_diagram(
    revision: str = _REVISION_OPTION,
    session_id: str = _SESSION_ID_OPTION,
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id/prefix. Omit to infer from the current worktree.",
    ),
) -> None:
    """Stop collaboration conditionally and return the retained read-only document."""
    with clean_exit():
        manager, state = resolve_or_infer_workspace(workspace)
        _emit(
            manager.stop_diagram(
                state.id,
                DiagramStopRequest(session_id=session_id, expected_revision=revision),
            )
        )


def register(app: typer.Typer) -> None:
    """Mount the diagram noun beside the other top-level workspace surfaces."""
    app.add_typer(diagram_app, name="diagram")


__all__ = ["register"]
