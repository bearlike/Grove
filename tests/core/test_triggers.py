"""Grove's configured session instructions: who receives which rule, and how often.

Sending is cheap and sending WRONGLY is not — an instruction lands in a
transcript the agent re-reads every turn, and a stopped native session would be
revived by an ordinary message. So the decision is a pure function and these
tests drive it directly. Each one changes the single fact that should flip the
answer, because a test that reaches the right answer through the wrong clause is
how a guard goes vacuous.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grove.core.activity import WorkspaceActivity
from grove.core.agents.model import AgentActivityState
from grove.core.config import TriggerRule, TriggersConfig, load_config
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.watches import (
    TriggerFireRequest,
    TriggerPredicate,
    TriggerTarget,
    WatchOutcome,
    WatchView,
)
from grove.core.errors import GroveError
from grove.core.instructions import GroveInstruction
from grove.core.phase import PhaseReport
from grove.core.watches.dispatch import TriggerDispatch
from grove.core.watches.log import WatchLog
from grove.core.watches.mailbox import MailboxWatchCourier
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.subscriptions import StandingWatches
from grove.core.watches.triggers import Firing, TriggerEvidence, TriggerWatcher
from grove.core.watches.watcher import WatcherRegistry
from grove.core.workspace import WorkspaceState, WorkspaceStatus, WorkspaceTitle

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
WS = "a" * 32
# The packaged catalog, read the way the cascade's layer 0 reads it — never via
# `load_config`, which would also read the developer's own user config.
CATALOG = {
    rule.name: rule
    for rule in TriggersConfig.model_validate(TriggersConfig.seed_layer()["triggers"]).rules
}


def _state(**over: Any) -> WorkspaceState:
    fields: dict[str, Any] = {
        "id": WS,
        "title": "parser validation",
        "description": "Validate malformed input",
        "repo_root": "/repos/grove",
        "worktree_path": "/repos/grove/.worktrees/parser",
        "branch": "grove/parser",
        "base_branch": "main",
        "tmux_session": "grove-parser",
        "agent_name": "claude",
        "status": WorkspaceStatus.RUNNING,
        "created_at": NOW - timedelta(hours=1),
        "updated_at": NOW - timedelta(hours=1),
        "brief": True,
    }
    fields.update(over)
    return WorkspaceState(**fields)


def _phase(phase: str = "build", *, ago: timedelta = timedelta(minutes=11)) -> PhaseReport:
    return PhaseReport.model_validate({"phase": phase, "note": "wiring", "updated_at": NOW - ago})


def _seen(**over: Any) -> TriggerEvidence:
    fields: dict[str, Any] = {
        "state": _state(),
        "status": WorkspaceStatus.ACTIVE,
        "activity": AgentActivityState.WORKING,
        "session_id": "s1",
        "session_started_at": NOW - timedelta(hours=1),
        "progress": 12,
        "compactions": 0,
        "phase": _phase(),
    }
    fields.update(over)
    return TriggerEvidence(**fields)


def _watched(**over: Any) -> TriggerPredicate:
    """A predicate that has already seen session s1 once, at progress 10."""
    fields: dict[str, Any] = {"workspace_id": WS, "session_id": "s1", "progress": 10}
    fields.update(over)
    return TriggerPredicate(**fields)


def _decide(
    names: tuple[str, ...], predicate: TriggerPredicate, seen: TriggerEvidence
) -> Firing | None:
    rules = tuple(CATALOG[name] for name in names)
    return TriggerWatcher.decide(predicate, seen, rules, now=NOW)


def _settled(firing: Firing) -> TriggerPredicate:
    """The fired predicate as the next check sees it: progress unchanged since."""
    return firing.predicate.model_copy(update={"progress": 10})


# ─── the shared gate: live, working, progressing ────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.ERROR,
        WorkspaceStatus.ORPHANED,
    ],
)
def test_a_workspace_that_is_not_live_is_never_contacted(status: WorkspaceStatus) -> None:
    assert _decide(("phase",), _watched(), _seen(status=status)) is None


@pytest.mark.parametrize(
    "activity",
    [
        AgentActivityState.IDLE,
        AgentActivityState.WAITING,
        AgentActivityState.BLOCKED,
        AgentActivityState.ERROR,
        AgentActivityState.STARTING,
        None,
    ],
)
def test_only_a_working_agent_is_contacted(activity: AgentActivityState | None) -> None:
    assert _decide(("phase",), _watched(), _seen(activity=activity)) is None


def test_one_long_tool_call_is_not_progress() -> None:
    """Replies plus tool calls stood still: one long tool call, or a stall."""
    assert _decide(("phase",), _watched(progress=12), _seen(progress=12)) is None


def test_the_first_sight_of_a_session_only_records_a_baseline() -> None:
    """A fresh registration has nothing to compare against, so it must not fire."""
    assert _decide(("phase",), TriggerPredicate(workspace_id=WS), _seen()) is None


def test_progress_while_working_lets_a_due_rule_fire() -> None:
    firing = _decide(("phase",), _watched(), _seen())
    assert firing is not None and firing.rule.name == "phase"


# ─── phase_stale ────────────────────────────────────────────────────────────


def test_a_stale_phase_fires_at_ten_minutes_by_default() -> None:
    assert CATALOG["phase"].after_minutes == 10
    nine = _seen(phase=_phase(ago=timedelta(minutes=9)))
    ten = _seen(phase=_phase(ago=timedelta(minutes=10)))
    assert _decide(("phase",), _watched(), nine) is None
    assert _decide(("phase",), _watched(), ten) is not None


def test_the_check_runs_well_inside_the_staleness_threshold() -> None:
    """A ten-minute threshold checked every ten minutes reminds at up to twenty."""
    assert TriggersConfig().check_seconds <= 60


def test_a_phase_is_reminded_about_once_per_report() -> None:
    phase = _phase()
    firing = _decide(("phase",), _watched(), _seen(phase=phase))
    assert firing is not None
    assert firing.predicate.phase_reminded_at == phase.updated_at
    assert _decide(("phase",), _settled(firing), _seen(phase=phase)) is None


def test_a_handed_off_phase_is_not_reminded() -> None:
    assert _decide(("phase",), _watched(), _seen(phase=_phase("handoff"))) is None


def test_a_grove_seeded_bare_scope_is_not_a_report() -> None:
    seeded = PhaseReport.model_validate({"phase": "scope", "updated_at": NOW - timedelta(hours=1)})
    assert _decide(("phase",), _watched(), _seen(phase=seeded)) is None


# ─── compaction ─────────────────────────────────────────────────────────────


def test_a_compaction_since_the_last_check_fires_the_reminder_once() -> None:
    firing = _decide(("compaction",), _watched(compactions=0), _seen(compactions=1))
    assert firing is not None and firing.rule.name == "compaction"
    assert firing.predicate.compactions == 1
    assert _decide(("compaction",), _settled(firing), _seen(compactions=1)) is None


def test_a_compaction_while_idle_waits_for_the_next_working_check() -> None:
    """A `/compact` typed into an idle session must not be lost."""
    idle = _seen(compactions=1, activity=AgentActivityState.WAITING)
    assert _decide(("compaction",), _watched(compactions=0), idle) is None
    # The baseline never advances the compaction count, so it is still owed.
    assert TriggerWatcher.baseline(_watched(compactions=0), idle).compactions == 0
    assert _decide(("compaction",), _watched(compactions=0), _seen(compactions=1)) is not None


def test_a_resumed_session_s_old_compactions_are_not_reminded() -> None:
    """A new session id adopts its compaction count as already accounted for."""
    switched = _seen(session_id="s2", compactions=4)
    adopted = TriggerWatcher.baseline(_watched(compactions=0), switched)
    assert (adopted.session_id, adopted.compactions) == ("s2", 4)


# ─── session_start ──────────────────────────────────────────────────────────


def test_the_start_rule_fires_once_in_a_fresh_session() -> None:
    fresh = _seen(session_started_at=NOW - timedelta(minutes=2))
    firing = _decide(("start",), _watched(), fresh)
    assert firing is not None and firing.predicate.sent == {"start": 1}
    assert _decide(("start",), _settled(firing), fresh) is None


def test_the_start_rule_does_not_brief_a_session_already_deep_in_its_task() -> None:
    """Deploying triggers must not brief every session already running on the host."""
    assert (
        _decide(("start",), _watched(), _seen(session_started_at=NOW - timedelta(hours=2))) is None
    )


def _row(state: WorkspaceState) -> WorkspaceActivity:
    return WorkspaceActivity(
        state=state,
        sessions=(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=NOW,
    )


def test_a_workspace_created_without_a_brief_gets_no_start_rule() -> None:
    names = {
        r.name for r in TriggerEvidence.of(_row(_state(brief=False)), tuple(CATALOG.values())).rules
    }
    assert "start" not in names and "phase" in names
    briefed = {r.name for r in TriggerEvidence.of(_row(_state()), tuple(CATALOG.values())).rules}
    assert "start" in briefed


# ─── unnamed, and the send cap ──────────────────────────────────────────────


def test_a_generated_title_is_recognisable_and_never_derived() -> None:
    first, second = WorkspaceTitle.generate(), WorkspaceTitle.generate()
    assert WorkspaceTitle.is_generated(first) and first != second
    assert not WorkspaceTitle.is_generated("wk-parser-fix")
    assert not WorkspaceTitle.is_generated("parser validation")


def test_a_generated_title_or_a_missing_description_is_unnamed() -> None:
    assert (
        _seen(state=_state(title=WorkspaceTitle.generate())).missing_name() == "a generated title"
    )
    assert _seen(state=_state(description=None)).missing_name() == "no description"
    assert _seen().missing_name() is None


def test_the_naming_request_is_capped_at_two_sends_per_session() -> None:
    rule = CATALOG["unnamed"]
    assert rule.max_sends == 2
    unnamed = _seen(state=_state(title=WorkspaceTitle.generate(), description=None))
    predicate, sends = _watched(), 0
    for minutes in range(0, 60, 6):  # spaced past the five-minute floor
        firing = TriggerWatcher.decide(
            predicate, unnamed, (rule,), now=NOW + timedelta(minutes=minutes)
        )
        if firing is not None:
            sends += 1
            predicate = firing.predicate
        predicate = predicate.model_copy(update={"progress": 10})
    assert sends == 2


def test_naming_requests_are_spaced_apart() -> None:
    rule = CATALOG["unnamed"]
    unnamed = _seen(state=_state(description=None))
    first = TriggerWatcher.decide(_watched(), unnamed, (rule,), now=NOW)
    assert first is not None
    later = NOW + timedelta(minutes=2)
    assert TriggerWatcher.decide(_settled(first), unnamed, (rule,), now=later) is None


def test_a_named_workspace_is_never_asked() -> None:
    assert _decide(("unnamed",), _watched(), _seen()) is None


def test_a_new_session_starts_its_counts_over() -> None:
    """A cap is a promise about one conversation's context window."""
    switched = _seen(session_id="s2")
    assert TriggerWatcher.baseline(_watched(sent={"unnamed": 2}), switched).sent == {}


