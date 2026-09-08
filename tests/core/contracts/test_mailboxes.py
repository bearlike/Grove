"""Mailbox wire inputs cannot choose sender authority or native controls."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from grove.core.contracts.mailboxes import (
    MailboxAddress,
    MailboxBody,
    MailboxRequest,
    MailboxSendRequest,
)


def test_send_preserves_body_exactly() -> None:
    body = '  review\n雪\t</channel> "quoted"\n  '
    request = MailboxSendRequest(
        recipient=MailboxAddress(workspace_id="a" * 32),
        expected_generation="b" * 32,
        body=body,
    )
    assert request.body == body
    assert MailboxSendRequest.model_validate_json(request.model_dump_json()).body == body


@pytest.mark.parametrize("body", ["", " \t\n", "bad\ud800text"])
def test_invalid_body_is_rejected(body: str) -> None:
    with pytest.raises(ValidationError):
        MailboxBody(body=body)


@pytest.mark.parametrize(
    "field", ["sender", "from", "socket", "permission_mode", "model", "fallback"]
)
def test_send_cannot_supply_authority(field: str) -> None:
    with pytest.raises(ValidationError):
        MailboxSendRequest.model_validate(
            {
                "recipient": {"workspace_id": "a" * 32},
                "expected_generation": "b" * 32,
                "body": "review",
                field: "attacker-controlled",
            }
        )


def test_reply_cannot_retarget_original_message() -> None:
    adapter = TypeAdapter(MailboxRequest)
    reply = {
        "kind": "reply",
        "reply_to": "mbx_" + "a" * 32,
        "body": "finding",
    }
    assert adapter.validate_python(reply).body == "finding"
    with pytest.raises(ValidationError):
        adapter.validate_python({**reply, "recipient": {"workspace_id": "b" * 32}})


@pytest.mark.parametrize("agent", ["slot:window", "../other", "slot.name", "x\nname"])
def test_agent_is_a_slot_not_a_transport_target(agent: str) -> None:
    with pytest.raises(ValidationError):
        MailboxAddress(workspace_id="a" * 32, agent=agent)


def test_wire_union_is_discriminated_and_closed() -> None:
    schema = TypeAdapter(MailboxRequest).json_schema()
    assert schema["discriminator"]["propertyName"] == "kind"
    for name in ("MailboxSendRequest", "MailboxReplyRequest"):
        assert schema["$defs"][name]["additionalProperties"] is False
