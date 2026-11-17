"""The fleet digest and batch mail.

Each test changes the single fact that should flip the answer, so a test that
reaches the right answer through the wrong clause cannot pass. Only the
projection and the mailbox transport are in-memory doubles.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grove.core.activity import (
    DashboardSnapshot,
    FleetSummary,
    ProjectGroup,
    SessionActivity,
    TodoProgress,
    WorkspaceActivity,
)
from grove.core.agents.model import AgentActivity, AgentActivityState, AgentQuestion, AgentSession
from grove.core.config import FleetDigestConfig
from grove.core.contracts.fleet import MailboxBatchRequest
from grove.core.contracts.mailboxes import MailboxAddress, MailboxReceipt, MailboxSendRequest
from grove.core.contracts.tickets import TicketRef
from grove.core.delegations import DelegationJournal
from grove.core.fleet_digest import (
    BatchConflict,
    DigestCursorInvalid,
    FleetDigest,
    MailboxBatch,
)
from grove.core.phase import PhaseReport
from grove.core.workspace import WorkspaceState, WorkspaceStatus

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
READER = "f" * 32
A, B, C = "a" * 32, "b" * 32, "c" * 32


# ─── fixtures: projection rows built from the real dataclasses ──────────────


def _state(workspace_id: str, **over: Any) -> WorkspaceState:
    fields: dict[str, Any] = {
        "id": workspace_id,
        "title": f"ws-{workspace_id[:4]}",
        "repo_root": "/repos/grove",
        "worktree_path": f"/repos/grove/.worktrees/{workspace_id[:4]}",
        "branch": "grove/x",
        "base_branch": "main",
        "tmux_session": f"grove-{workspace_id[:4]}",
        "agent_name": "claude",
        "status": WorkspaceStatus.ACTIVE,
        "created_at": NOW - timedelta(hours=2),
        "updated_at": NOW - timedelta(hours=2),
    }
    fields.update(over)
    return WorkspaceState(**fields)


def _session(
    *,
    state: AgentActivityState = AgentActivityState.WORKING,
    session_id: str = "s1",
    questions: tuple[AgentQuestion, ...] = (),
    tokens_in: int = 100,
    replies: int = 3,
    task: str | None = "wiring the parser",
    last_event_at: datetime = NOW - timedelta(seconds=5),
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id=session_id,
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(
            state=state,
            current_task=task,
            assistant_replies=replies,
            tool_calls=replies,
            tokens_in=tokens_in,
            questions=questions,
            last_event_at=last_event_at,
        ),
    )


def _phase(phase: str = "build", *, ago: timedelta = timedelta(minutes=5), **over: Any):
    return PhaseReport.model_validate(
        {"phase": phase, "note": "wiring", "updated_at": NOW - ago, **over}
    )


def _row(workspace_id: str, **over: Any) -> WorkspaceActivity:
    state_over = over.pop("state_over", {})
    fields: dict[str, Any] = {
        "state": _state(workspace_id, **state_over),
        "sessions": (_session(),),
        "base_ahead": 0,
        "base_behind": 0,
        "diff_added": 0,
        "diff_removed": 0,
        "dirty_files": 0,
        "pane_target": None,
        "recent_commits": (),
        "observed_at": NOW,
        "phase": _phase(),
        "todo": TodoProgress(total=4, completed=1, in_progress=1, pending=2),
    }
    fields.update(over)
    return WorkspaceActivity(**fields)


def _question(qid: str = "q1") -> AgentQuestion:
    return AgentQuestion(id=qid, group_id="g1", kind="free_text", prompt="Which schema?")


class _Projection:
    """The maintained projection a test moves between reads."""

    def __init__(self, *rows: WorkspaceActivity, error_repo: str | None = None) -> None:
        self.rows = list(rows)
        self.seq = 1
        self.error_repo = error_repo

    def set(self, *rows: WorkspaceActivity) -> None:
        self.rows = list(rows)
        self.seq += 1

    def __call__(self) -> tuple[DashboardSnapshot, int]:
        groups = [
            ProjectGroup(
                repo_root="/repos/grove",
                repo_name="grove",
                cwd="/repos/grove",
                workspaces=tuple(self.rows),
            )
        ]
        if self.error_repo:
            groups.append(
                ProjectGroup(
                    repo_root=f"/repos/{self.error_repo}",
                    repo_name=self.error_repo,
                    cwd=f"/repos/{self.error_repo}",
                    workspaces=(),
                    error="config did not parse",
                )
            )
        return DashboardSnapshot(projects=tuple(groups), generated_at=NOW), self.seq


def _digest(projection: _Projection, *, page_size: int | None = None, **config: Any) -> FleetDigest:
    digest = FleetDigest(
        snapshot=projection,
        config=FleetDigestConfig(**config),
        clock=lambda: NOW,
        boot_id="boot1",
    )
    if page_size is not None:
        digest.PAGE_SIZE = page_size  # type: ignore[misc]
    return digest


# ─── rows, flags and the key ────────────────────────────────────────────────


def test_a_row_carries_claims_and_observations_and_caps_the_task_line() -> None:
    row = _row(
        A,
        sessions=(_session(task="x" * 500, questions=(_question(),)),),
        fleet=FleetSummary(active=2, total=3),
        state_over={"ticket_refs": [TicketRef(provider="gitea", id="42")]},
    )

    out = FleetDigest.row_of(row, repo_name="grove", now=NOW, quiet_minutes=30)

    assert (out.status, out.agent, out.session_id) == ("active", "working", "s1")
    assert (out.phase, out.phase_note, out.phase_blocked) == ("build", "wiring", False)
    assert (out.todo_done, out.todo_total) == (1, 4)
    assert out.tickets == ["gitea:42"] and out.question_ids == ["q1"]
    assert out.subagents_active == 2
    assert out.current_task is not None and len(out.current_task) == 200
    assert out.current_task.endswith("…")
    assert out.flags == ["question"]


def test_missing_data_stays_missing_rather_than_a_zero() -> None:
    out = FleetDigest.row_of(
        _row(A, sessions=(), phase=None, todo=None), repo_name="grove", now=NOW, quiet_minutes=30
    )
    assert (out.agent, out.session_id, out.phase, out.todo_total) == (None, None, None, None)
    assert out.subagents_active is None and out.flags == []


@pytest.mark.parametrize(
    ("over", "flag"),
    [
        ({"sessions": (_session(questions=(_question(),)),)}, "question"),
        ({"phase": _phase(blocked=True)}, "blocked"),
        ({"sessions": (_session(state=AgentActivityState.BLOCKED),)}, "blocked"),
        ({"sessions": (_session(state=AgentActivityState.ERROR),)}, "error"),
        ({"state_over": {"status": WorkspaceStatus.ERROR}}, "error"),
        ({"state_over": {"status": WorkspaceStatus.OFFLINE}}, "offline"),
        ({"phase": _phase(ago=timedelta(minutes=31))}, "quiet"),
        ({"phase": _phase("handoff")}, "handoff"),
    ],
)
def test_each_flag_is_raised_by_its_own_fact(over: dict[str, Any], flag: str) -> None:
    assert flag not in FleetDigest.flags_of(_row(A), now=NOW, quiet_minutes=30)
    assert flag in FleetDigest.flags_of(_row(A, **over), now=NOW, quiet_minutes=30)


def test_quiet_needs_a_working_agent_not_just_an_old_phase() -> None:
    """Phase age alone is not a failure: an idle agent with an old report is not quiet."""
    old = _phase(ago=timedelta(hours=3))
    idle = _row(A, phase=old, sessions=(_session(state=AgentActivityState.IDLE),))
    assert "quiet" not in FleetDigest.flags_of(idle, now=NOW, quiet_minutes=30)
    assert "quiet" in FleetDigest.flags_of(_row(A, phase=old), now=NOW, quiet_minutes=30)


def test_flags_come_out_in_triage_order() -> None:
    row = _row(
        A,
        phase=_phase("handoff", blocked=True),
        sessions=(_session(questions=(_question(),)),),
        state_over={"status": WorkspaceStatus.OFFLINE},
    )
    assert FleetDigest.flags_of(row, now=NOW, quiet_minutes=30) == (
        "question",
        "blocked",
        "offline",
        "handoff",
    )


def test_ticking_counters_and_timestamps_do_not_move_the_key() -> None:
    """A hundred token ticks must not count as a change."""
    base = _row(A)
    ticked = _row(
        A,
        sessions=(_session(tokens_in=99_999, replies=40, last_event_at=NOW, task="something new"),),
        observed_at=NOW + timedelta(minutes=9),
        diff_added=120,
        dirty_files=7,
    )
    assert FleetDigest.key_of(base) == FleetDigest.key_of(ticked)


def test_pane_activity_alone_does_not_move_the_key() -> None:
    """ACTIVE and IDLE track pane output, which moves on every keystroke."""
    idle = _row(A, state_over={"status": WorkspaceStatus.IDLE})
    assert FleetDigest.key_of(_row(A)) == FleetDigest.key_of(idle)


@pytest.mark.parametrize(
    "over",
    [
        {"state_over": {"status": WorkspaceStatus.OFFLINE}},
        {"sessions": (_session(state=AgentActivityState.WAITING),)},
        {"sessions": (_session(session_id="s2"),)},
        {"sessions": (_session(questions=(_question(),)),)},
        {"phase": _phase("verify")},
        {"phase": _phase(blocked=True)},
        {"phase": _phase(ago=timedelta(minutes=1))},  # a re-report is a real signal
        {"todo": TodoProgress(total=4, completed=2, in_progress=1, pending=1)},
        {"state_over": {"ticket_refs": [TicketRef(provider="gitea", id="7")]}},
    ],
)
def test_every_reader_fact_moves_the_key(over: dict[str, Any]) -> None:
    assert FleetDigest.key_of(_row(A)) != FleetDigest.key_of(_row(A, **over))


# ─── reads: selection, paging, since, resync ────────────────────────────────


def test_a_read_orders_by_id_and_totals_the_whole_selection() -> None:
    projection = _Projection(
        _row(C), _row(A), _row(B, phase=_phase(blocked=True)), error_repo="broken"
    )
    view = _digest(projection, page_size=2).read().view

    assert [r.workspace_id for r in view.rows] == [A, B]
    assert view.totals.workspaces == 3  # the page is two; the totals are all three
    assert view.totals.by_flag == {"blocked": 1}
    assert view.totals.by_status == {"active": 3}
    assert view.degraded_repos == ["broken"]
    assert view.next_page is not None and view.resync_required is False


def test_paging_continues_the_same_read_even_after_the_projection_moved() -> None:
    projection = _Projection(_row(A), _row(B), _row(C))
    digest = _digest(projection, page_size=2)
    first = digest.read(consumer=READER)
    projection.set(_row(A))  # B and C vanish mid-read

    second = digest.read(consumer=READER, page=first.view.next_page)

    assert [r.workspace_id for r in second.view.rows] == [C]
    assert second.view.next_page is None and second.observed is None
    assert second.view.cursor == first.view.cursor


def test_since_returns_only_changed_rows_and_removals() -> None:
    projection = _Projection(_row(A), _row(B), _row(C))
    digest = _digest(projection)
    first = digest.read(consumer=READER).view
    projection.set(_row(A, sessions=(_session(tokens_in=10**6),)), _row(B, phase=_phase("verify")))

    delta = digest.read(consumer=READER, since=first.cursor).view

    assert [r.workspace_id for r in delta.rows] == [B]  # A only ticked tokens
    assert delta.removed == [C]
    assert delta.resync_required is False
    assert delta.totals.workspaces == 2


def test_a_cursor_from_another_run_or_selection_resyncs_with_a_full_page() -> None:
    projection = _Projection(_row(A), _row(B))
    digest = _digest(projection)
    cursor = digest.read(consumer=READER).view.cursor

    restarted = FleetDigest(
        snapshot=projection, config=FleetDigestConfig(), clock=lambda: NOW, boot_id="boot2"
    )
    after_restart = restarted.read(consumer=READER, since=cursor).view
    narrowed = digest.read(consumer=READER, since=cursor, workspaces=[A]).view

    assert after_restart.resync_required and len(after_restart.rows) == 2
    assert narrowed.resync_required and [r.workspace_id for r in narrowed.rows] == [A]


def test_an_older_cursor_of_the_same_consumer_resyncs() -> None:
    """Only the last-issued cursor has a baseline; an older one would diff wrongly."""
    projection = _Projection(_row(A))
    digest = _digest(projection)
    old = digest.read(consumer=READER).view.cursor
    projection.set(_row(A, phase=_phase("verify")))
    digest.read(consumer=READER)

    assert digest.read(consumer=READER, since=old).view.resync_required


def test_consumers_keep_separate_positions() -> None:
    """One reader's read must not advance another reader's change baseline."""
    projection = _Projection(_row(A))
    digest = _digest(projection)
    cursor = digest.read(consumer=READER).view.cursor
    projection.set(_row(A, phase=_phase("verify")))
    digest.read()  # anonymous

    delta = digest.read(consumer=READER, since=cursor).view
    assert [r.workspace_id for r in delta.rows] == [A]


@pytest.mark.parametrize("token", ["%%%", "bm90LWpzb24", "eyJhIjogMX0"])
def test_a_token_this_daemon_never_issued_is_refused(token: str) -> None:
    digest = _digest(_Projection(_row(A)))
    with pytest.raises(DigestCursorInvalid):
        digest.read(since=token)
    with pytest.raises(DigestCursorInvalid):
        digest.read(page=token)


# ─── batch mail ─────────────────────────────────────────────────────────────


class _Mailbox:
    """A MailboxDelivery double: refuses whoever it is told is not live."""

    def __init__(self, *, offline: set[str] | None = None, broken: set[str] | None = None) -> None:
        self.offline = offline or set()
        self.broken = broken or set()
        self.sent: list[MailboxSendRequest] = []
        self.in_flight = 0
        self.peak = 0

    def send(self, request: MailboxSendRequest) -> MailboxReceipt:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(0.01)
            recipient = request.recipient.workspace_id
            if recipient in self.broken:
                raise RuntimeError("socket closed")
            self.sent.append(request)
            stage = "rejected" if recipient in self.offline else "delivered"
            return MailboxReceipt(
                sender=request.sender,
                recipient=request.recipient,
                stage=stage,
                reason="not_live" if stage == "rejected" else None,
                created_at=NOW,
            )
        finally:
            self.in_flight -= 1


class _Locks:
    def __init__(self) -> None:
        self.held: list[str] = []
        self.active: set[str] = set()
        self.max_active = 0

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        self.held.append(key)
        self.active.add(key)
        self.max_active = max(self.max_active, len(self.active))
        try:
            yield
        finally:
            self.active.discard(key)


def _batch(
    mailbox: _Mailbox,
    locks: _Locks | None = None,
    *,
    concurrency: int | None = None,
    journal: DelegationJournal | None = None,
) -> MailboxBatch:
    # The default journal resolves `paths.delegations_path`, which the autouse
    # isolation fixture points at a fresh per-test directory.
    batch = MailboxBatch(
        mailbox,  # type: ignore[arg-type]
        hold=(locks or _Locks()).hold,
        journal=journal or DelegationJournal(),
        clock=lambda: NOW,
    )
    if concurrency is not None:
        batch.CONCURRENCY = concurrency  # type: ignore[misc]
    return batch


def _request(
    *recipients: str, request_id: str = "req-0001", body: str = "Rebase on main."
) -> MailboxBatchRequest:
    return MailboxBatchRequest(
        request_id=request_id,
        sender=MailboxAddress(workspace_id=READER),
        recipients=[MailboxAddress(workspace_id=r) for r in recipients],
        subject="Rebase",
        body=body,
    )


async def test_a_batch_is_one_receipt_per_recipient_in_order_with_partial_success() -> None:
    mailbox, locks = _Mailbox(offline={B}, broken={C}), _Locks()
    receipt = await _batch(mailbox, locks).send(_request(C, A, B))

    assert [(r.recipient.workspace_id, r.stage) for r in receipt.receipts] == [
        (C, "unknown"),
        (A, "delivered"),
        (B, "rejected"),
    ]
    assert receipt.receipts[0].reason == "transport_failed"
    assert sorted(locks.held) == sorted([A, B, C])
    assert receipt.replayed is False


async def test_duplicate_recipients_collapse_to_one_delivery() -> None:
    mailbox = _Mailbox()
    receipt = await _batch(mailbox).send(_request(A, B, A))
    assert [r.recipient.workspace_id for r in receipt.receipts] == [A, B]
    assert len(mailbox.sent) == 2


async def test_a_retry_with_the_same_id_and_payload_replays_without_sending() -> None:
    mailbox = _Mailbox()
    batch = _batch(mailbox)
    first = await batch.send(_request(A, B))
    again = await batch.send(_request(A, B))

    assert again.replayed is True and again.receipts == first.receipts
    assert len(mailbox.sent) == 2


async def test_the_same_id_with_a_different_payload_is_a_conflict() -> None:
    batch = _batch(_Mailbox())
    await batch.send(_request(A))
    with pytest.raises(BatchConflict):
        await batch.send(_request(A, body="Something else."))
    with pytest.raises(BatchConflict):
        await batch.send(_request(A, B))


async def test_a_concurrent_duplicate_waits_for_the_first_send_rather_than_sending_twice() -> None:
    mailbox = _Mailbox()
    batch = _batch(mailbox)
    first, second = await asyncio.gather(batch.send(_request(A, B)), batch.send(_request(A, B)))
    assert len(mailbox.sent) == 2
    assert sorted([first.replayed, second.replayed]) == [False, True]


async def test_delivery_concurrency_is_bounded() -> None:
    mailbox = _Mailbox()
    ids = [f"{i:032x}" for i in range(1, 11)]
    await _batch(mailbox, concurrency=2).send(_request(*ids))
    assert len(mailbox.sent) == 10
    assert mailbox.peak <= 2


# ─── the journal: idempotency that survives a restart ───────────────────────


async def test_a_retry_after_a_restart_replays_from_the_journal_and_never_redelivers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "delegations.sqlite3"
    first_mailbox = _Mailbox(offline={B})
    first = await _batch(first_mailbox, journal=DelegationJournal(path)).send(_request(A, B))

    # A new process: new batch, new journal object, same file, empty memory.
    mailbox = _Mailbox()
    again = await _batch(mailbox, journal=DelegationJournal(path)).send(_request(A, B))

    assert mailbox.sent == []
    assert again.replayed is True
    assert [(r.recipient.workspace_id, r.stage, r.reason) for r in again.receipts] == [
        (A, "delivered", None),
        (B, "rejected", "not_live"),
    ]
    assert [r.created_at for r in again.receipts] == [r.created_at for r in first.receipts]


async def test_a_changed_payload_after_a_restart_is_still_a_conflict(tmp_path: Path) -> None:
    path = tmp_path / "delegations.sqlite3"
    await _batch(_Mailbox(), journal=DelegationJournal(path)).send(_request(A))

    mailbox = _Mailbox()
    with pytest.raises(BatchConflict):
        await _batch(mailbox, journal=DelegationJournal(path)).send(
            _request(A, body="Something else.")
        )
    assert mailbox.sent == []


async def test_a_recipient_with_no_recorded_receipt_replays_as_unknown_and_is_not_resent(
    tmp_path: Path,
) -> None:
    """The crash gap: the request was recorded, B's send never returned a receipt."""
    journal = DelegationJournal(tmp_path / "delegations.sqlite3")
    request = _request(A, B)
    journal.record_request(
        request_id=request.request_id,
        kind="batch",
        sender=request.sender,
        digest=MailboxBatch.digest(request),
        subject=request.subject,
        body=request.body,
        recipients=request.recipients,
        created_at=NOW,
    )
    journal.record_receipt(
        request.request_id,
        MailboxReceipt(
            sender=request.sender,
            recipient=request.recipients[0],
            stage="delivered",
            created_at=NOW,
        ),
    )

    mailbox = _Mailbox()
    replay = await _batch(mailbox, journal=journal).send(request)

    assert mailbox.sent == []
    assert replay.replayed is True
    assert [(r.recipient.workspace_id, r.stage) for r in replay.receipts] == [
        (A, "delivered"),
        (B, "unknown"),
    ]
    assert replay.receipts[1].reason == "transport_failed"
    assert replay.receipts[1].detail == MailboxBatch.UNRECORDED


