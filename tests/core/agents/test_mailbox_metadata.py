"""Mailbox identity survives the real parser, turn projection and wire view."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _TranscriptParser
from grove.core.contracts.mailboxes import (
    MailboxAccess,
    MailboxAddress,
    MailboxIdentity,
    MailboxReceipt,
)
from grove.core.contracts.sessions import SessionTurnView
from grove.core.mailboxes import MailboxEnvelope


@pytest.mark.parametrize(
    ("envelope", "sender", "subject", "body"),
    [
        (
            '<teammate-message teammate_id="reviewer" summary="Review">'
            "Full body</teammate-message>",
            "reviewer",
            "Review",
            "Full body",
        ),
        (
            '<cross-session-message from="uds:/tmp/peer.sock" from-name="peer">'
            "Reply body</cross-session-message>",
            "peer",
            None,
            "Reply body",
        ),
        (
            "<task-notification><task-id>task-1</task-id><summary>Complete</summary>"
            "<result>Whole result</result></task-notification>",
            "task-1",
            "Complete",
            "Whole result",
        ),
    ],
)
def test_mailbox_survives_projection(
    tmp_path: Path, envelope: str, sender: str, subject: str | None, body: str
) -> None:
    path = tmp_path / "session.jsonl"
    rows = [
        {
            "type": "user",
            "uuid": "human",
            "timestamp": "2026-09-06T12:00:00Z",
            "message": {"role": "user", "content": "Real human prompt"},
        },
        {
            "type": "user",
            "uuid": "mail",
            "timestamp": "2026-09-06T12:00:01Z",
            "message": {"role": "user", "content": envelope},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    parser = _TranscriptParser(ClaudeCodeAdapter._read((path,)))
    assert parser.activity().human_turns == 1
    turns = parser.turns()
    assert len(turns) == 1
    wire = SessionTurnView.from_turn(turns[0])
    mail = next(entry for entry in wire.entries if entry.mailbox is not None)
    assert mail.role == "notification"
    # Exhaustive on purpose: a field added to the envelope has to be a
    # deliberate decision here rather than arriving by default.
    assert mail.mailbox.model_dump() == {
        "sender": sender,
        "recipient": None,
        "subject": subject,
        "body": body,
        # None of these three is Grove's own mailbox — they are harness
        # envelopes that merely normalize to the same shape, so none may
        # render as an agent-to-agent handoff.
        "kind": "notice",
    }


def test_groves_own_envelope_projects_as_a_peer_handoff(tmp_path: Path) -> None:
    """The case the three fixtures above cannot cover.

    They are all HARNESS envelopes (a teammate relay, a cross-session relay, a
    task notice) and every one normalizes to `kind="notice"`. Grove's own
    mailbox is the only producer of `peer`, and until 2026-09-15 it was not
    recognized here at all: its envelope matched no marker, so it counted as a
    HUMAN TURN and carried no mailbox payload. Both halves are asserted.
    """
    now = datetime.now(UTC)
    receipt = MailboxReceipt(
        message_id="mbx_" + "a" * 32,
        sender=MailboxIdentity(address=MailboxAddress(workspace_id="a" * 32), generation="a" * 32),
        recipient=MailboxIdentity(
            address=MailboxAddress(workspace_id="b" * 32), generation="b" * 32
        ),
        stage="accepted",
        created_at=now,
    )
    body = "Please review PR #42"
    envelope = MailboxEnvelope.render(
        receipt, body, "request", MailboxAccess(cli=True, mcp=True, can_reply=True)
    )
    path = tmp_path / "session.jsonl"
    rows = [
        {
            "type": "user",
            "uuid": "human",
            "timestamp": "2026-09-06T12:00:00Z",
            "message": {"role": "user", "content": "Real human prompt"},
        },
        {
            "type": "user",
            "uuid": "mail",
            "timestamp": "2026-09-06T12:00:01Z",
            "message": {"role": "user", "content": envelope},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    parser = _TranscriptParser(ClaudeCodeAdapter._read((path,)))
    # A delivered peer message is machine traffic, not a person typing.
    assert parser.activity().human_turns == 1
    wire = SessionTurnView.from_turn(parser.turns()[0])
    mail = next(entry for entry in wire.entries if entry.mailbox is not None)
    assert mail.role == "notification"
    assert mail.mailbox.model_dump() == {
        "sender": "a" * 32,
        "recipient": "b" * 32,
        # The envelope carries no subject; the delivery INTENT is the nearest
        # honest thing, and summarizing the body would be Grove paraphrasing a
        # peer in its own voice.
        "subject": "request",
        "body": body,
        "kind": "peer",
    }
