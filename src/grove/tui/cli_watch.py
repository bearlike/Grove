"""``grove watch`` — register a callback instead of sleeping in a loop.

A thin shell over the daemon's ``/watches`` routes, in the exact shape
``cli_mailbox`` already has: build a client, resolve the caller's own workspace
from ``GROVE_PHASE_FILE``, print JSON. Every decision — what settles, when to
look again, who is told — belongs to the engine; nothing in this file chooses
anything an MCP caller would not get too.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast, get_args

import typer
from pydantic import BaseModel, ValidationError

from grove.client import BackendConfig, GroveClient
from grove.client.errors import GroveClientError
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.tickets import TicketProviderName
from grove.core.contracts.watches import (
    DEFAULT_DEADLINE,
    DEFAULT_INTERVAL,
    CiPredicate,
    CommandPredicate,
    TimerPredicate,
    WatchPredicate,
    WatchRegistration,
)
from grove.core.phase import PhaseFile

DEFAULT_DAEMON_URL = "http://127.0.0.1:7421"
_DEFAULT_MIN = int(DEFAULT_DEADLINE.total_seconds() // 60)

watch_app = typer.Typer(
    name="watch",
    help=(
        "Wait for something without sleeping. Register a watch, stop, and Grove "
        "mails you the outcome when it settles or when your deadline passes."
    ),
)


def _client() -> GroveClient:
    socket_path = os.environ.get("GROVE_MAILBOX_SOCKET")
    return GroveClient(
        BackendConfig(
            label="watch",
            daemon_url=os.environ.get("GROVE_MAILBOX_URL", DEFAULT_DAEMON_URL),
            daemon_socket=Path(socket_path) if socket_path else None,
        )
    )


def _own_workspace() -> str:
    """Whose callback this is, from the variable Grove exported at launch.

    The same resolution ``grove mailbox send`` uses, and for the same reason: a
    cwd cannot identify an AGENT (several workspaces share a repo root under
    root placement, several agents share one container), so the directory is
    never asked.
    """
    raw = os.environ.get(PhaseFile.PATH_ENV, "")
    own = PhaseFile.workspace_id_in(raw) if raw else None
    if own is None:
        raise typer.BadParameter(
            "could not tell which workspace is asking: pass --for <workspace-id> "
            f"(no usable {PhaseFile.PATH_ENV} in this environment)"
        )
    return own


def _run(operation: Callable[[GroveClient], Awaitable[BaseModel]]) -> BaseModel:
    client = _client()

    async def execute() -> BaseModel:
        await client.connect()
        try:
            return await operation(client)
        finally:
            await client.close()

    return asyncio.run(execute())


def _emit(result: BaseModel) -> None:
    typer.echo(result.model_dump_json(indent=2))


def _fail(exc: Exception) -> None:
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc


def _register(
    predicate: WatchPredicate,
    *,
    owner: str | None,
    every: int,
    deadline_minutes: float | None,
    note: str,
) -> None:
    recipient = MailboxAddress(workspace_id=owner or _own_workspace())
    try:
        registration = WatchRegistration(
            recipient=recipient,
            predicate=predicate,
            every=timedelta(seconds=every),
            # Omitted means "the engine's default", never a CLI-local number:
            # a second copy of the default here is how the CLI and MCP would
            # come to disagree about how long an agent may be left waiting.
            deadline=None if deadline_minutes is None else timedelta(minutes=deadline_minutes),
            note=note,
        )
    except (ValidationError, ValueError) as exc:
        _fail(exc)
    try:
        _emit(_run(lambda client: client.register_watch(registration)))
    except (GroveClientError, OSError) as exc:
        _fail(exc)


@watch_app.command("ci")
def watch_ci(
    head_sha: str = typer.Argument(..., help="The commit whose checks you are waiting on."),
    owner: str = typer.Option(..., "--owner", help="Repository owner on the forge."),
    repo: str = typer.Option(..., "--repo", help="Repository name on the forge."),
    provider: str = typer.Option("gitea", "--provider", help="Which forge holds it."),
    every: int = typer.Option(30, "--every", help="Seconds between checks (minimum 15)."),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        help=(
            "Minutes to wait before giving up; you are messaged either way. "
            f"Default {_DEFAULT_MIN} (a timer defaults to its own time)."
        ),
    ),
    note: str = typer.Option("", "--note", help="One line to remind you what this was for."),
    for_workspace: str = typer.Option("", "--for", help="Register on another workspace's behalf."),
) -> None:
    """Wake me when the checks on this commit have all concluded."""
    if provider not in get_args(TicketProviderName):
        raise typer.BadParameter(
            f"unknown provider {provider!r}; expected one of "
            f"{', '.join(get_args(TicketProviderName))}"
        )
    _register(
        CiPredicate(
            provider=cast("TicketProviderName", provider),
            owner=owner,
            repo=repo,
            head_sha=head_sha,
        ),
        owner=for_workspace or None,
        every=every,
        deadline_minutes=deadline,
        note=note,
    )


@watch_app.command("timer")
def watch_timer(
    minutes: float = typer.Argument(..., help="Wake me in this many minutes."),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        help=(
            "Minutes to wait before giving up; you are messaged either way. "
            f"Default {_DEFAULT_MIN} (a timer defaults to its own time)."
        ),
    ),
    note: str = typer.Option("", "--note", help="One line to remind you what this was for."),
    for_workspace: str = typer.Option("", "--for", help="Register on another workspace's behalf."),
) -> None:
    """The durable replacement for ``sleep``: survives a restart, tells you why."""
    at = datetime.now(UTC) + timedelta(minutes=minutes)
    _register(
        TimerPredicate(at=at),
        owner=for_workspace or None,
        every=int(DEFAULT_INTERVAL.total_seconds()),
        deadline_minutes=deadline,
        note=note,
    )


@watch_app.command("cmd")
def watch_cmd(
    argv: Annotated[list[str], typer.Argument(help="The command, after `--`.")],
    every: int = typer.Option(30, "--every", help="Seconds between runs (minimum 15)."),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        help=(
            "Minutes to wait before giving up; you are messaged either way. "
            f"Default {_DEFAULT_MIN} (a timer defaults to its own time)."
        ),
    ),
    exit_code: Annotated[
        list[int] | None,
        typer.Option("--exit-code", help="Statuses that mean settled (default 0)."),
    ] = None,
    note: str = typer.Option("", "--note", help="One line to remind you what this was for."),
    for_workspace: str = typer.Option("", "--for", help="Register on another workspace's behalf."),
) -> None:
    """Wake me when this command exits with a status that means it is done."""
    owner = for_workspace or _own_workspace()
    _register(
        CommandPredicate(
            argv=list(argv),
            terminal_exit_codes=list(exit_code) if exit_code else [0],
            workspace_id=owner,
        ),
        owner=owner,
        every=every,
        deadline_minutes=deadline,
        note=note,
    )


@watch_app.command("ls")
def watch_ls() -> None:
    """Every watch on this host — pending, and recently settled."""
    try:
        _emit(_run(lambda client: client.list_watches()))
    except (GroveClientError, OSError) as exc:
        _fail(exc)


@watch_app.command("cancel")
def watch_cancel(
    watch_id: str = typer.Argument(..., help="The id `grove watch ls` shows."),
) -> None:
    """Withdraw a watch you no longer need."""
    try:
        _emit(_run(lambda client: client.cancel_watch(watch_id)))
    except (GroveClientError, OSError) as exc:
        _fail(exc)


__all__ = ["watch_app"]
