"""Standing ticket watches: a person's change reaches the workspace, Grove's own never does.

Every test here is written from a way the feature could fail its user: a real
change that is never reported, Grove's own sticky comment waking the agent in a
loop, a forge hammered once per workspace, or a watch that outlives its
workspace. The forge is an in-memory fake whose writes move its own next read,
because a fake that only scripts answers cannot catch a feedback loop.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from grove.core.activity import DashboardDelta
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.tickets import TicketComment, TicketRef
from grove.core.contracts.watches import (
    CiPredicate,
    CommandPredicate,
    TicketPredicate,
    TimerPredicate,
    WatchOutcome,
    WatchRegistration,
)
from grove.core.errors import TicketProviderError
from grove.core.issueops.footer import render_footer, splice_footer
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER
from grove.core.tickets.provider import TicketState
from grove.core.tickets.reads import TicketReads
from grove.core.watches.log import WatchLog
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.subscriptions import TicketSubscriptions
from grove.core.watches.ticket import TicketWatcher
from grove.core.watches.watcher import WatcherRegistry
from grove.core.workspace import WorkspaceState, WorkspaceStatus

WS = "c" * 32
BOT = "grove-bot"
T0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Forge:
    """One ticket with real state: every write changes what the next read returns."""

    name = "gitea"
    context = "acme/widgets"

    def __init__(self) -> None:
        self.title = "Fix the parser"
        self.status = "open"
        self.body = "Parser drops the last token."
        self.comments: list[TicketComment] = []
        self.reads = 0
        self._ids = itertools.count(1)

    # the read the watcher and the publisher share
    def read_state(self, ticket_id: str, kind: str) -> TicketState:
        self.reads += 1
        ref = TicketRef(provider="gitea", id=ticket_id, title=self.title, status=self.status)
        return TicketState(ref=ref, body=self.body, comment_count=len(self.comments))

    def list_comments(self, ticket_id: str) -> list[TicketComment]:
        return list(self.comments)

    def viewer_login(self) -> str:
        return BOT

    # writes, by a person or by Grove
    def comment(self, body: str, author: str) -> None:
        self.comments.append(TicketComment(id=str(next(self._ids)), body=body, author=author))

    def grove_sticky_flush(self) -> None:
        """What the status publisher does to a thread: its marked comment, edited again."""
        body = f"status {next(self._ids)}\n{STICKY_MARKER}\n{SIGNATURE_MARKER}"
        sticky = [c for c in self.comments if STICKY_MARKER in c.body]
        if sticky:
            index = self.comments.index(sticky[0])
            self.comments[index] = TicketComment(id=sticky[0].id, body=body, author=BOT)
        else:
            self.comments.append(TicketComment(id=str(next(self._ids)), body=body, author=BOT))

    def grove_footer_write(self) -> None:
        self.body = splice_footer(
            self.body, render_footer(workspace_url="https://grove.example/w/1")
        )


class Courier:
    def __init__(self) -> None:
        self.sent: list[WatchOutcome] = []

    def __call__(self, watch, outcome):
        self.sent.append(outcome)
        return "delivered", None


def _rig(tmp_path, forge: Forge | None = None):
    forge = forge or Forge()
    clock = Clock()
    courier = Courier()
    reads = TicketReads(clock=clock)
    scheduler = WatchScheduler(
        log=WatchLog(tmp_path / "watches.json"),
        watchers=WatcherRegistry([TicketWatcher(lambda _p: forge, reads)]),
        deliver=courier,
        clock=clock,
    )
    scheduler.prime()
    return forge, clock, courier, scheduler


def _watch(scheduler: WatchScheduler, ticket_id: str = "42") -> str:
    return scheduler.register(
        WatchRegistration(
            recipient=MailboxAddress(workspace_id=WS),
            predicate=TicketPredicate(workspace_id=WS, provider="gitea", ticket_id=ticket_id),
        )
    ).id


async def _step(clock: Clock, scheduler: WatchScheduler) -> None:
    """One minute passes and everything due is checked."""
    clock.now += timedelta(minutes=1)
    await scheduler.tick()


async def test_the_state_at_attach_is_recorded_silently_then_changes_are_mailed(tmp_path):
    forge, clock, courier, scheduler = _rig(tmp_path)
    _watch(scheduler)
    await scheduler.tick()
    assert courier.sent == []  # attaching is not news

    forge.status = "closed"
    forge.title = "Fix the tokenizer"
    forge.comment("Reproduced on main, see logs.", author="alice")
    await _step(clock, scheduler)

    [mail] = courier.sent
    assert "State changed from open to closed." in mail.summary
    assert "Title changed from 'Fix the parser' to 'Fix the tokenizer'." in mail.summary
    assert "New comment from alice: Reproduced on main, see logs." in mail.summary


async def test_a_standing_watch_keeps_going_after_it_reports(tmp_path):
    forge, clock, courier, scheduler = _rig(tmp_path)
    watch_id = _watch(scheduler)
    await scheduler.tick()
    forge.status = "closed"
    await _step(clock, scheduler)
    forge.status = "open"
    await _step(clock, scheduler)

    assert [m.summary.splitlines()[1] for m in courier.sent] == [
        "- State changed from open to closed.",
        "- State changed from closed to open.",
    ]
    row = scheduler.list().watches[0]
    assert (row.id, row.state, row.expires_at) == (watch_id, "pending", None)


async def test_groves_own_writes_never_wake_the_workspace(tmp_path):
    """The recursion guard: sticky edits, footer writes and bot comments are silent."""
    forge, clock, courier, scheduler = _rig(tmp_path)
    _watch(scheduler)
    await scheduler.tick()

    for _ in range(3):
        forge.grove_sticky_flush()
        forge.grove_footer_write()
        forge.comment("Opened a pull request for this.", author=BOT)
        await _step(clock, scheduler)

    assert courier.sent == []


async def test_a_human_comment_is_reported_even_beside_groves_own(tmp_path):
    forge, clock, courier, scheduler = _rig(tmp_path)
    _watch(scheduler)
    await scheduler.tick()
    forge.grove_sticky_flush()
    forge.comment("Please also cover the empty input.", author="bob")
    await _step(clock, scheduler)

    [mail] = courier.sent
    assert "New comment from bob" in mail.summary
    assert STICKY_MARKER not in mail.summary


async def test_a_ticket_is_read_at_most_once_a_minute_however_many_watch_it(tmp_path):
    forge, clock, _courier, scheduler = _rig(tmp_path)
    for workspace in ("a" * 32, "b" * 32, "d" * 32):
        scheduler.register(
            WatchRegistration(
                recipient=MailboxAddress(workspace_id=workspace),
                predicate=TicketPredicate(workspace_id=workspace, provider="gitea", ticket_id="42"),
            )
        )
    await scheduler.tick()
    assert forge.reads == 1
    await _step(clock, scheduler)
    assert forge.reads == 2


async def test_an_unreachable_forge_is_never_reported_as_a_change(tmp_path):
    forge, clock, courier, scheduler = _rig(tmp_path)
    _watch(scheduler)
    await scheduler.tick()

    def unreachable(ticket_id, kind):
        raise TicketProviderError("503")

    forge.read_state = unreachable  # type: ignore[method-assign]
    await _step(clock, scheduler)
    assert courier.sent == []
    assert scheduler.list().watches[0].state == "pending"


def _registration(predicate) -> WatchRegistration:
    return WatchRegistration(
        recipient=MailboxAddress(workspace_id=WS),
        predicate=predicate,
        every=timedelta(minutes=1),
    )


def _all_kinds(scheduler: WatchScheduler, clock: Clock) -> list[str]:
    return [
        scheduler.register(
            _registration(
                CiPredicate(
                    provider="github",
                    owner="acme",
                    repo="widgets",
                    head_sha="a" * 40,
                )
            )
        ).id,
        scheduler.register(_registration(CommandPredicate(argv=["true"], workspace_id=WS))).id,
        scheduler.register(_registration(TimerPredicate(at=clock.now + timedelta(hours=1)))).id,
        scheduler.register(
            _registration(TicketPredicate(workspace_id=WS, provider="gitea", ticket_id="42"))
        ).id,
    ]


def _states(scheduler: WatchScheduler, watch_ids: list[str]) -> list[str]:
    rows = {row.id: row for row in scheduler.list().watches}
    return [rows[watch_id].state for watch_id in watch_ids]


def _state(status: WorkspaceStatus, refs: list[TicketRef]) -> WorkspaceState:
    return WorkspaceState(
        id=WS,
        title="t",
        repo_root="/repo",
        worktree_path="/repo/.worktrees/t",
        branch="b",
        base_branch="main",
        tmux_session="grove-t",
        agent_name="claude",
        status=status,
        created_at=T0,
        updated_at=T0,
        ticket_refs=refs,
    )


def test_subscriptions_follow_the_record_attach_detach_pause(tmp_path):
    _forge, _clock, _courier, scheduler = _rig(tmp_path)
    record: dict[str, WorkspaceState | None] = {}
    subs = TicketSubscriptions(scheduler=scheduler, workspaces=lambda _id: record.get("ws"))

    def pending() -> set[str]:
        return {
            row.predicate.ticket_id
            for row in scheduler.list(workspace_id=WS).watches
            if row.state == "pending" and isinstance(row.predicate, TicketPredicate)
        }

    issue, pr = (
        TicketRef(provider="gitea", id="42"),
        TicketRef(provider="gitea", id="43", kind="pull_request"),
    )
    record["ws"] = _state(WorkspaceStatus.RUNNING, [issue, pr])
    subs.reconcile(WS)
    subs.reconcile(WS)  # idempotent: no second row per ticket
    assert pending() == {"42", "43"}
    assert len(scheduler.list(workspace_id=WS).watches) == 2

    record["ws"] = _state(WorkspaceStatus.RUNNING, [pr])
    subs.reconcile(WS)
    assert pending() == {"43"}

    record["ws"] = _state(WorkspaceStatus.PAUSED, [pr])
    subs.reconcile(WS)
    assert pending() == set()

    record["ws"] = None  # killed
    subs.reconcile_all([])
    assert pending() == set()


def test_a_paused_workspace_cancels_every_pending_watch_owned_by_its_recipient(tmp_path):
    """Lifecycle ownership is the callback recipient, not the predicate's subject."""
    _forge, clock, _courier, scheduler = _rig(tmp_path)
    record: dict[str, WorkspaceState | None] = {WS: _state(WorkspaceStatus.PAUSED, [])}
    subscriptions = TicketSubscriptions(scheduler=scheduler, workspaces=record.get)
    watch_ids = _all_kinds(scheduler, clock)

    subscriptions.observe(
        DashboardDelta(
            kind="workspace_changed",
            seq=1,
            workspace_id=WS,
            detail={"event": "paused"},
        )
    )

    assert _states(scheduler, watch_ids) == ["cancelled"] * 4


