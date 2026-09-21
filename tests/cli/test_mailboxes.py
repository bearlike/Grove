"""`grove mailbox` — addressing, the sender default, and honest refusals."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxContact,
    MailboxDirectory,
    MailboxReceipt,
    MailboxSendRequest,
)
from grove.core.phase import PhaseFile
from grove.tui import cli_mailbox

WORKSPACE = "a" * 32
OTHER = "b" * 32
runner = CliRunner()


class _FakeClient:
    """Stands in at the HTTP boundary; records exactly what the CLI composed."""

    def __init__(self) -> None:
        self.sent: list[MailboxSendRequest] = []

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def list_mailbox_contacts(self) -> MailboxDirectory:
        return MailboxDirectory(
            body_limit_bytes=65536,
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
        self.sent.append(request)
        return MailboxReceipt(
            message_id="mbx_" + "c" * 32,
            sender=request.sender,
            recipient=request.recipient,
            stage="delivered",
            created_at=datetime.now(UTC),
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(cli_mailbox, "mailbox_client", lambda: fake)
    return fake


def test_contacts_needs_no_credential_and_prints_the_directory(client: _FakeClient) -> None:
    result = runner.invoke(cli_mailbox.mailbox_app, ["contacts"])

    assert result.exit_code == 0, result.output
    assert OTHER in result.output
    assert '"live": true' in result.output


def test_send_defaults_the_sender_to_this_workspace(
    client: _FakeClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one variable Grove already gives every agent, in its own namespace."""
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(tmp_path / PhaseFile.RELDIR / f"{WORKSPACE}.json"))

    result = runner.invoke(
        cli_mailbox.mailbox_app,
        ["send", "--to", OTHER, "--subject", "Ready", "--body", "PR is open."],
    )

    assert result.exit_code == 0, result.output
    assert client.sent[0].sender.workspace_id == WORKSPACE
    assert client.sent[0].recipient.workspace_id == OTHER


def test_an_explicit_sender_always_wins(
    client: _FakeClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default is a convenience; the address is never proven, so it is free."""
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(tmp_path / PhaseFile.RELDIR / f"{WORKSPACE}.json"))

    result = runner.invoke(
        cli_mailbox.mailbox_app,
        ["send", "--from", OTHER, "--to", WORKSPACE, "--subject", "Re", "--body", "ack"],
    )

    assert result.exit_code == 0, result.output
    assert client.sent[0].sender.workspace_id == OTHER


def test_send_without_a_resolvable_sender_refuses_and_names_the_flag(
    client: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing beats guessing: a cwd cannot say which agent is asking."""
    monkeypatch.delenv(PhaseFile.PATH_ENV, raising=False)

    result = runner.invoke(
        cli_mailbox.mailbox_app,
        ["send", "--to", OTHER, "--subject", "Ready", "--body", "PR is open."],
    )

    assert result.exit_code != 0
    assert "--from" in result.output
    assert client.sent == []


def test_agent_slots_reach_the_request(
    client: _FakeClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(tmp_path / PhaseFile.RELDIR / f"{WORKSPACE}.json"))

    result = runner.invoke(
        cli_mailbox.mailbox_app,
        [
            "send",
            "--to",
            OTHER,
            "--to-agent",
            "reviewer",
            "--subject",
            "Ready",
            "--body",
            "PR is open.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.sent[0].recipient.agent == "reviewer"


def test_body_and_body_file_are_mutually_exclusive(
    client: _FakeClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(tmp_path / PhaseFile.RELDIR / f"{WORKSPACE}.json"))

    result = runner.invoke(
        cli_mailbox.mailbox_app,
        ["send", "--to", OTHER, "--subject", "Ready", "--body", "a", "--body-file", "-"],
    )

    assert result.exit_code != 0
    assert client.sent == []
