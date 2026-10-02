"""The phase reminder reaches a working agent with a stale phase, and nobody else.

Every exclusion test starts from the one scenario that DOES remind and changes
exactly one fact, so each guard is the only thing standing between that fixture
and a reminder: deleting the guard fails its test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from grove.core.agents.model import AgentActivityState
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.watches import PhaseNudgePredicate, WatchView
from grove.core.instructions import GroveInstruction
from grove.core.phase import PhaseReport
from grove.core.watches import (
    MailboxWatchCourier,
    NudgeEvidence,
    PhaseNudgeWatcher,
    StandingWatches,
    WatcherRegistry,
    WatchLog,
    WatchScheduler,
)
from grove.core.workspace import WorkspaceState, WorkspaceStatus

WS = "d" * 32
T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
EVERY = timedelta(minutes=10)


def _report(*, phase: str = "build", at: datetime = T0, **kw: object) -> PhaseReport:
    return PhaseReport(phase=phase, updated_at=at, **kw)  # type: ignore[arg-type]


def _working(progress: int = 20, **kw: object) -> NudgeEvidence:
    """A live, working agent whose phase was written at T0."""
    base = NudgeEvidence(
        status=WorkspaceStatus.ACTIVE,
        activity=AgentActivityState.WORKING,
        progress=progress,
        phase=_report(),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


# The previous check, one interval ago, saw the same phase at progress 10.
_BASELINE = PhaseNudgePredicate(workspace_id=WS, progress=10, phase_at=T0)
_NOW = T0 + EVERY


def _decide(seen: NudgeEvidence, predicate: PhaseNudgePredicate = _BASELINE):
    return PhaseNudgeWatcher.decide(predicate, seen, now=_NOW, every=EVERY)


def test_a_working_agent_with_a_stale_phase_is_reminded_once_and_briefly() -> None:
    seen = _decide(_working())
    assert seen is not None and seen.outcome is not None
    assert "not from the user" in seen.outcome.summary
    assert "grove phase" in seen.outcome.summary
    assert len(seen.outcome.summary) < 400  # a transcript line, not a document
    # The report it reminded about is recorded, so the next interval stays quiet
    # however long the agent keeps working on the same phase.
    assert seen.predicate.nudged_at == T0
    again = _decide(_working(progress=30), seen.predicate)  # type: ignore[arg-type]
    assert again is None or again.outcome is None


@pytest.mark.parametrize(
    "status",
    [
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.ERROR,
        WorkspaceStatus.ORPHANED,
        WorkspaceStatus.PROVISIONING,
        # The raw persisted intent is never what a live session reconciles to.
        WorkspaceStatus.RUNNING,
    ],
)
def test_a_workspace_that_is_not_live_is_never_reminded(status: WorkspaceStatus) -> None:
    seen = _decide(_working(status=status))
    assert seen is None or seen.outcome is None


@pytest.mark.parametrize(
    "activity",
    [
        AgentActivityState.IDLE,
        AgentActivityState.WAITING,
        AgentActivityState.BLOCKED,
        AgentActivityState.ERROR,
        AgentActivityState.STARTING,
        AgentActivityState.UNKNOWN,
        None,  # no session at all
    ],
)
def test_an_agent_that_is_not_working_is_never_reminded(
    activity: AgentActivityState | None,
) -> None:
    seen = _decide(_working(activity=activity))
    assert seen is None or seen.outcome is None


def test_a_long_tool_call_is_not_mistaken_for_working_on() -> None:
    """Progress stood still for the interval: one long tool call, or a stall."""
    seen = _decide(_working(progress=10))
    assert seen is None or seen.outcome is None


def test_an_agent_that_never_reported_is_not_reminded() -> None:
    seen = _decide(_working(phase=None))
    assert seen is None or seen.outcome is None


def test_a_seeded_scope_claim_is_not_a_report() -> None:
    """Grove seeds bare `scope` for attached tickets; the agent wrote nothing."""
    seen = _decide(_working(phase=_report(phase="scope")))
    assert seen is None or seen.outcome is None
    # The same phase with the agent's own note IS a report.
    noted = _decide(_working(phase=_report(phase="scope", note="reading the parser")))
    assert noted is not None and noted.outcome is not None


@pytest.mark.parametrize(
    "report",
    [_report(phase="handoff"), _report(phase="verify", blocked=True)],
)
def test_a_handed_off_or_blocked_task_is_never_reminded(report: PhaseReport) -> None:
    seen = _decide(_working(phase=report))
    assert seen is None or seen.outcome is None


def test_a_phase_younger_than_the_interval_is_left_alone() -> None:
    fresh = _report(at=_NOW - timedelta(minutes=3))
    baseline = _BASELINE.model_copy(update={"phase_at": fresh.updated_at})
    seen = _decide(_working(phase=fresh), baseline)
    assert seen is None or seen.outcome is None


def test_the_first_look_only_records_a_baseline() -> None:
    """With no previous check there is no interval to have made progress in."""
    seen = _decide(_working(), PhaseNudgePredicate(workspace_id=WS))
    assert seen is not None and seen.outcome is None
    assert seen.predicate.progress == 20


def test_a_new_report_re_arms_the_reminder() -> None:
    """One reminder per REPORT: writing a new phase makes the next stall remindable."""
    reminded = _BASELINE.model_copy(update={"nudged_at": T0})
    newer = _report(phase="verify", at=T0 + timedelta(minutes=1))
    seen = PhaseNudgeWatcher.decide(
        reminded, _working(phase=newer), now=newer.updated_at + EVERY, every=EVERY
    )
    assert seen is not None and seen.outcome is not None


# ─── the backbone, end to end ───────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class _Delivery:
    """Stands in for MailboxDelivery; records which road each send took."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.peer = 0

    def notify(self, address: MailboxAddress, text: str) -> tuple[str, str | None]:
        self.notes.append(text)
        return "delivered", None

    def send(self, request: object) -> object:  # pragma: no cover - must not be reached
        self.peer += 1
        raise AssertionError("a reminder must not travel as peer mail")


