"""Wire shapes for a bounded fleet digest and batch mail.

Everything here is shaped by one constraint: a fleet question must cost one
BOUNDED read, and an instruction to a group must cost one call with one honest
receipt per recipient.

**The digest is a projection, never a second source of truth.** Every row is
read off the activity projection the dashboard already maintains; nothing here
rescans git or transcripts. Missing data stays ``None`` rather than becoming a
successful zero, because "the agent did not report" and "the agent reported
nothing" are answers a reader acts on differently.

**A batch is many single sends, not a new transport.** Each recipient goes
through the same validation, lifecycle lock and delivery as
``MailboxSendRequest``, and gets the same ``MailboxReceipt``. There is no
``all`` wildcard: the caller freezes the recipient list before sending, so the
meaning of a batch cannot change while it is being delivered.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grove.core.contracts.mailboxes import MailboxAddress, MailboxMessageId, MailboxReceipt

WorkspaceId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]

#: The digest's own attention vocabulary, ordered most to least urgent. A row
#: carries every flag that applies; the order is only how a reader should sort.
DigestFlag = Literal["question", "blocked", "error", "offline", "quiet", "handoff"]


class FleetDigestRow(BaseModel):
    """One workspace, small enough that fifty fit in a model turn.

    ``agent`` is the primary session's blended state (``working``, ``waiting``,
    ``idle``, ``blocked``, ``error``, ``unknown``), the same word the dashboard
    shows. ``phase`` and ``phase_note`` are what the agent CLAIMS; ``status`` and
    ``agent`` are what Grove OBSERVED. A reader must keep the two apart.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: WorkspaceId
    title: str
    repo_name: str
    status: str
    agent: str | None = None
    session_id: str | None = None
    phase: str | None = None
    phase_note: str | None = None
    phase_blocked: bool = False
    phase_updated_at: datetime | None = None
    todo_done: int | None = None
    todo_total: int | None = None
    current_task: str | None = None
    question_ids: list[str] = Field(default_factory=list)
    tickets: list[str] = Field(default_factory=list)
    subagents_active: int | None = None
    last_event_at: datetime | None = None
    observed_at: datetime
    flags: list[DigestFlag] = Field(default_factory=list)


class FleetDigestTotals(BaseModel):
    """Counts over the WHOLE selection, not just the returned page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspaces: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_agent: dict[str, int] = Field(default_factory=dict)
    by_phase: dict[str, int] = Field(default_factory=dict)
    by_flag: dict[str, int] = Field(default_factory=dict)


class FleetDigestView(BaseModel):
    """One bounded page of the fleet, plus totals over all of it.

    ``cursor`` is an opaque token naming the projection generation this page was
    read from. Pass it back as ``since`` to receive only rows whose facts
    changed after it, plus ``removed`` for workspaces that left the
    selection. ``resync_required`` means the cursor belongs to an earlier daemon
    run or a different selection: discard what you held and read from scratch.
    ``next_page`` continues this same read; it is not a change cursor.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    scope: list[WorkspaceId] | None = None
    totals: FleetDigestTotals
    rows: list[FleetDigestRow] = Field(default_factory=list)
    removed: list[WorkspaceId] = Field(default_factory=list)
    degraded_repos: list[str] = Field(default_factory=list)
    cursor: str
    next_page: str | None = None
    resync_required: bool = False


class MailboxBatchRequest(BaseModel):
    """The same message to an explicit, frozen list of recipients.

    ``request_id`` makes a retry safe: resending the same id with the same
    payload returns the recorded receipts instead of delivering again, and the
    same id with a different payload is refused.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    sender: MailboxAddress
    recipients: list[MailboxAddress] = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=1_048_576)
    in_reply_to: MailboxMessageId | None = None

    @field_validator("subject", "body")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value

    @field_validator("recipients")
    @classmethod
    def unique(cls, value: list[MailboxAddress]) -> list[MailboxAddress]:
        """Duplicates collapse in order: one recipient, one delivery, one receipt."""
        return list(dict.fromkeys(value))


class MailboxBatchReceipt(BaseModel):
    """One receipt per unique recipient, in request order. Partial success is normal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    receipts: list[MailboxReceipt]
    replayed: bool = False
    """True when this answer was recorded by an earlier call with the same id."""


#: What a journal row recorded. Only batches are written; ``voice`` rows from an
#: earlier build stay readable because the journal is durable.
DelegationKind = Literal["batch", "voice"]


__all__ = [
    "DelegationKind",
    "DigestFlag",
    "FleetDigestRow",
    "FleetDigestTotals",
    "FleetDigestView",
    "MailboxBatchReceipt",
    "MailboxBatchRequest",
]
