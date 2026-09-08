"""Durable ticket-history capture at the state-store mutation seam."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.contracts.tickets import TicketRef
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.core.workspace_history import WorkspaceHistoryStore

_AT = datetime(2026, 9, 15, 12, tzinfo=UTC)


def _state(*, refs: list[TicketRef] | None = None) -> WorkspaceState:
    return WorkspaceState(
        id="workspace-1",
        title="Persist ticket transitions",
        repo_root="/repo",
        branch="history-transitions",
        base_branch="main",
        worktree_path="/repo/.worktrees/history-transitions",
        tmux_session="grove-history-transitions",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=_AT,
        updated_at=_AT,
        ticket_refs=refs or [],
    )


def _store(tmp_path: Path) -> tuple[JsonWorkspaceStore, WorkspaceHistoryStore]:
    history = WorkspaceHistoryStore(tmp_path / "history.sqlite3")
    return JsonWorkspaceStore(tmp_path / "state.json", history=history), history


def test_save_captures_ticket_attach_once_and_replay_does_not_advance_last_seen(
    tmp_path: Path,
) -> None:
    store, history = _store(tmp_path)
    attached = _state(refs=[TicketRef(provider="gitea", id="769")])

    store.save(attached)
    first = history.history_for(attached.id).tickets[0]
    store.save(attached)
    replayed = history.history_for(attached.id).tickets[0]

    assert replayed.ticket_key == "gitea:769"
    assert replayed.first_seen == first.first_seen
    assert replayed.last_seen == first.last_seen


def test_save_records_ticket_kind_transition_once_without_touching_unchanged_ref(
    tmp_path: Path,
) -> None:
    store, history = _store(tmp_path)
    issue = _state(refs=[TicketRef(provider="gitea", id="769", kind="issue")])
    changed = replace(
        issue,
        ticket_refs=[TicketRef(provider="gitea", id="769", kind="pull_request")],
        updated_at=_AT + timedelta(seconds=1),
    )

    store.save(issue)
    before = history.history_for(issue.id).tickets[0]
    store.save(changed)
    transitioned = history.history_for(issue.id).tickets[0]
    store.save(changed)
    replayed = history.history_for(issue.id).tickets[0]

    assert transitioned.kind == "pull_request"
    assert transitioned.first_seen == before.first_seen
    assert transitioned.last_seen >= before.last_seen
    assert replayed.last_seen == transitioned.last_seen


def test_record_tickets_replay_does_not_write_or_advance_last_seen(tmp_path: Path) -> None:
    history = WorkspaceHistoryStore(tmp_path / "history.sqlite3")
    ref = TicketRef(provider="gitea", id="769")

    assert history.record_tickets("workspace-1", [ref], now=_AT)
    first = history.history_for("workspace-1").tickets[0]
    assert not history.record_tickets("workspace-1", [ref], now=_AT + timedelta(minutes=1))
    replayed = history.history_for("workspace-1").tickets[0]

    assert replayed.last_seen == first.last_seen


def test_detaching_a_ticket_preserves_its_history_after_kill(tmp_path: Path) -> None:
    store, history = _store(tmp_path)
    attached = _state(refs=[TicketRef(provider="gitea", id="769")])
    detached = replace(attached, ticket_refs=[], updated_at=_AT + timedelta(seconds=1))

    store.save(attached)
    store.save(detached)
    store.delete(detached.id)

    recorded = history.history_for(detached.id)
    assert recorded.name is not None
    assert recorded.name.deleted_at is not None
    assert [ticket.ticket_key for ticket in recorded.tickets] == ["gitea:769"]
