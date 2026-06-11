"""Session wire views: listing flattening, the entry-text cap, host-path omission.

Round-trips the `from_*` adapters against hand-built engine dataclasses; the
endpoint behavior lives in tests/daemon/test_sessions_endpoints.py.
"""

from __future__ import annotations

from pathlib import Path

from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    DigestEntry,
    SessionSummary,
    SessionTurn,
)
from grove.core.contracts.sessions import SessionDetailView, SessionSummaryView, SessionTurnView
from grove.core.sessions import SessionListing

SID = "11111111-1111-4111-8111-111111111111"


def _summary() -> SessionSummary:
    return SessionSummary(
        session_id=SID,
        adapter_kind="claude_code",
        transcript_path=Path("/home/someone/.claude/projects/x") / f"{SID}.jsonl",
        cwd="/home/someone/project",
        created_at=None,
        modified_at=None,
        size_bytes=128,
        git_branch="main",
        title="fix the widget",
        first_prompt="please fix",
        last_prompt="thanks",
        activity=AgentActivity(state=AgentActivityState.WAITING, tokens_in=10, tokens_out=5),
    )


def test_summary_view_flattens_listing_and_omits_host_paths() -> None:
    listing = SessionListing(
        summary=_summary(),
        provenance="grove_launched",
        workspace_id="w1",
        workspace_title="Widget",
        workspace_branch="grove/widget",
    )
    view = SessionSummaryView.from_listing(listing)
    assert view.session_id == SID
    assert view.provenance == "grove_launched"
    assert view.workspace_id == "w1"
    assert view.first_prompt == "please fix"
    assert view.activity.state is AgentActivityState.WAITING
    # Host-private paths never cross the wire (views serialize, never expose).
    payload = view.model_dump_json()
    assert "transcript_path" not in payload
    assert "/home/someone" not in payload


def test_turn_view_caps_entry_text() -> None:
    turn = SessionTurn(
        user_text="u" * 10_000,
        entries=(DigestEntry(role="assistant", text="a" * 10_000),),
    )
    view = SessionTurnView.from_turn(turn)
    assert len(view.user_text) <= 4_000
    assert view.user_text.endswith("…")
    assert len(view.entries[0].text) <= 4_000
    assert view.entries[0].text.endswith("…")
    # Short text passes through untouched — the ellipsis is the only trim signal.
    short = SessionTurnView.from_turn(SessionTurn(user_text="hi"))
    assert short.user_text == "hi"


def test_detail_view_composes_session_and_turns() -> None:
    listing = SessionListing(summary=_summary(), provenance="fs_discovered")
    detail = SessionDetailView.from_listing_turns(
        listing,
        (SessionTurn(user_text="hi", entries=(DigestEntry(role="assistant", text="hello"),)),),
    )
    assert detail.session.provenance == "fs_discovered"
    assert detail.session.workspace_id is None
    assert [t.user_text for t in detail.turns] == ["hi"]
    assert detail.turns[0].entries[0].role == "assistant"
