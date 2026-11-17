"""``grove vacuum`` — run one reaper pass by hand and print what it found.

A thin shell over :class:`~grove.core.reaper.Reaper`, the same pass the daemon
runs on its schedule. Dry by default; ``--apply`` removes. Exits 1 when the
pass found anything, so a CI job can assert the host is clean.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, get_args

import typer

#: The resource classes `--class` accepts. A literal here because the reaper
#: package is imported lazily; a census test pins it to `ResourceClass`.
CLASSES: tuple[str, ...] = ("tmux", "process", "files")


def vacuum(
    *,
    apply: Annotated[
        bool, typer.Option("--apply", help="Remove what is past its grace (default: report).")
    ] = False,
    classes: Annotated[
        list[str] | None,
        typer.Option(
            "--class",
            help=f"Only this resource class; repeatable. One of: {', '.join(CLASSES)}.",
        ),
    ] = None,
) -> None:
    """Reconcile leaked tmux servers, Grove processes and files against the workspace records.

    A resource is marked the first time it is seen and removed only on a later
    pass after its grace (a minute for servers and processes, a day for files),
    so run this twice to remove a leak. Candidates already marked and still in
    their grace are listed with when they were first seen and how long is left;
    the exit status is 1 whenever anything is marked, sweepable or swept.
    """
    unknown = sorted(set(classes or ()) - set(CLASSES))
    if unknown:
        raise typer.BadParameter(f"unknown class: {', '.join(unknown)}", param_hint="--class")
    # Deferred: every CLI invocation (and every completion TAB) imports this
    # module, and only `grove vacuum` needs the reaper.
    from grove.core.reaper import (  # noqa: PLC0415
        FilesCollector,
        ProcessCollector,
        Reaper,
        ReaperStore,
        ResourceClass,
        TmuxCollector,
    )
    from grove.core.store import JsonWorkspaceStore  # noqa: PLC0415

    selected = frozenset(c for c in get_args(ResourceClass) if c in classes) if classes else None

    reaper = Reaper(
        collectors=(TmuxCollector(), ProcessCollector(), FilesCollector()),
        workspaces=JsonWorkspaceStore(),
        store=ReaperStore(),
    )
    report = reaper.run(apply=apply, classes=selected)
    now = datetime.now(UTC)
    for wait in report.waiting:
        left = max(wait.sweepable_at - now, timedelta(0))
        typer.echo(
            f"{'marked':<7} {wait.ref.cls:<8} {wait.ref.key}  first seen "
            f"{wait.first_seen.astimezone().isoformat(timespec='seconds')}, "
            f"sweepable in {_duration(left)}"
        )
    for row in report.rows:
        line = f"{row.action:<7} {row.ref.cls:<8} {row.ref.key}  {row.reason}"
        if row.error:
            line += f"  ({row.error})"
        typer.echo(line)
    if not report.found_anything:
        typer.echo("nothing to reap")
    raise typer.Exit(code=1 if report.found_anything else 0)


def ps(ref: Annotated[str, typer.Argument(help="Workspace id or unique prefix.")]) -> None:
    """List every process a host workspace is running, read from its own cgroup scope.

    Exact where systemd's user manager hosts the workspace's scope. Otherwise,
    and for a workspace created before scopes, it walks the pane process tree,
    which misses anything that detached, and says so.
    """
    from grove.core.process import ProcessTree  # noqa: PLC0415
    from grove.core.scope import Scope  # noqa: PLC0415
    from grove.core.tmux import TmuxServer, session_pane_pids  # noqa: PLC0415
    from grove.tui.cli_workspace import clean_exit, resolve_workspace  # noqa: PLC0415

    with clean_exit():
        _, state = resolve_workspace(ref)
    pids: tuple[int, ...] = ()
    if state.tmux_socket:
        pids = Scope.for_socket(state.tmux_socket).pids()
    exact = bool(pids)
    if not exact:
        roots = session_pane_pids(state.tmux_session, TmuxServer(socket=state.tmux_socket))
        pids = tuple(pid for pid, _ in ProcessTree.capture(roots).identities) if roots else ()
        typer.echo("approximate: no cgroup scope, walking the pane process tree", err=True)
    for pid in sorted(pids):
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            continue
        typer.echo(f"{pid:>8} {comm}")


def _duration(span: timedelta) -> str:
    """``1h 05m`` / ``4m`` / ``30s``: how long until a marked candidate may be swept."""
    total = int(span.total_seconds())
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


def register(app: typer.Typer) -> None:
    """Graft ``grove vacuum`` and ``grove ps`` onto the top-level app, flat like the other verbs."""
    app.command("vacuum")(vacuum)
    app.command("ps")(ps)


__all__ = ["register"]
