"""The mail wire shapes: addressed, bounded, and honest about what it observed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxContact,
    MailboxDirectory,
    MailboxReceipt,
    MailboxSendRequest,
)

WORKSPACE = "a" * 32
OTHER = "b" * 32


def _request(**overrides: object) -> MailboxSendRequest:
    payload: dict[str, object] = {
        "sender": MailboxAddress(workspace_id=WORKSPACE),
        "recipient": MailboxAddress(workspace_id=OTHER),
        "subject": "Ready for review",
        "body": "The PR is open.",
    }
    payload.update(overrides)
    return MailboxSendRequest.model_validate(payload)


def test_a_send_names_both_ends_and_needs_no_generation() -> None:
    """Knowing an address is enough — there is no token or fence to quote."""
    request = _request()

    assert request.sender.workspace_id == WORKSPACE
    assert request.recipient.workspace_id == OTHER
    assert request.in_reply_to is None
    assert "generation" not in MailboxSendRequest.model_fields


@pytest.mark.parametrize("field", ["subject", "body"])
def test_whitespace_only_text_is_refused(field: str) -> None:
    """An empty message wearing a costume is still an empty message."""
    with pytest.raises(ValidationError):
        _request(**{field: "   \n\t "})


def test_a_reply_correlates_but_does_not_route() -> None:
    """`in_reply_to` is for the reader; the addresses alone decide delivery."""
    original = _request()
    reply = _request(
        sender=original.recipient,
        recipient=original.sender,
        subject="Re: Ready for review",
        in_reply_to="mbx_" + "c" * 32,
    )

    assert reply.recipient == original.sender
    assert reply.in_reply_to is not None


def test_an_agent_slot_is_part_of_the_address() -> None:
    """A second agent in a container is addressable, not a special case."""
    address = MailboxAddress(workspace_id=WORKSPACE, agent="reviewer")

    assert address.agent == "reviewer"
    assert MailboxAddress(workspace_id=WORKSPACE).agent == ""


def test_a_contact_states_whether_it_can_be_written_to() -> None:
    directory = MailboxDirectory(
        body_limit_bytes=1024,
        contacts=[
            MailboxContact(
                address=MailboxAddress(workspace_id=WORKSPACE),
                display_name="review the parser",
                provider="codex",
                runtime="host",
                live=False,
            )
        ],
    )

    assert directory.contacts[0].live is False
    assert directory.protocol_version == 2


def test_a_receipt_cannot_claim_more_than_submission() -> None:
    """The vocabulary has no member meaning "the agent read it"."""
    receipt = MailboxReceipt(
        message_id="mbx_" + "d" * 32,
        sender=MailboxAddress(workspace_id=WORKSPACE),
        recipient=MailboxAddress(workspace_id=OTHER),
        stage="delivered",
        created_at=datetime.now(UTC),
    )

    assert receipt.stage == "delivered"
    # `model_copy` skips validation, so the refusal has to be asserted where
    # one actually runs — otherwise this guard passes with the literal absent.
    with pytest.raises(ValidationError):
        MailboxReceipt.model_validate({**receipt.model_dump(mode="json"), "stage": "read"})