def test_at_most_one_rule_fires_per_check() -> None:
    both = _seen(compactions=1, state=_state(description=None))
    firing = _decide(("compaction", "unnamed"), _watched(compactions=0), both)
    assert firing is not None and firing.rule.name == "compaction"


def test_a_disabled_rule_is_not_offered() -> None:
    config = TriggersConfig(rules=[CATALOG["phase"].model_copy(update={"enabled": False})])
    assert config.active() == ()


# ─── manual dispatch ────────────────────────────────────────────────────────


def _watcher(seen: TriggerEvidence) -> TriggerWatcher:
    return TriggerWatcher(lambda _id: seen, render=lambda rule, _s, values: rule.render(values))


def test_a_manual_fire_skips_the_condition_but_not_the_live_gate() -> None:
    idle = _seen(activity=AgentActivityState.WAITING)
    assert not isinstance(_watcher(idle).fire(_watched(), CATALOG["phase"], NOW), str)
    offline = _watcher(_seen(status=WorkspaceStatus.OFFLINE))
    assert offline.fire(_watched(), CATALOG["phase"], NOW) == "workspace is not live"


def test_a_manual_fire_counts_against_the_same_cap() -> None:
    refusal = _watcher(_seen()).fire(_watched(sent={"unnamed": 2}), CATALOG["unnamed"], NOW)
    assert isinstance(refusal, str) and "cap" in refusal