def _state(status: WorkspaceStatus) -> WorkspaceState:
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
    )


async def test_the_reminder_rides_the_standing_watch_backbone_end_to_end(tmp_path) -> None:
    clock = _Clock()
    evidence = {"seen": _working(progress=1)}
    delivery = _Delivery()
    scheduler = WatchScheduler(
        log=WatchLog(tmp_path / "watches.json"),
        watchers=WatcherRegistry([PhaseNudgeWatcher(lambda _id: evidence["seen"], every=EVERY)]),
        deliver=MailboxWatchCourier(delivery),  # type: ignore[arg-type]
        clock=clock,
    )
    scheduler.prime()
    record = {WS: _state(WorkspaceStatus.RUNNING)}
    standing = StandingWatches(scheduler=scheduler, workspaces=record.get, phase_nudge_every=EVERY)

    standing.reconcile(WS)
    standing.reconcile(WS)  # idempotent
    rows = [r for r in scheduler.list(workspace_id=WS).watches if r.state == "pending"]
    assert len(rows) == 1 and rows[0].expires_at is None  # standing: no deadline

    await scheduler.tick()  # first look: baseline only
    assert delivery.notes == []

    clock.now += EVERY
    evidence["seen"] = _working(progress=9)
    await scheduler.tick()
    assert len(delivery.notes) == 1
    note = delivery.notes[0]
    assert GroveInstruction.is_reminder(note)
    assert note.startswith('<grove-instruction kind="reminder">')

    clock.now += EVERY
    evidence["seen"] = _working(progress=15)
    await scheduler.tick()
    assert len(delivery.notes) == 1  # same report: never nagged twice
    assert delivery.peer == 0

    record[WS] = _state(WorkspaceStatus.PAUSED)
    standing.reconcile(WS)
    states = {r.state for r in scheduler.list(workspace_id=WS).watches}
    assert states == {"cancelled"}


def test_disabling_the_reminder_cancels_a_held_watch(tmp_path) -> None:
    scheduler = WatchScheduler(
        log=WatchLog(tmp_path / "watches.json"),
        watchers=WatcherRegistry([PhaseNudgeWatcher(lambda _id: None, every=EVERY)]),
        deliver=lambda _w, _o: ("delivered", None),
    )
    record = {WS: _state(WorkspaceStatus.RUNNING)}
    enabled = StandingWatches(scheduler=scheduler, workspaces=record.get, phase_nudge_every=EVERY)
    enabled.reconcile(WS)
    StandingWatches(scheduler=scheduler, workspaces=record.get).reconcile(WS)
    assert {r.state for r in scheduler.list(workspace_id=WS).watches} == {"cancelled"}


def test_the_reminder_fence_round_trips() -> None:
    text = GroveInstruction.reminder("Update your phase.")
    assert GroveInstruction.is_reminder(text)
    assert GroveInstruction.unwrap(text) == "Update your phase."
    assert not GroveInstruction.is_reminder("please remind me to update the phase")
    assert not GroveInstruction.is_reminder(GroveInstruction.wrap("attachments", "x"))


def test_the_watch_view_accepts_the_new_kind() -> None:
    WatchView.model_validate(
        {
            "id": "wch_" + "0" * 32,
            "recipient": {"workspace_id": WS},
            "predicate": {"kind": "phase_nudge", "workspace_id": WS},
            "state": "pending",
            "created_at": T0,
            "expires_at": None,
        }
    )
