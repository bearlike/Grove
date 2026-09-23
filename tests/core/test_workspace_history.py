"""Contract tests for the durable workspace-history store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PhaseReport, TaskPhase, TicketClaim
from grove.core.workspace_history import WorkspaceHistoryStore

_AT = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _store(tmp_path: Path) -> WorkspaceHistoryStore:
    return WorkspaceHistoryStore(tmp_path / "h.sqlite3")


def _report(
    *,
    phase: TaskPhase = "build",
    blocked: bool = False,
    note: str | None = None,
    tickets: tuple[TicketClaim, ...] = (),
) -> PhaseReport:
    return PhaseReport(
        phase=phase,
        blocked=blocked,
        note=note,
        updated_at=_AT,
        tickets=tickets,
    )


def test_record_progress_deduplicates_note_less_workspace_and_ticket_claims(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = _report(
        tickets=(TicketClaim(ticket="gitea:687", phase="build"),),
    )

    for _ in range(500):
        store.record_progress("workspace-1", report, now=_AT)

    progress = store.history_for("workspace-1").progress
    assert len(progress) == 2
    assert {(entry.ticket_key, entry.phase, entry.note) for entry in progress} == {
        (None, "build", None),
        ("gitea:687", "build", None),
    }


def test_record_progress_appends_each_content_transition(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reports = (
        _report(),
        _report(phase="verify"),
        _report(blocked=True),
        _report(note="waiting for review"),
    )

    for offset, report in enumerate(reports):
        store.record_progress("workspace-1", report, now=_AT + timedelta(seconds=offset))

    progress = store.history_for("workspace-1").progress
    assert {(entry.phase, entry.blocked, entry.note) for entry in progress} == {
        ("build", False, None),
        ("verify", False, None),
        ("build", True, None),
        ("build", False, "waiting for review"),
    }


def test_record_name_unchanged_pair_updates_last_seen_without_history_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_seen = _AT
    later_seen = _AT + timedelta(minutes=1)

    store.record_name(
        "workspace-1",
        title="Build history",
        description="Keep workspace facts",
        now=first_seen,
    )
    store.record_name(
        "workspace-1",
        title="Build history",
        description="Keep workspace facts",
        now=later_seen,
    )

    history = store.history_for("workspace-1")
    assert len(history.names) == 1
    assert history.name is not None
    assert history.name.first_seen == first_seen
    assert history.name.last_seen == later_seen


def test_record_name_keeps_two_subsecond_renames(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_name("workspace-1", title="Untitled", description=None, now=_AT)
    store.record_name("workspace-1", title="Import audit", description=None, now=_AT)

    history = store.history_for("workspace-1")
    assert {change.title for change in history.names} == {"Untitled", "Import audit"}
    assert len(history.names) == 2
    assert history.name is not None
    assert history.name.title == "Import audit"


def test_mark_deleted_tombstones_known_workspace_without_losing_its_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    deleted_at = _AT + timedelta(minutes=1)
    store.record_name("workspace-1", title="Release work", description="Ship it", now=_AT)
    store.record_progress("workspace-1", _report(note="ready"), now=_AT)
    store.record_tickets(
        "workspace-1",
        [TicketRef(provider="gitea", id="687")],
        now=_AT,
    )

    store.mark_deleted("workspace-1", now=deleted_at)
    store.mark_deleted("unknown-workspace", now=deleted_at)

    history = store.history_for("workspace-1")
    assert history.name is not None
    assert history.name.deleted_at == deleted_at
    assert history.name.title == "Release work"
    assert history.name.description == "Ship it"
    assert [change.title for change in history.names] == ["Release work"]
    assert [(entry.phase, entry.note) for entry in history.progress] == [("build", "ready")]
    assert [ticket.ticket_key for ticket in history.tickets] == ["gitea:687"]
    assert store.history_for("unknown-workspace").is_empty


def test_record_tickets_updates_kind_without_replacing_first_seen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first_seen = _AT
    last_seen = _AT + timedelta(minutes=1)

    store.record_tickets(
        "workspace-1",
        [TicketRef(provider="gitea", id="687", kind="issue")],
        now=first_seen,
    )
    store.record_tickets(
        "workspace-1",
        [TicketRef(provider="gitea", id="687", kind="pull_request")],
        now=last_seen,
    )

    tickets = store.history_for("workspace-1").tickets
    assert len(tickets) == 1
    assert tickets[0].ticket_key == "gitea:687"
    assert tickets[0].kind == "pull_request"
    assert tickets[0].first_seen == first_seen
    assert tickets[0].last_seen == last_seen


def test_history_is_empty_until_a_workspace_fact_is_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.history_for("workspace-1").is_empty

    store.record_name("workspace-1", title="Known workspace", description=None, now=_AT)

    assert not store.history_for("workspace-1").is_empty


def test_record_progress_round_trips_missing_note_and_workspace_ticket_key_as_none(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.record_progress(
        "workspace-1",
        _report(tickets=(TicketClaim(ticket="gitea:687", phase="plan"),)),
        now=_AT,
    )

    progress = store.history_for("workspace-1").progress
    workspace_claim = next(entry for entry in progress if entry.ticket_key is None)
    ticket_claim = next(entry for entry in progress if entry.ticket_key == "gitea:687")
    assert workspace_claim.note is None
    assert workspace_claim.ticket_key is None
    assert ticket_claim.note is None


def test_unopenable_store_is_best_effort_for_every_operation(tmp_path: Path) -> None:
    database_directory = tmp_path / "not-a-database.sqlite3"
    database_directory.mkdir()
    store = WorkspaceHistoryStore(database_directory)
    report = _report()
    ticket = TicketRef(provider="gitea", id="687")

    store.record_name("workspace-1", title="No disk", description=None, now=_AT)
    store.record_progress("workspace-1", report, now=_AT)
    store.record_tickets("workspace-1", [ticket], now=_AT)
    store.mark_deleted("workspace-1", now=_AT)

    assert store.history_for("workspace-1").is_empty


def test_names_for_returns_only_known_requested_workspace_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_name("workspace-1", title="One", description=None, now=_AT)
    store.record_name("workspace-2", title="Two", description=None, now=_AT)

    assert store.names_for(()) == {}
    names = store.names_for(["workspace-1", "missing-workspace"])
    assert set(names) == {"workspace-1"}
    assert names["workspace-1"].title == "One"
