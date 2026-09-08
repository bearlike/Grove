"""Mailbox CLI delegates exclusively to the bound URL client."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from grove.core.contracts.mailboxes import MailboxPeerPage, MailboxReceipt
from grove.tui.cli import app


class _MailboxClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def connect(self) -> None:
        self.calls.append(("connect", None))

    async def close(self) -> None:
        self.calls.append(("close", None))

    async def list_mailbox_peers(
        self, *, workspace_id: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> MailboxPeerPage:
        self.calls.append(
            ("peers", {"workspace_id": workspace_id, "limit": limit, "cursor": cursor})
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
                "body_limit_bytes": 2048,
                "receipt_ttl_seconds": 60,
                "peers": [],
                "next_cursor": None,
            }
        )

    async def send_mailbox_message(self, request: object) -> MailboxReceipt:
        self.calls.append(("send", request))
        return MailboxReceipt.model_validate(
            {
                "message_id": "mbx_" + "e" * 32,
                "stage": "accepted",
                "created_at": "2026-09-12T00:00:00Z",
            }
        )

    async def get_mailbox_message_status(self, message_id: str) -> MailboxReceipt:
        self.calls.append(("status", message_id))
        return MailboxReceipt.model_validate(
            {"message_id": message_id, "stage": "delivered", "created_at": "2026-09-12T00:00:00Z"}
        )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_mailbox_peers_uses_the_url_client_and_emits_json(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MailboxClient()
    monkeypatch.setattr("grove.tui.cli_mailbox.mailbox_client", lambda: client)

    result = runner.invoke(app, ["mailbox", "peers", "--workspace-id", "a" * 32, "--limit", "9"])

    assert result.exit_code == 0, result.output
    assert '"body_limit_bytes": 2048' in result.output
    assert client.calls == [
        ("connect", None),
        ("peers", {"workspace_id": "a" * 32, "limit": 9, "cursor": None}),
        ("close", None),
    ]


def test_mailbox_send_reads_body_file_stdin_verbatim_and_requires_generation(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MailboxClient()
    monkeypatch.setattr("grove.tui.cli_mailbox.mailbox_client", lambda: client)

    result = runner.invoke(
        app,
        [
            "mailbox",
            "send",
            "c" * 32,
            "--agent",
            "worker",
            "--generation",
            "d" * 32,
            "--body-file",
            "-",
        ],
        input="first line\n\nlast line\n",
    )

    assert result.exit_code == 0, result.output
    request = client.calls[1][1]
    assert request.body == "first line\n\nlast line\n"
    assert request.recipient.agent == "worker"
    assert '"stage": "accepted"' in result.output


def test_mailbox_reply_uses_the_reply_variant(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MailboxClient()
    monkeypatch.setattr("grove.tui.cli_mailbox.mailbox_client", lambda: client)

    result = runner.invoke(app, ["mailbox", "reply", "mbx_" + "e" * 32, "--body", "ack"])

    assert result.exit_code == 0, result.output
    request = client.calls[1][1]
    assert request.kind == "reply"
    assert request.body == "ack"


def test_mailbox_status_uses_the_url_client(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _MailboxClient()
    monkeypatch.setattr("grove.tui.cli_mailbox.mailbox_client", lambda: client)

    result = runner.invoke(app, ["mailbox", "status", "mbx_" + "e" * 32])

    assert result.exit_code == 0, result.output
    assert client.calls[1] == ("status", "mbx_" + "e" * 32)
    assert '"stage": "delivered"' in result.output


def test_mailbox_send_without_bound_token_fails_closed(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROVE_MAILBOX_TOKEN", raising=False)

    result = runner.invoke(
        app,
        ["mailbox", "send", "c" * 32, "--generation", "d" * 32, "--body", "no credential"],
    )

    assert result.exit_code == 1
    assert "GROVE_MAILBOX_TOKEN" in result.output
