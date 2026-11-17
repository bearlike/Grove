"""`grove mailbox` — look up contacts and write to another agent.

Delivery is the daemon's job (the recipient's transport is connected there and
nowhere else), so these commands go over the client SDK's URL transport with the
same same-host bearer every other local Grove process mints. There is no
separate mailbox credential: on a loopback daemon serving one user, an agent
writing to a peer is the principal already running the fleet.

``--from`` defaults to this workspace, read from ``GROVE_PHASE_FILE`` — the one
variable Grove already gives every agent, in its own namespace. It is a
CONVENIENCE, never a proof: the value is a claim the envelope prints as one, so
an agent that knows an address may always name it explicitly.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import typer
from pydantic import BaseModel, ValidationError

from grove.client import BackendConfig, GroveClient, GroveClientError
from grove.core.contracts.fleet import MailboxBatchReceipt, MailboxBatchRequest
from grove.core.contracts.mailboxes import MailboxAddress, MailboxSendRequest
from grove.core.phase import PhaseFile

DEFAULT_DAEMON_URL = "http://127.0.0.1:7421"
_BODY_FILE_OPTION = typer.Option(None, "--body-file", help="Read the body from a file, or '-'.")
# Module-level for ruff B008: a list-typed option is not an exempted primitive.
_BATCH_TO_OPTION = typer.Option(
    ..., "--to", help="Recipient workspace id. Repeat for each recipient."
)
mailbox_app = typer.Typer(
    name="mailbox",
    help=(
        "Write to another Grove agent. `contacts` lists who is reachable; "
        "`send` delivers one message. Learn: grove skills show working-in-grove. "
        "Back: grove --help."
    ),
)


def mailbox_client() -> GroveClient:
    """The local daemon client. No mailbox-specific credential exists."""
    return GroveClient(
        BackendConfig(
            label="mailbox",
            # A container agent carries its workspace credential; a host one mints.
            daemon_token=os.environ.get("GROVE_API_TOKEN") or None,
            daemon_url=os.environ.get("GROVE_MAILBOX_URL", DEFAULT_DAEMON_URL),
            daemon_socket=(
                Path(socket_path)
                if (socket_path := os.environ.get("GROVE_MAILBOX_SOCKET"))
                else None
            ),
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


def _own_workspace_id() -> str:
    """This agent's own workspace, from the variable Grove handed it.

    `PhaseFile.workspace_id_in` reads the filename and the one directory above
    it, which is what makes this correct for a containerized agent: the variable
    carries a path in the AGENT's namespace, so every leading component may be
    meaningless on this host while that tail is the part Grove composed. Never
    inferred from the cwd — several workspaces legitimately share one worktree,
    so a directory cannot say which agent is asking.
    """
    raw = os.environ.get(PhaseFile.PATH_ENV, "")
    own = PhaseFile.workspace_id_in(raw) if raw else None
    if own is None:
        raise typer.BadParameter(
            "could not tell which workspace is sending: pass --from <workspace-id> "
            f"(no usable {PhaseFile.PATH_ENV} in this environment)"
        )
    return own


def _call(operation: Callable[[GroveClient], Awaitable[BaseModel]]) -> BaseModel:
    """Run one SDK call against the local daemon."""
    client = mailbox_client()

    async def execute() -> BaseModel:
        await client.connect()
        try:
            return await operation(client)
        finally:
            await client.close()

    return asyncio.run(execute())


@mailbox_app.command("contacts")
def contacts() -> None:
    """List every agent that can be written to right now."""
    try:
        directory = _call(lambda client: client.list_mailbox_contacts())
    except GroveClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(directory.model_dump_json(indent=2))


@mailbox_app.command("send")
def send(
    *,
    to: str = typer.Option(..., "--to", help="Recipient workspace id."),
    subject: str = typer.Option(..., "--subject", help="What the message is about."),
    to_agent: str = typer.Option("", "--to-agent", help="Recipient agent slot."),
    sender: str | None = typer.Option(
        None, "--from", help="Sender workspace id. Defaults to this workspace."
    ),
    from_agent: str = typer.Option("", "--from-agent", help="Sender agent slot."),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = _BODY_FILE_OPTION,
    in_reply_to: str | None = typer.Option(
        None, "--in-reply-to", help="Message id this answers, for the reader's thread."
    ),
) -> None:
    """Send one message. A reply is this same command with the ends swapped."""
    try:
        request = MailboxSendRequest(
            sender=MailboxAddress(workspace_id=sender or _own_workspace_id(), agent=from_agent),
            recipient=MailboxAddress(workspace_id=to, agent=to_agent),
            subject=subject,
            body=_body(body, body_file),
            in_reply_to=in_reply_to,
        )
        receipt = _call(lambda client: client.send_mailbox_message(request))
    except (GroveClientError, OSError, ValidationError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(receipt.model_dump_json(indent=2))


@mailbox_app.command("batch")
def batch(
    *,
    to: list[str] = _BATCH_TO_OPTION,
    subject: str = typer.Option(..., "--subject", help="What the message is about."),
    sender: str | None = typer.Option(
        None, "--from", help="Sender workspace id. Defaults to this workspace."
    ),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = _BODY_FILE_OPTION,
    request_id: str | None = typer.Option(
        None,
        "--request-id",
        help="Idempotency key. Reuse it with the same payload to retry safely; "
        "default is a fresh one per call.",
    ),
) -> None:
    """Send one message to several agents; prints one receipt line per recipient."""
    try:
        request = MailboxBatchRequest(
            request_id=request_id or uuid.uuid4().hex,
            sender=MailboxAddress(workspace_id=sender or _own_workspace_id()),
            recipients=[MailboxAddress(workspace_id=item) for item in to],
            subject=subject,
            body=_body(body, body_file),
        )
        result = _call(lambda client: client.send_mailbox_batch(request))
    except (GroveClientError, OSError, ValidationError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    assert isinstance(result, MailboxBatchReceipt)
    if result.replayed:
        typer.echo(f"request {result.request_id} replayed (recorded receipts)")
    for receipt in result.receipts:
        reason = f" ({receipt.reason})" if receipt.reason else ""
        who = receipt.recipient.workspace_id if receipt.recipient else "?"
        typer.echo(f"{who}\t{receipt.stage}{reason}")


__all__ = ["mailbox_app"]
