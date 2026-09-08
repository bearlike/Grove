"""Peer addressing and receipts, independent of the recipient's native transport."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MailboxGeneration = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
MailboxMessageId = Annotated[str, Field(pattern=r"^mbx_[a-f0-9]{32}$")]
MailboxIntent = Literal["request", "information"]
MailboxStage = Literal["accepted", "queued", "delivered", "unknown", "rejected"]
MailboxReason = Literal[
    "unsupported",
    "not_registered",
    "stale_recipient",
    "sender_not_bound",
    "scope_denied",
    "recipient_refused",
    "recipient_blocked",
    "reply_unavailable",
    "too_large",
    "busy_conflict",
    "backpressure",
    "transport_unknown",
]


class MailboxAddress(BaseModel):
    """A managed agent slot, never a PID, display name or transcript path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    agent: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class MailboxIdentity(BaseModel):
    """An address fenced to one authenticated runtime incarnation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: MailboxAddress
    generation: MailboxGeneration


class MailboxAccess(BaseModel):
    """Grove egress is separate from whether a native inbox accepts input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    can_discover: bool = False
    can_send: bool = False
    can_reply: bool = False
    cli: bool = False
    mcp: bool = False


class MailboxPeer(BaseModel):
    """Public capabilities contain no private transport handle or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: MailboxAddress
    generation: MailboxGeneration | None = None
    display_name: str
    provider: str
    runtime: Literal["host", "container"]
    can_receive: bool | None = None
    access: MailboxAccess = Field(default_factory=MailboxAccess)
    reason: MailboxReason | None = None


class MailboxPeerPage(BaseModel):
    """Caller identity survives filtering and pagination of the peer directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    caller: MailboxIdentity | None = None
    access: MailboxAccess = Field(default_factory=MailboxAccess)
    body_limit_bytes: int = Field(gt=0)
    receipt_ttl_seconds: int = Field(gt=0)
    peers: list[MailboxPeer] = Field(default_factory=list)
    next_cursor: str | None = None


class MailboxBody(BaseModel):
    """Preserve text exactly; the coordinator enforces the configured byte limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    body: str = Field(min_length=1, max_length=1_048_576)

    @field_validator("body")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("body must be valid UTF-8 text") from exc
        if not value.strip():
            raise ValueError("body must contain non-whitespace text")
        return value


class MailboxSendRequest(MailboxBody):
    """Only a fresh send chooses a target; sender identity comes from auth."""

    kind: Literal["send"] = "send"
    recipient: MailboxAddress
    expected_generation: MailboxGeneration
    intent: MailboxIntent = "information"


class MailboxReplyRequest(MailboxBody):
    """Resolve the original sender server-side, without trusting quoted metadata."""

    kind: Literal["reply"] = "reply"
    reply_to: MailboxMessageId


MailboxRequest = Annotated[MailboxSendRequest | MailboxReplyRequest, Field(discriminator="kind")]


class MailboxReceipt(BaseModel):
    """A transport observation is neither model compliance nor human consent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: MailboxMessageId | None = None
    reply_to: MailboxMessageId | None = None
    sender: MailboxIdentity | None = None
    recipient: MailboxIdentity | None = None
    stage: MailboxStage
    reason: MailboxReason | None = None
    native_evidence: str | None = None
    disposition: Literal["held", "refused", "expired", "dropped"] | None = None
    created_at: datetime
    expires_at: datetime | None = None
