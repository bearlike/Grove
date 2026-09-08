"""Mailbox MCP forwarding uses the dedicated bound credential client."""

from __future__ import annotations

from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxPeerPage,
    MailboxReceipt,
    MailboxReplyRequest,
    MailboxSendRequest,
)
from grove.mcp.tools import GroveTools
from tests.mcp.conftest import FakeGroveClient


class _MailboxClient(FakeGroveClient):
    def __init__(self) -> None:
        super().__init__()
        self.request: MailboxSendRequest | MailboxReplyRequest | None = None

    async def list_mailbox_peers(
        self, *, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> MailboxPeerPage:
        self.calls.append(
            ("list_mailbox_peers", {"workspace_id": workspace_id, "limit": limit, "cursor": cursor})
        )
        return MailboxPeerPage.model_validate(
            {
                "protocol_version": 1,
                "caller": None,
                "access": {
                    "can_discover": True,
                    "can_send": True,
                    "can_reply": True,
                    "cli": True,
                    "mcp": True,
                },
                "body_limit_bytes": 1024,
                "receipt_ttl_seconds": 60,
                "peers": [],
                "next_cursor": None,
            }
        )

    async def send_mailbox_message(
        self, request: MailboxSendRequest | MailboxReplyRequest
    ) -> MailboxReceipt:
        self.request = request
        self.calls.append(("send_mailbox_message", {"request": request}))
        return MailboxReceipt.model_validate(
            {
                "message_id": "mbx_" + "e" * 32,
                "stage": "accepted",
                "created_at": "2026-09-12T00:00:00Z",
            }
        )

    async def get_mailbox_message_status(self, message_id: str) -> MailboxReceipt:
        self.calls.append(("get_mailbox_message_status", {"message_id": message_id}))
        return MailboxReceipt.model_validate(
            {"message_id": message_id, "stage": "delivered", "created_at": "2026-09-12T00:00:00Z"}
        )


async def test_list_mailbox_peers_uses_the_mailbox_client() -> None:
    operator = FakeGroveClient()
    mailbox = _MailboxClient()
    page = await GroveTools(operator, mailbox_client=mailbox).list_mailbox_peers(
        workspace_id="a" * 32, limit=8, cursor="next"
    )

    assert page.body_limit_bytes == 1024
    assert mailbox.calls == [
        ("list_mailbox_peers", {"workspace_id": "a" * 32, "limit": 8, "cursor": "next"})
    ]
    assert operator.calls == []


async def test_send_mailbox_message_forwards_the_send_discriminator_to_the_bound_client() -> None:
    mailbox = _MailboxClient()
    request = MailboxSendRequest(
        recipient=MailboxAddress(workspace_id="c" * 32, agent="worker"),
        expected_generation="d" * 32,
        body="Review this.",
    )

    receipt = await GroveTools(FakeGroveClient(), mailbox_client=mailbox).send_mailbox_message(
        request
    )

    assert mailbox.request == request
    assert receipt.stage == "accepted"


async def test_send_mailbox_message_forwards_the_reply_discriminator_to_the_bound_client() -> None:
    mailbox = _MailboxClient()
    request = MailboxReplyRequest(reply_to="mbx_" + "e" * 32, body="Done.")

    receipt = await GroveTools(FakeGroveClient(), mailbox_client=mailbox).send_mailbox_message(
        request
    )

    assert mailbox.request == request
    assert receipt.message_id == "mbx_" + "e" * 32


async def test_get_mailbox_message_status_uses_the_bound_client() -> None:
    mailbox = _MailboxClient()
    receipt = await GroveTools(
        FakeGroveClient(), mailbox_client=mailbox
    ).get_mailbox_message_status("mbx_" + "e" * 32)

    assert receipt.stage == "delivered"
    assert mailbox.calls == [("get_mailbox_message_status", {"message_id": "mbx_" + "e" * 32})]