def test_a_killed_workspace_cancels_every_pending_recipient_owned_watch(tmp_path):
    """A kill delta has no row, so reconciliation must read the now-missing record."""
    _forge, clock, _courier, scheduler = _rig(tmp_path)
    subscriptions = TicketSubscriptions(scheduler=scheduler, workspaces=lambda _id: None)
    watch_ids = _all_kinds(scheduler, clock)

    subscriptions.observe(
        DashboardDelta(
            kind="workspace_changed",
            seq=1,
            workspace_id=WS,
            detail={"event": "killed"},
        )
    )

    assert _states(scheduler, watch_ids) == ["cancelled"] * 4


def test_startup_cancels_all_kinds_for_a_missing_workspace(tmp_path):
    """A deleted record while the daemon was down cannot leave any callback pending."""
    _forge, clock, _courier, scheduler = _rig(tmp_path)
    subscriptions = TicketSubscriptions(scheduler=scheduler, workspaces=lambda _id: None)
    watch_ids = _all_kinds(scheduler, clock)

    subscriptions.reconcile_all([])

    assert _states(scheduler, watch_ids) == ["cancelled"] * 4


def test_resuming_does_not_rearm_watches_cancelled_while_paused(tmp_path):
    """A fresh lifecycle cannot revive a wait that belonged to the paused session."""
    _forge, clock, _courier, scheduler = _rig(tmp_path)
    record: dict[str, WorkspaceState | None] = {WS: _state(WorkspaceStatus.PAUSED, [])}
    subscriptions = TicketSubscriptions(scheduler=scheduler, workspaces=record.get)
    watch_ids = _all_kinds(scheduler, clock)

    subscriptions.reconcile(WS)
    record[WS] = _state(WorkspaceStatus.RUNNING, [])
    subscriptions.reconcile(WS)

    assert _states(scheduler, watch_ids) == ["cancelled"] * 4