def test_an_empty_selector_matches_nothing() -> None:
    assert TriggerTarget().is_empty
    assert not TriggerDispatch.matches(TriggerTarget(), _state(), None)


@pytest.mark.parametrize(
    ("target", "hit"),
    [
        (TriggerTarget(all=True), True),
        (TriggerTarget(workspace_ids=[WS]), True),
        (TriggerTarget(workspace_ids=["b" * 32]), False),
        (TriggerTarget(all=True, agent="codex"), False),
        (TriggerTarget(phase="build"), True),
        (TriggerTarget(phase="verify"), False),
        (TriggerTarget(title_prefix="parser"), True),
        (TriggerTarget(title_prefix="wk-"), False),
        (TriggerTarget(repo="/repos/grove"), True),
        (TriggerTarget(ticket="gitea:42"), False),
    ],
)
def test_every_selector_field_narrows(target: TriggerTarget, hit: bool) -> None:
    assert TriggerDispatch.matches(target, _state(), _phase()) is hit


# ─── the catalog and its cascade ────────────────────────────────────────────


def test_the_packaged_catalog_ships_the_four_rules_in_order() -> None:
    assert [(r.name, r.when) for r in CATALOG.values()] == [
        ("start", "session_start"),
        ("compaction", "compaction"),
        ("phase", "phase_stale"),
        ("unnamed", "unnamed"),
    ]


