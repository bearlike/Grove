"""Session wire views: listing flattening, the entry-text cap, host-path omission.

Round-trips the `from_*` adapters against hand-built engine dataclasses; the
endpoint behavior lives in tests/daemon/test_sessions_endpoints.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    AgentQuestion,
    AgentQuestionOption,
    DigestEntry,
    FileEdit,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoItem,
    TodoList,
)
from grove.core.contracts.sessions import SessionDetailView, SessionSummaryView, SessionTurnView
from grove.core.sessions import CatalogEntry, ProjectContext, SessionListing

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


def test_summary_view_flattens_listing_and_omits_the_transcript_path() -> None:
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
    assert view.primary is True  # the explicit pin marker
    assert view.workspace_id == "w1"
    assert view.first_prompt == "please fix"
    assert view.activity is not None
    assert view.activity.state is AgentActivityState.WAITING
    # The transcript's own path stays host-private (views serialize, never
    # expose); the working directory deliberately crosses, because "where did
    # this session happen" is the question a session row exists to answer.
    payload = view.model_dump_json()
    assert "transcript_path" not in payload
    assert ".claude/projects" not in payload
    assert view.cwd == "/home/someone/project"
    # Catalog-only fields stay at their defaults for a project-scoped row.
    assert view.project is None
    assert view.live is False


def _catalog_entry(*, cwd: str | None = "/home/someone/project", **over: object) -> CatalogEntry:
    fields: dict[str, object] = {
        "ref": SessionRef(
            session_id=SID,
            adapter_kind="claude_code",
            cwd=cwd,
            transcript_path=Path("/home/someone/.claude/projects/x") / f"{SID}.jsonl",
            birth=datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            mtime=1_800_000_000.0,
            git_branch="main",
        ),
        "provenance": "fs_discovered",
        "project": ProjectContext(
            repo_root=Path("/home/someone/project"),
            repo_name="project",
            is_worktree=False,
            is_grove_managed=False,
        ),
    }
    fields.update(over)
    return CatalogEntry(**fields)  # type: ignore[arg-type]


def test_catalog_view_reports_unparsed_fields_as_null_not_zero() -> None:
    """A host-scope row is built from one bounded head read, so everything a
    full parse would have produced is honestly absent — a client must be able
    to tell "not measured" from "measured as empty"."""
    view = SessionSummaryView.from_catalog(_catalog_entry())
    assert view.session_id == SID
    assert view.activity is None
    assert view.size_bytes is None
    assert view.title is None
    assert view.first_prompt is None
    assert view.workspace_branch is None
    # ...while everything the head read DID yield is present.
    assert view.git_branch == "main"
    assert view.created_at == datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    assert view.modified_at == datetime.fromtimestamp(1_800_000_000.0, UTC)
    assert view.cwd == "/home/someone/project"
    assert view.project is not None
    assert view.project.repo_name == "project"
    assert "transcript_path" not in view.model_dump_json()


def test_catalog_view_primary_tracks_provenance_and_liveness_rides_through() -> None:
    minted = SessionSummaryView.from_catalog(
        _catalog_entry(
            provenance="grove_launched", workspace_id="w1", workspace_title="W", live=True
        )
    )
    assert minted.primary is True
    assert minted.workspace_id == "w1"
    assert minted.live is True
    assert SessionSummaryView.from_catalog(_catalog_entry()).primary is False


def test_catalog_view_keeps_an_unplaceable_row_rather_than_inventing_a_project() -> None:
    view = SessionSummaryView.from_catalog(_catalog_entry(cwd=None, project=None))
    assert view.cwd is None
    assert view.project is None


def test_catalog_view_maps_a_failed_stat_to_no_timestamp() -> None:
    """``mtime`` is ``0.0`` exactly when the stat failed — a missing value, not
    a session last written in 1970."""
    entry = _catalog_entry()
    view = SessionSummaryView.from_catalog(
        replace(entry, ref=replace(entry.ref, mtime=0.0)),
    )
    assert view.modified_at is None


def test_summary_view_primary_false_for_a_discovered_session() -> None:
    """A non-minted (`fs_discovered`) listing is never the pin — `primary`
    stays `False`, the field's default."""
    listing = SessionListing(summary=_summary(), provenance="fs_discovered")
    assert SessionSummaryView.from_listing(listing).primary is False


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


