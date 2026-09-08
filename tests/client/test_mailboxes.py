"""Mailbox SDK methods preserve the coordinator's HTTP contract.

Uses an ``httpx.MockTransport`` attached to a real ``GroveClient``. The
contract is intentionally hermetic: the test observes the exact HTTP request
and parses the frozen wire models without needing a mailbox-capable daemon.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from grove.client import BackendConfig, GroveClient
from grove.client.errors import NeedsPairingError
from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxReplyRequest,
    MailboxSendRequest,
)

_NOW = "2026-09-12T00:00:00Z"
_SENDER = {"address": {"workspace_id": "a" * 32, "agent": "coordinator"}, "generation": "b" * 32}
_RECIPIENT = {"workspace_id": "c" * 32, "agent": "worker"}


def _client(handler: httpx.MockTransport) -> GroveClient:
    client = GroveClient(
        BackendConfig(label="mailbox", daemon_url="http://daemon.test", daemon_token="token")
    )
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_list_mailbox_peers_preserves_optional_query_parameters() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "protocol_version": 1,
                "caller": _SENDER,
                "access": {
                    "can_discover": True,
                    "can_send": True,
                    "can_reply": True,
                    "cli": True,
                    "mcp": True,
                },
                "body_limit_bytes": 1024,
                "receipt_ttl_seconds": 60,
                "peers": [
                    {
                        "address": _RECIPIENT,
                        "generation": "d" * 32,
                        "display_name": "worker",
                        "provider": "claude_code",
                        "runtime": "container",
                        "can_receive": True,
                    }
                ],
                "next_cursor": "later",
            },
        )

    client = _client(httpx.MockTransport(handler))
    try:
        page = await client.list_mailbox_peers(workspace_id="a" * 32, limit=7, cursor="before")
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/mailboxes/peers"
    assert dict(captured[0].url.params) == {
        "workspace_id": "a" * 32,
        "limit": "7",
        "cursor": "before",
    }
    assert page.caller is not None and page.caller.address.agent == "coordinator"
    assert page.peers[0].address.agent == "worker"
    assert page.next_cursor == "later"


async def test_send_mailbox_message_posts_the_discriminated_request_verbatim() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "message_id": "mbx_" + "e" * 32,
                "sender": _SENDER,
                "recipient": {"address": _RECIPIENT, "generation": "d" * 32},
                "stage": "accepted",
                "created_at": _NOW,
                "expires_at": _NOW,
            },
        )

    request = MailboxSendRequest(
        recipient=MailboxAddress(**_RECIPIENT),
        expected_generation="d" * 32,
        intent="request",
        body="Please review the diff.\n",
    )
    client = _client(httpx.MockTransport(handler))
    try:
        receipt = await client.send_mailbox_message(request)
    finally:
        await client.close()

    assert captured[0].method == "POST"
    assert captured[0].url.path == "/mailboxes/messages"
    assert json.loads(captured[0].content) == request.model_dump(mode="json")
    assert receipt.message_id == "mbx_" + "e" * 32
    assert receipt.created_at == datetime(2026, 9, 12, tzinfo=receipt.created_at.tzinfo)


async def test_reply_posts_only_the_reply_variant() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"reply_to": "mbx_" + "e" * 32, "stage": "queued", "created_at": _NOW},
        )

    request = MailboxReplyRequest(reply_to="mbx_" + "e" * 32, body="Acknowledged.")
    client = _client(httpx.MockTransport(handler))
    try:
        receipt = await client.send_mailbox_message(request)
    finally:
        await client.close()

    assert json.loads(captured[0].content) == {
        "kind": "reply",
        "reply_to": "mbx_" + "e" * 32,
        "body": "Acknowledged.",
    }
    assert receipt.reply_to == request.reply_to
    assert receipt.stage == "queued"


async def test_get_mailbox_message_status_reads_the_receipt() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"message_id": "mbx_" + "e" * 32, "stage": "delivered", "created_at": _NOW}
        )

    client = _client(httpx.MockTransport(handler))
    try:
        receipt = await client.get_mailbox_message_status("mbx_" + "e" * 32)
    finally:
        await client.close()

    assert captured[0].method == "GET"
    assert captured[0].url.path == "/mailboxes/messages/mbx_" + "e" * 32
    assert receipt.stage == "delivered"


def test_daemon_socket_uses_a_url_transport_and_never_spawns_a_local_daemon() -> None:
    config = BackendConfig(
        label="mailbox",
        daemon_url="http://ignored.example",
        daemon_socket=Path("/run/grove/mailbox.sock"),
        daemon_token="bound-token",
    )

    client = GroveClient(config)

    assert client._transport.http_url == "http://localhost"


async def test_private_socket_without_token_never_mints_owner_credentials() -> None:
    client = GroveClient(BackendConfig(label="mailbox", daemon_socket=Path("/not-used.sock")))
    with pytest.raises(NeedsPairingError, match="explicit bound token"):
        await client.connect()
