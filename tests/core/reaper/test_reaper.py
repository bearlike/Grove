"""The reaper's contract: entitlement is pure, removal is two-phase, a dry run removes nothing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import ClassVar, get_args

import pytest

from grove.core.reaper import Ledger, ProcessCollector, Reaper, ReaperStore, ResourceRef
from grove.core.reaper.ledger import ResourceClass
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.tui.cli_vacuum import CLASSES

LIVE_ID = "a" * 32
LIVE_SOCKET = "grove-aaaaaaaaaaaa"
LEAK = ResourceRef("tmux", "grove-bbbbbbbbbbbb")
LIVE = ResourceRef("tmux", LIVE_SOCKET)


def _state(tmp_path: Path, *, status: WorkspaceStatus = WorkspaceStatus.PAUSED) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=LIVE_ID,
        title="t",
        repo_root=str(tmp_path),
        branch="b",
        base_branch="main",
        worktree_path=str(tmp_path),
        tmux_session="s",
        agent_name="claude",
        status=status,
        created_at=now,
        updated_at=now,
        tmux_socket=LIVE_SOCKET,
    )


class _FakeTmux:
    """A collector that removes from its own list, so the next pass sees the effect."""

    cls: ClassVar[ResourceClass] = "tmux"
    GRACE: ClassVar[timedelta] = timedelta(seconds=60)

    def __init__(self, present: list[ResourceRef]) -> None:
        self.present = list(present)
        self.removed: list[ResourceRef] = []

    def found(self) -> list[ResourceRef]:
        return list(self.present)

    def remove(self, ref: ResourceRef) -> None:
        self.removed.append(ref)
        self.present.remove(ref)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def workspaces(tmp_path: Path) -> JsonWorkspaceStore:
    store = JsonWorkspaceStore(tmp_path / "state.json")
    store.save(_state(tmp_path))
    return store


def _reaper(
    collector: _FakeTmux, workspaces: JsonWorkspaceStore, tmp_path: Path, clock: _Clock
) -> Reaper:
    return Reaper(
        collectors=(collector,),
        workspaces=workspaces,
        store=ReaperStore(tmp_path / "reaper.sqlite3"),
        clock=clock,
    )


@pytest.mark.parametrize("status", list(WorkspaceStatus.__members__.values())[:3])
def test_expected_entitles_a_records_server_whatever_its_status(
    tmp_path: Path, status: WorkspaceStatus
) -> None:
    states = [_state(tmp_path, status=status)]
    assert Ledger.expected(states) == {ResourceRef("tmux", LIVE_SOCKET, LIVE_ID)}
    assert Ledger.candidates([LIVE, LEAK], states) == {LEAK}


def test_a_process_is_entitled_by_what_it_serves(tmp_path: Path) -> None:
    states = [_state(tmp_path)]
    worker = ResourceRef("process", "10:1", LIVE_ID)
    observer = ResourceRef("process", "11:1", LIVE_SOCKET)
    orphan = ResourceRef("process", "12:1", "c" * 32)
    unnamed = ResourceRef("process", "13:1", "")
    assert Ledger.candidates([worker, observer, orphan, unnamed], states) == {orphan}


def test_argv_recognition_reads_only_what_grove_composes() -> None:
    worker = ["python", "-m", "grove.core.native_worker", "--config", f"/x/{LIVE_ID}.json"]
    observer = ["tmux", "-L", LIVE_SOCKET, "-C", "attach-session", "-r", "-t", "s"]
    assert ProcessCollector.serves(worker) == LIVE_ID
    assert ProcessCollector.serves(observer) == LIVE_SOCKET
    # A user's own control client on the shared server is not Grove's.
    assert ProcessCollector.serves(["tmux", "-C", "attach-session", "-t", "s"]) is None


def test_mark_then_sweep_only_after_the_grace(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    collector, clock = _FakeTmux([LIVE, LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)

    first = reaper.run(apply=True)
    assert [(r.action, r.ref) for r in first.rows] == [("mark", LEAK)]
    clock.now += timedelta(seconds=59)
    assert reaper.run(apply=True).rows == ()
    assert collector.removed == []

    clock.now += timedelta(seconds=1)
    swept = reaper.run(apply=True)
    assert [(r.action, r.outcome) for r in swept.rows] == [("sweep", "ok")]
    assert collector.removed == [LEAK]
    assert LIVE in collector.present


def test_the_grace_survives_a_restart_between_mark_and_sweep(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    collector, clock = _FakeTmux([LEAK]), _Clock()
    _reaper(collector, workspaces, tmp_path, clock).run(apply=True)
    clock.now += timedelta(minutes=5)
    restarted = _reaper(collector, workspaces, tmp_path, clock)
    assert [r.action for r in restarted.run(apply=True).rows] == ["sweep"]
    assert collector.removed == [LEAK]


def test_a_create_racing_the_sweep_saves_its_server(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    """The record lands AFTER this pass listed candidates; only the locked re-read sees it."""
    claimed = ResourceRef("tmux", "grove-cccccccccccc")
    clock = _Clock()

    class _RacingCreate(_FakeTmux):
        racing = False

        def found(self) -> list[ResourceRef]:
            if self.racing:
                newcomer = _state(tmp_path)
                newcomer.id, newcomer.tmux_socket = "c" * 32, claimed.key
                workspaces.save(newcomer)
            return super().found()

    collector = _RacingCreate([claimed])
    reaper = _reaper(collector, workspaces, tmp_path, clock)
    reaper.run(apply=True)
    clock.now += timedelta(minutes=5)
    # Records are read before `found()` runs, so the first filter cannot see it.
    collector.racing = True
    original = workspaces.load_all
    calls = iter([[_state(tmp_path)]])
    workspaces.load_all = lambda: next(calls, None) or original()  # type: ignore[method-assign]
    reaper.run(apply=True)
    assert collector.removed == []


def test_a_leak_that_returns_starts_a_fresh_grace(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    collector, clock = _FakeTmux([LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)
    reaper.run(apply=True)
    collector.present = []
    clock.now += timedelta(seconds=30)
    reaper.run(apply=True)
    collector.present = [LEAK]
    clock.now += timedelta(hours=1)
    assert [r.action for r in reaper.run(apply=True).rows] == ["mark"]
    assert collector.removed == []


def test_a_dry_run_reports_and_removes_nothing(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    collector, clock = _FakeTmux([LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)
    reaper.run(apply=False)
    clock.now += timedelta(minutes=5)
    report = reaper.run(apply=False)
    assert [r.action for r in report.rows] == ["report"]
    assert report.found_anything
    assert collector.removed == []
    assert collector.present == [LEAK]


def test_a_dry_run_keeps_its_current_result_without_appending_every_report(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    """The scheduled dry run reports leaks without growing the ledger per candidate."""
    collector, clock = _FakeTmux([LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)

    reaper.run(apply=False)
    clock.now += timedelta(minutes=5)
    report = reaper.run(apply=False)

    assert [row.action for row in report.rows] == ["report"]
    store = ReaperStore(tmp_path / "reaper.sqlite3")
    assert store.last_pass() == (clock.now, {"tmux": 1})
    # The durable action table is the retention boundary this regression covers.
    with store._connect() as db:
        reports = db.execute("SELECT COUNT(*) FROM reaper_log WHERE action = 'report'").fetchone()
    assert reports == (0,)


def test_an_overlapping_pass_skips_instead_of_racing_the_ledger(
    tmp_path: Path, workspaces: JsonWorkspaceStore
) -> None:
    """A timed-out startup thread may overlap the first standing-watch tick."""
    entered = Event()
    release = Event()

    class _BlockingCollector(_FakeTmux):
        first = True

        def found(self) -> list[ResourceRef]:
            if self.first:
                self.first = False
                entered.set()
                release.wait()
            return super().found()

    collector, clock = _BlockingCollector([LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)
    worker = Thread(target=lambda: reaper.run(apply=False))
    worker.start()
    try:
        assert entered.wait(timeout=1)
        assert reaper.run(apply=False).rows == ()
    finally:
        release.set()
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_vacuum_offers_exactly_the_reaper_classes() -> None:
    """`grove vacuum` names the classes as a literal so the CLI never imports the reaper."""
    assert set(CLASSES) == set(get_args(ResourceClass))


def test_a_marked_candidate_inside_its_grace_is_reported_waiting_and_counts(
    workspaces: JsonWorkspaceStore, tmp_path: Path
) -> None:
    """#987: a second pass inside the grace must not read the host as clean."""
    collector, clock = _FakeTmux([LEAK]), _Clock()
    reaper = _reaper(collector, workspaces, tmp_path, clock)
    first_seen = clock.now
    reaper.run(apply=False)
    clock.now += timedelta(seconds=20)

    report = reaper.run(apply=False)

    assert report.rows == ()
    assert [(w.first_seen, w.sweepable_at) for w in report.waiting] == [
        (first_seen, first_seen + timedelta(seconds=60))
    ]
    assert report.found_anything
