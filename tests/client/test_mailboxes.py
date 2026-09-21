"""Mailbox SDK methods preserve the daemon's HTTP contract.

Uses an ``httpx.MockTransport`` attached to a real ``GroveClient``: the test
observes the exact HTTP request and parses the frozen wire models without
needing a running daemon.
"""

from __future__ import annotations

import json

import httpx

from grove.client import BackendConfig, GroveClient
from grove.core.contracts.mailboxes import MailboxAddress, MailboxSendRequest

_NOW = "2026-09-12T00:00:00Z"
_SENDER = {"workspace_id": "a" * 32, "agent": ""}
_RECIPIENT = {"workspace_id": "c" * 32, "agent": "worker"}


def _client(handler: httpx.MockTransport) -> GroveClient:
    client = GroveClient(
        BackendConfig(label="mailbox", daemon_url="http://daemon.test", daemon_token="token")
    )
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_contacts_reads_the_directory_with_no_query_parameters() -> None:
    """Every live agent, every time — there is nothing to scope or page."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "protocol_version": 2,
                "body_limit_bytes": 1024,
                "contacts": [
                    {
                        "address": _RECIPIENT,
                        "display_name": "review the parser",
                        "provider": "codex",
                        "runtime": "host",
                        "live": True,
                    }
                ],
            },
        )

    directory = await _client(httpx.MockTransport(handler)).list_mailbox_contacts()

    assert captured[0].url.path == "/mailboxes/contacts"
    assert not captured[0].url.params
    assert directory.contacts[0].provider == "codex"
    assert directory.contacts[0].live is True


async def test_send_posts_the_addressed_message_verbatim() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "message_id": "mbx_" + "e" * 32,
                "sender": _SENDER,
                "recipient": _RECIPIENT,
                "stage": "delivered",
                "reason": None,
                "detail": None,
                "created_at": _NOW,
            },
        )

    request = MailboxSendRequest(
        sender=MailboxAddress(workspace_id="a" * 32),
        recipient=MailboxAddress(workspace_id="c" * 32, agent="worker"),
        subject="Ready for review",
        body="The PR is open.",
    )
    receipt = await _client(httpx.MockTransport(handler)).send_mailbox_message(request)

    body = json.loads(captured[0].content)
    assert captured[0].url.path == "/mailboxes/messages"
    assert body["subject"] == "Ready for review"
    assert body["recipient"]["agent"] == "worker"
    assert "expected_generation" not in body
    assert receipt.stage == "delivered"


async def test_a_refusal_decodes_as_a_receipt_rather_than_raising() -> None:
    """ "That agent is not live" is an answer to the question, not a fault."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message_id": None,
                "sender": _SENDER,
                "recipient": _RECIPIENT,
                "stage": "rejected",
                "reason": "not_live",
                "detail": "no live agent at that address",
                "created_at": _NOW,
            },
        )

    request = MailboxSendRequest(
        sender=MailboxAddress(workspace_id="a" * 32),
        recipient=MailboxAddress(workspace_id="c" * 32, agent="worker"),
        subject="Ready",
        body="ping",
    )
    receipt = await _client(httpx.MockTransport(handler)).send_mailbox_message(request)

    assert receipt.stage == "rejected"
    assert receipt.reason == "not_live"
    assert receipt.message_id is None
