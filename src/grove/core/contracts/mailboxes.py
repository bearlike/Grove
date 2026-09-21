"""Contacts and addressed mail, independent of how a recipient is delivered to.

Grove is a trusted single-user environment, so a contact is any live managed
agent and a message is addressed like email: ``sender`` and ``recipient``, a
``subject``, a ``body``. There is no enrollment, no per-peer credential and no
caller-supplied generation — knowing an address is enough to write to it, and
liveness is resolved at delivery rather than handed out in advance.

**``sender`` is DECLARED, never proven.** It says who the writer claims to be so
a reply has somewhere to go, and the rendered footer states it as a claim. A
reader must not treat it as authorization, and Grove never does: peer mail
arrives on the same channel a human types into, under the ``<grove-mailbox>``
fence that exists precisely to say *another agent is talking and Grove only
carried the bytes*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MailboxMessageId = Annotated[str, Field(pattern=r"^mbx_[a-f0-9]{32}$")]

# Why a delivery SUBMITTED to a terminal is not a delivery READ by an agent, and
# why the vocabulary stays this small: `delivered` means Grove handed the bytes
# to the recipient's transport, `rejected` means it refused before that, and
# `unknown` means the transport neither confirmed nor refused. Nothing here
# claims a model read or acted on the text, and no stage may ever be minted to
# suggest otherwise.
MailboxStage = Literal["delivered", "rejected", "unknown"]

# Refusal reasons, one per thing a writer can actually do about it: the
# recipient is not live (respawn or resume it), the body is too large (send
# less), or the transport failed (the workspace's own error says why).
MailboxReason = Literal["not_live", "too_large", "transport_failed"]


class MailboxAddress(BaseModel):
    """A managed agent slot, never a PID, display name or transcript path.

    ``agent`` is the empty string for a workspace's own primary agent and names
    an additional in-container agent otherwise — the same slot vocabulary
    ``peek_pane(agent=)`` and ``send_message(agent=)`` already take.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    agent: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class MailboxContact(BaseModel):
    """One addressable agent, as the directory sees it right now.

    ``live`` is the whole admission rule: a contact that is not live cannot be
    delivered to, and every other field is description. It is resolved per
    listing rather than stored, so a paused workspace stops being writable the
    moment it pauses without anything having to invalidate a registration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: MailboxAddress
    display_name: str
    provider: str
    runtime: Literal["host", "container"]
    live: bool


class MailboxDirectory(BaseModel):
    """Every contact this host can currently deliver to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[2] = 2
    body_limit_bytes: int = Field(gt=0)
    contacts: list[MailboxContact] = Field(default_factory=list)


class MailboxSendRequest(BaseModel):
    """One addressed message. The writer names both ends.

    ``in_reply_to`` correlates a reply for a reader; it never routes one, so a
    conversation survives a receipt expiring or the daemon restarting. That is
    the whole reason replying is an ordinary send with the addresses swapped
    rather than a second verb over a server-held receipt.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sender: MailboxAddress
    recipient: MailboxAddress
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=1_048_576)
    in_reply_to: MailboxMessageId | None = None

    @field_validator("subject", "body")
    @classmethod
    def non_blank(cls, value: str) -> str:
        """Whitespace-only text is an empty message wearing a costume."""
        if not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value


class MailboxReceipt(BaseModel):
    """What Grove observed about ONE submission, and nothing more.

    A ``delivered`` receipt means the recipient's transport accepted the bytes.
    It is not evidence that a model read them, agreed with them, or acted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: MailboxMessageId | None = None
    sender: MailboxAddress | None = None
    recipient: MailboxAddress | None = None
    stage: MailboxStage
    reason: MailboxReason | None = None
    detail: str | None = None
    created_at: datetime