def test_the_cascade_seeds_the_catalog(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    assert [r.name for r in load_config(None, env={}).triggers.rules] == list(CATALOG)


def test_a_layer_refines_one_field_of_a_built_in_rule_by_name(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    cfg = load_config(
        None,
        env={},
        cli_overrides={"triggers": {"rules": [{"name": "phase", "after_minutes": 20}]}},
    )
    phase = cfg.triggers.rule("phase")
    assert phase is not None and phase.after_minutes == 20
    assert phase.text == CATALOG["phase"].text


def test_a_layer_adds_a_manual_rule_that_never_fires_on_its_own(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    manual = {"name": "ship", "when": "manual", "text": "Ship {title}."}
    cfg = load_config(None, env={}, cli_overrides={"triggers": {"rules": [manual]}})
    assert cfg.triggers.rule("ship") is not None
    assert "ship" not in {rule.name for rule in cfg.triggers.active()}


def test_the_retired_reminder_keys_still_load_and_map_onto_their_rules(
    tmp_state_dir: Path,
) -> None:
    del tmp_state_dir
    cfg = load_config(
        None,
        env={},
        cli_overrides={
            "nudges": {"phase": {"enabled": False, "every_minutes": 5}},
            "brief": {"self_naming": False},
        },
    )
    phase, unnamed = cfg.triggers.rule("phase"), cfg.triggers.rule("unnamed")
    assert phase is not None and (phase.enabled, phase.after_minutes) == (False, 5)
    assert unnamed is not None and unnamed.enabled is False


def test_triggers_off_disables_every_rule() -> None:
    assert TriggersConfig(enabled=False, rules=list(CATALOG.values())).active() == ()


def test_rendering_fills_known_placeholders_and_leaves_any_other_brace() -> None:
    rule = TriggerRule(name="x", when="manual", text='{title} at {phase}, json {"a": 1} {nope}')
    assert rule.render({"title": "T", "phase": "build"}) == 'T at build, json {"a": 1} {nope}'


def test_the_reminders_are_firm_and_name_the_command() -> None:
    for name in ("compaction", "phase", "unnamed"):
        assert "grove " in CATALOG[name].text  # names the exact command to run
    assert "NOW" in CATALOG["phase"].text and "NOW" in CATALOG["unnamed"].text
    assert "twice" in CATALOG["unnamed"].text


def test_the_start_rule_points_at_the_skill_and_reads_placement_rather_than_infers_it() -> None:
    """The root-placement blind spot, pinned as copy: an agent in an ordinary
    checkout disbelieves "you are in a Grove workspace" unless told to read the
    record rather than infer it from where it is standing."""
    start = CATALOG["start"]
    assert "working-in-grove" in start.text
    assert "repository root itself" in start.text and "rather than inferring" in start.text
    assert len(start.text.split()) < 230  # spent on the first turn of every session
    assert start.include_instructions


# ─── through the real scheduler: persist, deliver, dispatch ─────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class _Courier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, watch: WatchView, outcome: WatchOutcome) -> tuple[str, str | None]:
        self.sent.append((watch.predicate.kind, outcome.summary))
        return "delivered", None


class _Evidence:
    """What the daemon would observe next; a test moves it between ticks."""

    def __init__(self, seen: TriggerEvidence) -> None:
        self.seen = seen

    def __call__(self, _workspace_id: str) -> TriggerEvidence:
        return self.seen


def _rig(tmp_path: Path, seen: TriggerEvidence | _Evidence):
    evidence = seen if isinstance(seen, _Evidence) else _Evidence(seen)
    seen = evidence.seen
    clock, courier = _Clock(), _Courier()
    watcher = TriggerWatcher(evidence, render=lambda rule, _s, values: rule.render(values))
    log = WatchLog(tmp_path / "watches.json")
    scheduler = WatchScheduler(
        log=log, watchers=WatcherRegistry([watcher]), deliver=courier, clock=clock
    )
    scheduler.prime()
    record = {WS: seen.state}
    standing = StandingWatches(
        scheduler=scheduler, workspaces=record.get, trigger_every=timedelta(seconds=60)
    )
    standing.reconcile(WS)
    rules = tuple(CATALOG.values())
    dispatch = TriggerDispatch(
        scheduler=scheduler,
        watcher=watcher,
        fleet=lambda: [(record[WS], seen.phase, rules)],
    )
    return clock, courier, log, scheduler, dispatch


def _trigger_row(scheduler: WatchScheduler) -> WatchView:
    return next(row for row in scheduler.list(workspace_id=WS).watches if row.state == "pending")


async def test_a_running_workspace_holds_one_trigger_watch_and_the_tick_delivers(
    tmp_path: Path,
) -> None:
    unnamed = _state(description=None)
    evidence = _Evidence(_seen(state=unnamed, rules=(CATALOG["unnamed"],)))
    clock, courier, _log, scheduler, _dispatch = _rig(tmp_path, evidence)

    await scheduler.tick()  # first sight: baseline only
    assert courier.sent == []
    clock.now += timedelta(seconds=61)
    evidence.seen = _seen(state=unnamed, rules=(CATALOG["unnamed"],), progress=20)
    await scheduler.tick()

    assert [kind for kind, _ in courier.sent] == ["trigger"]
    assert "no description" in courier.sent[0][1]
    # Persisted before delivery: the durable row already counts the send.
    row = _trigger_row(scheduler)
    assert isinstance(row.predicate, TriggerPredicate) and row.predicate.sent == {"unnamed": 1}


def test_a_manual_dispatch_and_the_schedule_share_one_cap(tmp_path: Path) -> None:
    seen = _seen(state=_state(description=None))
    _clock, courier, _log, _scheduler, dispatch = _rig(tmp_path, seen)
    request = TriggerFireRequest(rule="unnamed", target=TriggerTarget(workspace_ids=[WS]))

    receipts = [dispatch.fire(request).results[0].receipt for _ in range(3)]

    assert receipts == ["delivered", "delivered", "skipped"]
    assert len(courier.sent) == 2


def test_a_dispatch_to_a_workspace_with_no_trigger_watch_is_skipped(tmp_path: Path) -> None:
    seen = _seen()
    _clock, courier, _log, scheduler, dispatch = _rig(tmp_path, seen)
    scheduler.cancel(_trigger_row(scheduler).id)  # as a pause would

    view = dispatch.fire(TriggerFireRequest(rule="phase", target=TriggerTarget(workspace_ids=[WS])))

    assert view.results[0].receipt == "skipped"
    assert courier.sent == []


def test_a_selector_that_names_nothing_is_refused(tmp_path: Path) -> None:
    _clock, _courier, _log, _scheduler, dispatch = _rig(tmp_path, _seen())
    with pytest.raises(GroveError):
        dispatch.fire(TriggerFireRequest(rule="phase", target=TriggerTarget()))


def test_a_retired_phase_nudge_row_is_dropped_not_held_aside(tmp_path: Path) -> None:
    """Held aside, a retired kind would sit in the log forever, unreadable by every build."""
    path = tmp_path / "watches.json"
    retired = {
        "id": "wch_" + "1" * 32,
        "recipient": {"workspace_id": WS, "agent": ""},
        "predicate": {"kind": "phase_nudge", "workspace_id": WS},
        "state": "pending",
        "created_at": NOW.isoformat(),
        "expires_at": None,
    }
    path.write_text(json.dumps({"version": 1, "watches": {retired["id"]: retired}}))
    log = WatchLog(path)

    assert log.active() == []
    log.put(_trigger_row_for_log())
    assert "phase_nudge" not in path.read_text()


def _trigger_row_for_log() -> WatchView:
    return WatchView(
        id="wch_" + "2" * 32,
        recipient=MailboxAddress(workspace_id=WS),
        predicate=TriggerPredicate(workspace_id=WS),
        state="pending",
        created_at=NOW,
        expires_at=None,
    )


def test_a_session_already_watched_adopts_its_compactions_on_first_count() -> None:
    """The deploy case: a row exists for this session but never counted compactions.

    Without adoption the next WORKING check would remind about every compaction
    the session went through before triggers existed.
    """
    legacy = _watched(compactions=None)
    seen = _seen(compactions=3)
    assert TriggerWatcher.baseline(legacy, seen).compactions == 3
    assert _decide(("compaction",), legacy, seen) is None


def test_a_dispatch_never_advances_a_watch_it_does_not_own(tmp_path: Path) -> None:
    """A stopped workspace is skipped by the dispatch itself, not by luck downstream.

    The fleet lists the workspace, but its trigger watch belongs to ANOTHER
    workspace id, so the only thing between the selector and someone else's
    watch is the ownership lookup.
    """
    seen = _seen()
    _clock, courier, _log, scheduler, _dispatch = _rig(tmp_path, seen)
    stranger = _state(id="c" * 32, title="stopped one")
    watcher = TriggerWatcher(_Evidence(seen), render=lambda rule, _s, values: rule.render(values))
    dispatch = TriggerDispatch(
        scheduler=scheduler,
        watcher=watcher,
        fleet=lambda: [(stranger, None, tuple(CATALOG.values()))],
    )

    view = dispatch.fire(TriggerFireRequest(rule="phase", target=TriggerTarget(all=True)))

    assert [(r.workspace_id, r.receipt) for r in view.results] == [("c" * 32, "skipped")]
    assert courier.sent == []


class _Delivery:
    """Records what reaches a session through the stricter notify road."""

    def __init__(self) -> None:
        self.notified: list[str] = []

    def notify(self, _address: MailboxAddress, text: str) -> tuple[str, str | None]:
        self.notified.append(text)
        return "delivered", None


@pytest.mark.parametrize("rule", ["start", "phase"])
def test_a_delivered_trigger_carries_exactly_one_fence(rule: str) -> None:
    """The courier and the renderer each fenced once, and nothing tested the two
    together: the first live reminder after deploy arrived nested inside a second
    `<grove-instruction>`. Pinned on the COMPOSED text, the only place it shows."""
    rendered = (
        GroveInstruction.workspace(CATALOG[rule].text, [("id", WS)])
        if CATALOG[rule].include_instructions
        else GroveInstruction.reminder(CATALOG[rule].text)
    )
    delivery = _Delivery()
    courier = MailboxWatchCourier(delivery)  # type: ignore[arg-type]

    courier(_trigger_row_for_log(), WatchOutcome(ok=True, summary=rendered))

    (sent,) = delivery.notified
    assert sent.count("<grove-instruction") == 1
    assert GroveInstruction.is_authored(sent)
