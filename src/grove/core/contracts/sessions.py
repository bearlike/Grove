"""Wire shapes for agent-session exploration — Pydantic mirrors of the explorer IR.

The daemon serializes ``SessionExplorer`` output through these (``GET
/workspaces/{id}/sessions`` and ``.../sessions/{sid}/turns``). Session history
is **fetch-on-demand only**: these shapes are never embedded in the SSE
``DashboardEvent`` — turns are unbounded where the live dashboard payload must
stay small. Same ``from_*`` + ``frozen=True`` pattern as ``activity.py``, with
the engine dataclasses imported under ``TYPE_CHECKING`` only.

``transcript_path`` and the host ``cwd``'s parent layout stay off the wire by
the views-never-expose rule — clients identify a session by id, not by a
host-private path.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from grove.core.contracts.activity import AgentActivityView
from grove.core.contracts.questions import (
    _ENTRY_TEXT_CAP,
    AgentQuestionView,
    _truncate,
)

if TYPE_CHECKING:
    from grove.core.agents import DigestEntry, SessionTurn
    from grove.core.sessions import SessionListing


class DigestEntryView(BaseModel):
    """Wire mirror of ``grove.core.agents.DigestEntry`` (text capped).

    ``question`` is set only for ``role=="question"`` — the structured choice
    payload; every other role leaves it ``None`` and reads from ``text``.
    """

    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant", "tool", "summary", "status", "notification", "question"]
    text: str
    question: AgentQuestionView | None = None

    @classmethod
    def from_entry(cls, e: DigestEntry) -> DigestEntryView:
        return cls(
            role=e.role,
            text=_truncate(e.text, _ENTRY_TEXT_CAP),
            question=AgentQuestionView.from_question(e.question) if e.question else None,
        )


class SessionTurnView(BaseModel):
    """Wire mirror of ``grove.core.agents.SessionTurn``.

    ``user_text`` is empty for a leading continuation block (a resumed or
    compacted session's head) — same convention as the engine dataclass.
    """

    model_config = ConfigDict(frozen=True)

    user_text: str
    started_at: datetime | None
    entries: list[DigestEntryView]

    @classmethod
    def from_turn(cls, t: SessionTurn) -> SessionTurnView:
        return cls(
            user_text=_truncate(t.user_text, _ENTRY_TEXT_CAP),
            started_at=t.started_at,
            entries=[DigestEntryView.from_entry(e) for e in t.entries],
        )


class SessionSummaryView(BaseModel):
    """Wire mirror of ``grove.core.sessions.SessionListing`` — one session row.

    Flattens the listing's ``SessionSummary`` plus its project annotation.
    ``activity`` reuses the dashboard's ``AgentActivityView`` — the explorer's
    one parse per transcript yields both metadata and metrics, so the wire
    carries them together too. The ``workspace_*`` trio is ``None`` for a
    hand-staged session (a directory Grove doesn't manage).
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    adapter_kind: str
    provenance: str
    workspace_id: str | None
    workspace_title: str | None
    workspace_branch: str | None
    git_branch: str | None
    created_at: datetime | None
    modified_at: datetime | None
    size_bytes: int
    title: str | None
    first_prompt: str | None
    last_prompt: str | None
    activity: AgentActivityView

    @classmethod
    def from_listing(cls, ls: SessionListing) -> SessionSummaryView:
        s = ls.summary
        return cls(
            session_id=s.session_id,
            adapter_kind=s.adapter_kind,
            provenance=ls.provenance,
            workspace_id=ls.workspace_id,
            workspace_title=ls.workspace_title,
            workspace_branch=ls.workspace_branch,
            git_branch=s.git_branch,
            created_at=s.created_at,
            modified_at=s.modified_at,
            size_bytes=s.size_bytes,
            title=s.title,
            first_prompt=s.first_prompt,
            last_prompt=s.last_prompt,
            activity=AgentActivityView.from_activity(s.activity),
        )


class SessionDetailView(BaseModel):
    """One session with its conversation — the turns endpoint's response."""

    model_config = ConfigDict(frozen=True)

    session: SessionSummaryView
    turns: list[SessionTurnView]

    @classmethod
    def from_listing_turns(
        cls, listing: SessionListing, turns: tuple[SessionTurn, ...]
    ) -> SessionDetailView:
        return cls(
            session=SessionSummaryView.from_listing(listing),
            turns=[SessionTurnView.from_turn(t) for t in turns],
        )
