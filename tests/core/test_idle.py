"""Idle suspension (#970): the idle rule, freeze/thaw verbs, and the thaw a message performs."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

from grove.core.activity import QueueDepth, SessionActivity, WorkspaceActivity
from grove.core.agents.model import AgentActivity, AgentActivityState, AgentSession
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.watches import IdlePredicate
from grove.core.idle import IdleRule, IdleWatcher
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
RULE = IdleRule(cooldown=timedelta(minutes=15))


def _state(**changes: object) -> WorkspaceState:
    state = WorkspaceState(
        id="a" * 32,
        title="t",
        repo_root="/repo",
        branch="b",
        base_branch="main",
        worktree_path="/repo/wt",
        tmux_session="s",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(state, **changes)  # type: ignore[arg-type]


def _row(
    agent: AgentActivityState = AgentActivityState.WAITING,
    *,
    quiet_for: timedelta = timedelta(minutes=20),
    state: WorkspaceState | None = None,
    queue: QueueDepth | None = None,
    **activity: object,
) -> WorkspaceActivity:
    session = SessionActivity(
        session=AgentSession(
            session_id="s1",
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",  # type: ignore[arg-type]
        ),
        activity=replace(
            AgentActivity.empty(agent),
            last_event_at=NOW - quiet_for,
            **activity,  # type: ignore[arg-type]
        ),
    )
    return WorkspaceActivity(
        state=state or _state(),
        sessions=(session,),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=NOW,
        queue=queue,
    )


def test_an_agent_at_rest_past_the_cooldown_is_idle() -> None:
    assert RULE.idle(_row(AgentActivityState.WAITING), NOW)
    assert RULE.idle(_row(AgentActivityState.IDLE), NOW)


@pytest.mark.parametrize(
    "row",
    [
        _row(AgentActivityState.WORKING),
        # A pending question is a person's turn, never idle.
        _row(AgentActivityState.BLOCKED),
        _row(quiet_for=timedelta(minutes=5)),
        _row(active_subagents=1),
        _row(queue=QueueDepth(pending=1)),
        _row(state=_state(frozen_at=NOW)),
    ],
    ids=["working", "blocked", "recent", "subagent", "queued", "already-frozen"],
)
def test_anything_in_flight_or_recent_is_not_idle(row: WorkspaceActivity) -> None:
    assert not RULE.idle(row, NOW)


def test_a_thaw_restarts_the_idle_clock() -> None:
    """#1083, from the incident: a woken workspace whose last event predates its
    freeze was frozen again 4 s after the wake. Its event clock is long past the
    cooldown; only the thaw itself is recent."""
    woken = _state(thawed_at=NOW - timedelta(seconds=4))
    assert not RULE.idle(_row(quiet_for=timedelta(hours=1), state=woken), NOW)
    settled = _state(thawed_at=NOW - timedelta(minutes=16))
    assert RULE.idle(_row(quiet_for=timedelta(hours=1), state=settled), NOW)


def test_the_watch_freezes_only_rows_still_idle_when_read_fresh() -> None:
    """A stale projection must not freeze an agent that has since started working."""
    stale_idle, still_idle = _row(), _row(state=_state(id="b" * 32))
    fresh = {stale_idle.state.id: _row(AgentActivityState.WORKING), still_idle.state.id: still_idle}
    frozen: list[str] = []
    watcher = IdleWatcher(
        candidates=lambda: (stale_idle, still_idle),
        fresh=lambda row: fresh[row.state.id],
        freeze=lambda row: frozen.append(row.state.id),
        rule_for=lambda _row: RULE,
    )

    watcher.observe(IdlePredicate(), NOW)

    assert frozen == ["b" * 32]


@pytest.mark.parametrize(
    ("state", "rule", "due"),
    [
        (_state(frozen_at=NOW - timedelta(minutes=16)), timedelta(minutes=15), True),
        (_state(frozen_at=NOW - timedelta(minutes=5)), timedelta(minutes=15), False),
        (_state(), timedelta(minutes=15), False),
        (
            _state(frozen_at=NOW - timedelta(hours=1), hibernated_at=NOW),
            timedelta(minutes=15),
            False,
        ),
        (_state(frozen_at=NOW - timedelta(hours=1)), None, False),
    ],
    ids=["frozen-past-limit", "frozen-recently", "not-frozen", "already-hibernated", "disabled"],
)
def test_hibernation_is_due_only_for_a_workspace_frozen_past_its_limit(
    state: WorkspaceState, rule: timedelta | None, due: bool
) -> None:
    hibernating = IdleRule(cooldown=timedelta(minutes=15), hibernate_after=rule)
    assert hibernating.hibernate(_row(state=state), NOW) is due


def test_the_watch_hibernates_a_long_frozen_row_and_never_refreezes_it() -> None:
    long_frozen = _row(state=_state(frozen_at=NOW - timedelta(hours=1)))
    hibernated: list[str] = []
    frozen: list[str] = []
    watcher = IdleWatcher(
        candidates=lambda: (long_frozen,),
        fresh=lambda row: row,
        freeze=lambda row: frozen.append(row.state.id),
        rule_for=lambda _row: IdleRule(
            cooldown=timedelta(minutes=15), hibernate_after=timedelta(minutes=15)
        ),
        hibernate=lambda row: hibernated.append(row.state.id),
    )

    watcher.observe(IdlePredicate(), NOW)

    assert hibernated == ["a" * 32] and frozen == []


def test_pending_idle_work_never_blocks_or_double_submits_a_later_tick() -> None:
    """A slow hibernate leaves the watch worker free for every other candidate."""
    hibernating = _row(state=_state(frozen_at=NOW - timedelta(hours=1)))
    idle = _row(state=_state(id="b" * 32))
    started = Event()
    release = Event()
    submitted: list[str] = []
    futures: dict[str, Future[None]] = {}

    def hibernate(_row: WorkspaceActivity) -> None:
        started.set()
        assert release.wait(1)

    def submit(workspace_id: str, action: Callable[[], None]) -> Future[None]:
        future: Future[None] = Future()
        futures[workspace_id] = future
        submitted.append(workspace_id)

        def run() -> None:
            try:
                action()
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(None)

        if workspace_id == hibernating.state.id:
            Thread(target=run).start()
        return future

    watcher = IdleWatcher(
        candidates=lambda: (hibernating, idle),
        fresh=lambda row: row,
        freeze=lambda _row: None,
        rule_for=lambda _row: IdleRule(
            cooldown=timedelta(minutes=15), hibernate_after=timedelta(minutes=15)
        ),
        hibernate=hibernate,
        submit=submit,
    )
    try:
        watcher.observe(IdlePredicate(), NOW)
        assert started.wait(0.1)
        assert submitted == [hibernating.state.id, idle.state.id]

        watcher.observe(IdlePredicate(), NOW)
        assert submitted == [hibernating.state.id, idle.state.id]
    finally:
        release.set()


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {"worktree": {"root_template": str(tmp_path / "trees")}, "tmux": {"session_prefix": "t-"}}
    )
    return WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


def test_a_message_thaws_a_frozen_workspace_before_it_is_delivered(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="idle"))
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    frozen = manager.freeze(state.id)
    assert frozen.frozen_at is not None
    manager.send_message(state.id, "wake up")

    after = manager.store.get(state.id)
    assert after.frozen_at is None and after.thawed_at is not None
    assert [e.kind for e in events][:3] == ["frozen", "thawed", "message_sent"]
    assert fake_tmux.sent_texts[-1][1] == "wake up"


def test_freeze_is_idempotent_and_skips_a_paused_workspace(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="idle"))
    first = manager.freeze(state.id)
    assert manager.freeze(state.id).frozen_at == first.frozen_at
    manager.store.save(replace(first, frozen_at=None, status=WorkspaceStatus.PAUSED))
    assert manager.freeze(state.id).frozen_at is None


def test_the_watch_never_refreezes_a_workspace_a_message_just_woke(
    manager: WorkspaceManager,
) -> None:
    """#1083 incident: freeze, then a message thaws it, then the next idle check.
    The agent's last event is an hour old, as it was live, so only the thaw that
    the real verb stamped can keep the watch from freezing it again."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="idle"))
    manager.freeze(state.id)
    manager.send_message(state.id, "wake up")
    woken = manager.store.get(state.id)
    assert woken.thawed_at is not None

    row = _row(quiet_for=timedelta(hours=1), state=woken)
    check = woken.thawed_at + timedelta(seconds=4)
    frozen: list[str] = []
    watcher = IdleWatcher(
        candidates=lambda: (row,),
        fresh=lambda r: r,
        freeze=lambda r: frozen.append(r.state.id),
        rule_for=lambda _row: RULE,
    )

    watcher.observe(IdlePredicate(), check)
    assert frozen == []
    # And once the cooldown has passed since the thaw, the rule applies again.
    watcher.observe(IdlePredicate(), woken.thawed_at + timedelta(minutes=16))
    assert frozen == [state.id]