def test_question_entry_serializes_its_structured_payload() -> None:
    """A ``role=="question"`` entry carries the full ``AgentQuestion`` on the
    wire so the client can render a choice card; other roles carry ``None``."""
    q = AgentQuestion(
        id="call_1#0",
        group_id="call_1",
        kind="single_select",
        prompt="Merge or rebase?",
        header="Strategy",
        options=(
            AgentQuestionOption(label="Merge", description="keep both"),
            AgentQuestionOption(label="Rebase"),
        ),
        multiselect=False,
        answered=True,
        answer="Merge",
        source_tool="AskUserQuestion",
    )
    turn = SessionTurn(
        user_text="",
        entries=(
            DigestEntry(role="question", text="Merge or rebase?", question=q),
            DigestEntry(role="assistant", text="ok"),
        ),
    )
    view = SessionTurnView.from_turn(turn)

    qview = view.entries[0]
    assert qview.role == "question"
    assert qview.question is not None
    assert qview.question.id == "call_1#0"
    assert qview.question.kind == "single_select"
    assert qview.question.answered is True
    assert qview.question.answer == "Merge"
    assert [o.label for o in qview.question.options] == ["Merge", "Rebase"]
    assert qview.question.options[1].description is None
    # A plain entry has no structured payload.
    assert view.entries[1].question is None
    # Round-trips through JSON (the daemon serializes this).
    assert "Merge or rebase?" in view.model_dump_json()


def test_file_edit_path_is_relativized_to_session_cwd() -> None:
    """A ``file_edit`` entry carries both the full path (tooltip target) and a
    ``display_path`` made relative to the session's cwd (the header). An edit
    outside the cwd keeps the full path. The anchor is ``_summary().cwd`` ==
    ``/home/someone/project``, applied by ``from_listing_turns``."""
    listing = SessionListing(summary=_summary(), provenance="fs_discovered")
    detail = SessionDetailView.from_listing_turns(
        listing,
        (
            SessionTurn(
                user_text="edit it",
                entries=(
                    DigestEntry(
                        role="file_edit",
                        text="Edit /home/someone/project/src/app.py",
                        file_edit=FileEdit(
                            path="/home/someone/project/src/app.py",
                            old_text="a",
                            new_text="b",
                        ),
                    ),
                    DigestEntry(
                        role="file_edit",
                        text="Edit /etc/hosts",
                        file_edit=FileEdit(path="/etc/hosts", old_text="a", new_text="b"),
                    ),
                ),
            ),
        ),
    )
    under, outside = detail.turns[0].entries
    assert under.file_edit is not None
    assert under.file_edit.display_path == "src/app.py"  # header form
    assert under.file_edit.path == "/home/someone/project/src/app.py"  # tooltip form
    assert outside.file_edit is not None
    assert outside.file_edit.display_path == "/etc/hosts"  # not under cwd → full path


def test_todo_entry_serializes_the_structured_list() -> None:
    """A ``role="todo"`` entry populates ``DigestEntryView.todo`` with the whole
    normalized checklist (a third structured payload beside ``question``/
    ``file_edit``); a non-todo entry leaves it ``None``."""
    listing = SessionListing(summary=_summary(), provenance="fs_discovered")
    detail = SessionDetailView.from_listing_turns(
        listing,
        (
            SessionTurn(
                user_text="plan it",
                entries=(
                    DigestEntry(
                        role="todo",
                        text="1/2 done · Ship it",
                        todo=TodoList(
                            items=(
                                TodoItem(content="Scope it", status="completed"),
                                TodoItem(
                                    content="Ship it", status="in_progress", active_form="Shipping"
                                ),
                            )
                        ),
                    ),
                    DigestEntry(role="assistant", text="working on it"),
                ),
            ),
        ),
    )
    todo_entry, plain = detail.turns[0].entries
    assert todo_entry.role == "todo"
    assert todo_entry.todo is not None
    assert [(i.content, i.status, i.active_form) for i in todo_entry.todo.items] == [
        ("Scope it", "completed", None),
        ("Ship it", "in_progress", "Shipping"),
    ]
    assert plain.todo is None  # only the todo role carries the payload


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