async def test_a_journal_that_cannot_record_the_request_sends_nothing(tmp_path: Path) -> None:
    class _Refusing(DelegationJournal):
        def record_request(self, **_: Any) -> None:
            raise OSError("disk full")

    mailbox = _Mailbox()
    batch = _batch(mailbox, journal=_Refusing(tmp_path / "d.sqlite3"))
    with pytest.raises(OSError):
        await batch.send(_request(A))
    assert mailbox.sent == []


def test_the_payload_digest_ignores_the_request_id() -> None:
    assert MailboxBatch.digest(_request(A, request_id="req-aaaa")) == MailboxBatch.digest(
        _request(A, request_id="req-bbbb")
    )
    assert MailboxBatch.digest(_request(A)) != MailboxBatch.digest(_request(B))


def test_a_replaced_dataclass_still_keys_identically() -> None:
    """The key is over values, so an equal row built twice keys the same."""
    assert FleetDigest.key_of(_row(A)) == FleetDigest.key_of(replace(_row(A)))


# ─── guards that need their own fixture (each survived a mutation without one) ──


def test_an_answered_question_is_not_a_pending_question() -> None:
    answered = replace(_question("q1"), answered=True)
    row = _row(A, sessions=(_session(questions=(answered, _question("q2"))),))
    assert FleetDigest.row_of(row, repo_name="grove", now=NOW, quiet_minutes=30).question_ids == [
        "q2"
    ]
    only_answered = _row(A, sessions=(_session(questions=(answered,)),))
    assert "question" not in FleetDigest.flags_of(only_answered, now=NOW, quiet_minutes=30)


def test_a_page_token_from_a_replaced_read_restarts_rather_than_paging_the_new_one() -> None:
    """The page token names ONE read; a newer read by the same consumer replaces it."""
    projection = _Projection(_row(A), _row(B), _row(C))
    digest = _digest(projection, page_size=1)
    stale_page = digest.read(consumer=READER).view.next_page
    digest.read(consumer=READER)  # replaces the read the token belonged to

    restarted = digest.read(consumer=READER, page=stale_page).view
    assert restarted.resync_required
    assert [r.workspace_id for r in restarted.rows] == [A]


def test_removals_are_reported_once_per_read_not_once_per_page() -> None:
    projection = _Projection(_row(A), _row(B), _row(C))
    digest = _digest(projection, page_size=1)
    cursor = digest.read(consumer=READER).view.cursor
    projection.set(_row(A, phase=_phase("verify")), _row(B, phase=_phase("verify")))

    first = digest.read(consumer=READER, since=cursor).view
    second = digest.read(consumer=READER, page=first.next_page).view

    assert first.removed == [C] and second.removed == []
