"""Mailbox command group over a separately bound daemon credential.

Mailbox delivery is always remote coordinator work. These commands use the
client SDK's URL transport instead of building a local manager, and sends
require ``GROVE_MAILBOX_TOKEN`` so an operator credential can never be minted
implicitly for an agent-to-agent message.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import typer
from pydantic import BaseModel

from grove.client import BackendConfig, GroveClient, GroveClientError
from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxReplyRequest,
    MailboxSendRequest,
)

DEFAULT_DAEMON_URL = "http://127.0.0.1:7421"
_BODY_FILE_OPTION = typer.Option(None, "--body-file")
mailbox_app = typer.Typer(
    name="mailbox",
    help=(
        "Discover and send native peer mail for enrolled workers. "
        "Commands need GROVE_MAILBOX_TOKEN; help does not. "
        "Learn: grove skills list --details; grove skills show working-in-grove. "
        "Setup: grove skills show configuring-grove. Back: grove --help."
    ),
)


def mailbox_client() -> GroveClient:
    """Build the bound URL client without ever minting a local owner credential."""
    token = os.environ.get("GROVE_MAILBOX_TOKEN")
    if not token:
        raise GroveClientError("GROVE_MAILBOX_TOKEN is required for mailbox sends and replies")
    return GroveClient(
        BackendConfig(
            label="mailbox",
            daemon_url=os.environ.get("GROVE_MAILBOX_URL", DEFAULT_DAEMON_URL),
            daemon_socket=(
                Path(socket_path)
                if (socket_path := os.environ.get("GROVE_MAILBOX_SOCKET"))
                else None
            ),
            daemon_token=token,
        )
    )


def _body(body: str | None, body_file: Path | None) -> str:
    if (body is None) == (body_file is None):
        raise typer.BadParameter("provide exactly one of --body or --body-file")
    if body_file is None:
        assert body is not None
        return body
    if str(body_file) == "-":
        return sys.stdin.read()
    return body_file.read_text(encoding="utf-8")


async def _peers(
    client: GroveClient, workspace_id: str | None, limit: int, cursor: str | None
) -> BaseModel:
    return await client.list_mailbox_peers(workspace_id=workspace_id, limit=limit, cursor=cursor)


async def _send(
    client: GroveClient, request: MailboxSendRequest | MailboxReplyRequest
) -> BaseModel:
    return await client.send_mailbox_message(request)


async def _status(client: GroveClient, message_id: str) -> BaseModel:
    return await client.get_mailbox_message_status(message_id)


def _call(operation: Callable[[GroveClient], Awaitable[BaseModel]]) -> BaseModel:
    """Run one SDK call using the explicit mailbox credential."""
    client = mailbox_client()

    async def execute() -> BaseModel:
        await client.connect()
        try:
            return await operation(client)
        finally:
            await client.close()

    return asyncio.run(execute())


@mailbox_app.command("peers")
def peers(
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    limit: int = typer.Option(50, "--limit", min=1),
    cursor: str | None = typer.Option(None, "--cursor"),
) -> None:
    """List mailbox peers visible to the bound coordinator credential."""
    try:
        page = _call(lambda client: _peers(client, workspace_id, limit, cursor))
    except GroveClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(page.model_dump_json(indent=2))


@mailbox_app.command("send")
def send(
    workspace_id: str = typer.Argument(..., help="Recipient workspace id."),
    *,
    generation: str = typer.Option(
        ..., "--generation", help="Recipient generation from mailbox peers."
    ),
    agent: str = typer.Option("", "--agent", help="Recipient agent slot."),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = _BODY_FILE_OPTION,
    intent: str = typer.Option("information", "--intent"),
) -> None:
    """Send one mailbox message; transport failure leaves delivery unknown."""
    try:
        request = MailboxSendRequest(
            recipient=MailboxAddress(workspace_id=workspace_id, agent=agent),
            expected_generation=generation,
            intent=intent,
            body=_body(body, body_file),
        )
        receipt = _call(lambda client: _send(client, request))
    except (GroveClientError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(receipt.model_dump_json(indent=2))


@mailbox_app.command("reply")
def reply(
    message_id: str = typer.Argument(..., help="Original mailbox message id."),
    *,
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = _BODY_FILE_OPTION,
) -> None:
    """Reply through the coordinator without trusting quoted sender metadata."""
    try:
        request = MailboxReplyRequest(reply_to=message_id, body=_body(body, body_file))
        receipt = _call(lambda client: _send(client, request))
    except (GroveClientError, OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(receipt.model_dump_json(indent=2))


@mailbox_app.command("status")
def status(message_id: str = typer.Argument(..., help="Mailbox message id.")) -> None:
    """Read the latest coordinator delivery observation for a mailbox message."""
    try:
        receipt = _call(lambda client: _status(client, message_id))
    except GroveClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(receipt.model_dump_json(indent=2))


__all__ = ["mailbox_app"]
