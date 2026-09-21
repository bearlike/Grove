"""Mailbox MCP tools forward through the one ordinary client."""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxContact,
    MailboxDirectory,
    MailboxReceipt,
    MailboxSendRequest,
)
from grove.mcp.tools import GroveTools
from tests.mcp.conftest import FakeGroveClient

WORKSPACE = "a" * 32
OTHER = "c" * 32


class _MailboxClient(FakeGroveClient):
    def __init__(self) -> None:
        super().__init__()
        self.request: MailboxSendRequest | None = None

    async def list_mailbox_contacts(self) -> MailboxDirectory:
        self.calls.append(("list_mailbox_contacts", {}))
        return MailboxDirectory(
            body_limit_bytes=1024,
            contacts=[
                MailboxContact(
                    address=MailboxAddress(workspace_id=OTHER),
                    display_name="review the parser",
                    provider="codex",
                    runtime="host",
                    live=True,
                )
            ],
        )

    async def send_mailbox_message(self, request: MailboxSendRequest) -> MailboxReceipt:
        self.request = request
        self.calls.append(("send_mailbox_message", {"subject": request.subject}))
        return MailboxReceipt(
            message_id="mbx_" + "e" * 32,
            sender=request.sender,
            recipient=request.recipient,
            stage="delivered",
            created_at=datetime.now(UTC),
        )


async def test_contacts_is_a_zero_argument_read() -> None:
    """An agent with nothing in hand can still find out who to write to."""
    client = _MailboxClient()

    directory = await GroveTools(client).list_mailbox_contacts()

    assert client.calls == [("list_mailbox_contacts", {})]
    assert directory.contacts[0].address.workspace_id == OTHER


async def test_send_forwards_the_request_unchanged() -> None:
    client = _MailboxClient()
    request = MailboxSendRequest(
        sender=MailboxAddress(workspace_id=WORKSPACE),
        recipient=MailboxAddress(workspace_id=OTHER),
        subject="Ready for review",
        body="The PR is open.",
    )

    receipt = await GroveTools(client).send_mailbox_message(request)

    assert client.request == request
    assert receipt.stage == "delivered"


async def test_the_tools_need_no_second_client() -> None:
    """The dual-client split is gone: one client, no enrollment, no gate."""
    tools = GroveTools(_MailboxClient())

    assert not hasattr(tools, "_mailbox_client")
    assert not hasattr(tools, "_require_mailbox_client")