def test_detaching_a_ticket_leaves_other_running_workspace_watches_pending(tmp_path):
    """Ticket attachment controls only the standing ticket subscription, never a wait."""
    _forge, clock, _courier, scheduler = _rig(tmp_path)
    record: dict[str, WorkspaceState | None] = {
        WS: _state(WorkspaceStatus.RUNNING, [TicketRef(provider="gitea", id="42")])
    }
    subscriptions = TicketSubscriptions(scheduler=scheduler, workspaces=record.get)
    watch_ids = _all_kinds(scheduler, clock)

    subscriptions.reconcile(WS)
    record[WS] = _state(WorkspaceStatus.RUNNING, [])
    subscriptions.reconcile(WS)

    assert _states(scheduler, watch_ids) == ["pending", "pending", "pending", "cancelled"]


async def test_the_markers_alone_silence_groves_comment_when_the_login_is_unknown(tmp_path):
    """A tracker that cannot say who its token is still must not echo the sticky comment."""

    class Anonymous(Forge):
        def viewer_login(self) -> str:
            raise TicketProviderError("no /user endpoint")

    forge, clock, courier, scheduler = _rig(tmp_path, Anonymous())
    _watch(scheduler)
    await scheduler.tick()
    forge.grove_sticky_flush()
    await _step(clock, scheduler)
    assert courier.sent == []
