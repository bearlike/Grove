"""Wire views for durable workspace history.

The SQLite store holds long-lived records as engine dataclasses; these immutable
views are the authenticated HTTP representation and deliberately expose no
write surface.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from grove.core.workspace_history import (
    NameChange,
    ProgressEntry,
    RecordedTicket,
    WorkspaceHistory,
    WorkspaceName,
)


class RecordedNameView(BaseModel):
    """The most recently recorded name for a workspace, including its tombstone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str
    title: str
    description: str | None
    repo_root: str | None
    first_seen: datetime
    last_seen: datetime
    deleted_at: datetime | None

    @classmethod
    def from_name(cls, name: WorkspaceName) -> RecordedNameView:
        return cls(
            workspace_id=name.workspace_id,
            title=name.title,
            description=name.description,
            repo_root=name.repo_root,
            first_seen=name.first_seen,
            last_seen=name.last_seen,
            deleted_at=name.deleted_at,
        )


class NameChangeView(BaseModel):
    """One title and description a workspace held at a recorded instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    description: str | None
    recorded_at: datetime

    @classmethod
    def from_change(cls, change: NameChange) -> NameChangeView:
        return cls(
            title=change.title,
            description=change.description,
            recorded_at=change.recorded_at,
        )


class ProgressEntryView(BaseModel):
    """One durable workspace or ticket progress claim.

    ``phase`` stays an open string rather than ``TaskPhase``: this is a
    historical record, and an older row carrying a phase a newer binary no
    longer recognises must not make the entire history unreadable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_at: datetime
    phase: str | None
    blocked: bool
    note: str | None
    ticket_key: str | None

    @classmethod
    def from_entry(cls, entry: ProgressEntry) -> ProgressEntryView:
        return cls(
            recorded_at=entry.recorded_at,
            phase=entry.phase,
            blocked=entry.blocked,
            note=entry.note,
            ticket_key=entry.ticket_key,
        )


class RecordedTicketView(BaseModel):
    """One ticket a workspace was attached to, with its observed window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_key: str
    provider: str
    ticket_id: str
    kind: str
    first_seen: datetime
    last_seen: datetime

    @classmethod
    def from_ticket(cls, ticket: RecordedTicket) -> RecordedTicketView:
        return cls(
            ticket_key=ticket.ticket_key,
            provider=ticket.provider,
            ticket_id=ticket.ticket_id,
            kind=ticket.kind,
            first_seen=ticket.first_seen,
            last_seen=ticket.last_seen,
        )


class WorkspaceHistoryView(BaseModel):
    """Everything durably recorded about one workspace, newest first where ordered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: RecordedNameView | None = None
    names: list[NameChangeView] = Field(default_factory=list)
    progress: list[ProgressEntryView] = Field(default_factory=list)
    tickets: list[RecordedTicketView] = Field(default_factory=list)

    @classmethod
    def from_history(cls, history: WorkspaceHistory) -> WorkspaceHistoryView:
        return cls(
            name=RecordedNameView.from_name(history.name) if history.name is not None else None,
            names=[NameChangeView.from_change(change) for change in history.names],
            progress=[ProgressEntryView.from_entry(entry) for entry in history.progress],
            tickets=[RecordedTicketView.from_ticket(ticket) for ticket in history.tickets],
        )
