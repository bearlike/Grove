"""NotificationBroker edge/dedupe/debounce policy + the Gotify / webhook channels.

The broker's ``evaluate`` is the single pure decision site, so it is tested
directly against a fake clock with hand-built deltas (no threads, no I/O) — real
engine IR, so a contract drift fails to compile rather than silently passing. The
channels are tested against ``httpx.MockTransport`` — the same "stub only the I/O
boundary" seam ``MewboClient`` uses — asserting the exact wire shape and that a
failure narrows to the typed error the broker's guard expects.

The three triggers each get their own block: the agent-state rising edge, the
question edge (deduped by id, deliberately *not* debounced), and the lifecycle
edge (rendered from the broker's identity cache, or ``unresolved`` when the
workspace never reported activity).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from grove.core.activity import (
    DashboardDelta,
    FleetSummary,
    QueueDepth,
    SessionActivity,
    WorkspaceActivity,
)
from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    AgentQuestion,
    AgentQuestionOption,
    AgentSession,
)
from grove.core.config import (
    GotifyChannelConfig,
    GroveConfig,
    NotificationsConfig,
    WebhookChannelConfig,
)
from grove.core.errors import GotifyError, WebhookError
from grove.core.notifications import (
    GotifyNotificationChannel,
    Notification,
    NotificationBroker,
    NotificationChannel,
    WebhookNotificationChannel,
    WorkspaceIdentity,
)
from grove.core.workspace import WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)
DEEP_LINK_BASE = "https://grove.example.com"


# ─── builders ────────────────────────────────────────────────────────────────


def _ws_state() -> WorkspaceState:
    return WorkspaceState(
        id="ws1",
        title="fix-auth",
        repo_root="/home/u/proj",
        branch="grove/fix-auth",
        base_branch="main",
        worktree_path="/home/u/proj/.worktrees/fix-auth",
        tmux_session="grove-fix-auth",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=T0,
        updated_at=T0,
    )


def _question(qid: str, *, prompt: str = "Ship it?", answered: bool = False) -> AgentQuestion:
    return AgentQuestion(
        id=qid,
        group_id="grp1",
        kind="single_select",
        prompt=prompt,
        header="Deploy",
        options=(
            AgentQuestionOption(label="Yes", description="ship to prod"),
            AgentQuestionOption(label="No"),
        ),
        answered=answered,
    )


def _session(
    state: AgentActivityState,
    *,
    sid: str = "s1",
    task: str | None = None,
    questions: tuple[AgentQuestion, ...] = (),
    error: str | None = None,
    active_subagents: int = 0,
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id=sid,
            transcript_path=None,
            adapter_kind="claude_code",
            provenance="grove_launched",
        ),
        activity=AgentActivity(
            state=state,
            current_task=task,
            questions=questions,
            error_detail=error,
            active_subagents=active_subagents,
        ),
    )


def _row(
    *sessions: SessionActivity,
    fleet: FleetSummary | None = None,
    queue: QueueDepth | None = None,
) -> WorkspaceActivity:
    return WorkspaceActivity(
        state=_ws_state(),
        sessions=tuple(sessions),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=T0,
        fleet=fleet,
        queue=queue,
    )


def _delta(
    *sessions: SessionActivity,
    fleet: FleetSummary | None = None,
    queue: QueueDepth | None = None,
) -> DashboardDelta:
    return DashboardDelta(
        kind="session_activity",
        seq=1,
        workspace_id="ws1",
        repo_root="/home/u/proj",
        workspace=_row(*sessions, fleet=fleet, queue=queue),
    )


def _lifecycle(event: str, *, workspace_id: str = "ws1", **detail: str) -> DashboardDelta:
    """A ``workspace_changed`` delta exactly as ``ActivityService`` bridges one:
    the manager's ``WorkspaceEvent.kind`` folded into ``detail["event"]``."""
    return DashboardDelta(
        kind="workspace_changed",
        seq=1,
        workspace_id=workspace_id,
        repo_root="/home/u/proj",
        detail={"event": event, **detail},
    )


def _broker(**kw: object) -> NotificationBroker:
    clock = kw.pop("clock", lambda: T0)
    # Every existing test in this module predates the quiet-window push and
    # exercises some OTHER edge (debounce, dedupe, lifecycle, rendering) — none
    # of them mean to wait out `waiting_quiet`, so the helper defaults it to
    # immediate (the pre-feature contract) and only the tests that are actually
    # about the quiet window override it.
    kw.setdefault("waiting_quiet", timedelta(0))
    return NotificationBroker(channels=[], deep_link_base_url=DEEP_LINK_BASE, clock=clock, **kw)  # type: ignore[arg-type]


# ─── agent-state edge ─────────────────────────────────────────────────────────


def test_first_observation_seeds_without_firing() -> None:
    """A session first seen already WAITING (daemon restart mid-turn) seeds the
    edge memory but must not buzz — otherwise every finished workspace fires on boot."""
    broker = _broker()
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []


def test_rising_edge_into_waiting_fires() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed WORKING
    out = broker.evaluate(_delta(_session(AgentActivityState.WAITING, task="ran the suite")))
    assert len(out) == 1
    n = out[0]
    assert n.trigger == "agent_state"
    assert n.state is AgentActivityState.WAITING
    assert n.event == "waiting"
    assert n.severity == "normal"
    assert n.workspace_id == "ws1"
    assert n.reason == "finished its turn"
    assert n.summary == "ran the suite"
    assert n.deep_link == "https://grove.example.com/w/ws1"
    assert n.title() == "proj · fix-auth"
    assert "claude finished its turn" in n.body()
    assert "**claude** finished its turn" in n.markdown()
    assert "[Open workspace](https://grove.example.com/w/ws1)" in n.markdown()
    assert n.tags() == ("waiting", "normal")


def test_staying_in_attention_state_does_not_refire() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1
    # Still WAITING next tick → no new edge.
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []


def test_working_state_never_fires() -> None:
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))  # seed (no fire)
    assert broker.evaluate(_delta(_session(AgentActivityState.WORKING))) == []


def test_blocked_and_error_are_default_targets() -> None:
    # debounce=0 so the two distinct edges in one test instant aren't suppressed.
    broker = _broker(debounce=timedelta(0))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    blocked = broker.evaluate(_delta(_session(AgentActivityState.BLOCKED)))[0]
    assert (blocked.reason, blocked.severity) == ("needs your input", "high")
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    errored = broker.evaluate(_delta(_session(AgentActivityState.ERROR)))[0]
    assert (errored.reason, errored.severity) == ("hit an error", "high")


def test_debounce_suppresses_second_fire_within_window() -> None:
    """After a fire the workspace is quiet for the debounce window, even across a
    WAITING→WORKING→WAITING flap."""
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], debounce=timedelta(seconds=30))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1
    # 10s later: flap back to WORKING then WAITING — still inside the window.
    clock["t"] = T0 + timedelta(seconds=10)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []
    # Past the window: a fresh edge fires again.
    clock["t"] = T0 + timedelta(seconds=40)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.WAITING)))) == 1


# ─── the quiet-window push (WAITING is not "done") ────────────────────────────


def test_waiting_edge_is_held_back_while_a_subagent_is_active() -> None:
    """A WAITING turn with a spawned-but-unreturned sub-agent is not "done" — the
    edge does not even queue until ``active_subagents`` drops to zero."""
    broker = _broker(waiting_quiet=timedelta(minutes=1))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING, active_subagents=1))) == []
    assert broker.due_quiet(now=T0 + timedelta(hours=1)) == []  # nothing was ever parked
    broker.evaluate(_delta(_session(AgentActivityState.WAITING, active_subagents=0)))
    assert len(broker.due_quiet(now=T0 + timedelta(minutes=2))) == 1


def test_waiting_edge_is_held_back_while_the_fleet_is_active() -> None:
    """Same gate, sourced from the hook's live fleet rather than the transcript."""
    broker = _broker(waiting_quiet=timedelta(minutes=1))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    busy = FleetSummary(active=1, total=1)
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING), fleet=busy)) == []
    settled = FleetSummary(active=0, total=1)
    broker.evaluate(_delta(_session(AgentActivityState.WAITING), fleet=settled))
    assert len(broker.due_quiet(now=T0 + timedelta(minutes=2))) == 1


def test_waiting_edge_is_held_back_while_the_queue_has_pending_messages() -> None:
    """Same gate, sourced from the harness's own steer queue."""
    broker = _broker(waiting_quiet=timedelta(minutes=1))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    pending = QueueDepth(pending=1)
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING), queue=pending)) == []
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))
    assert len(broker.due_quiet(now=T0 + timedelta(minutes=2))) == 1


def test_quiet_window_push_waits_the_full_window_and_fires_once() -> None:
    broker = _broker(waiting_quiet=timedelta(minutes=15))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []  # parked, not fired
    assert broker.due_quiet(now=T0 + timedelta(minutes=14)) == []  # not yet
    fired = broker.due_quiet(now=T0 + timedelta(minutes=15))
    assert len(fired) == 1
    assert fired[0].trigger == "agent_state"
    assert fired[0].state is AgentActivityState.WAITING
    assert fired[0].reason == "finished its turn"
    # Idempotence requirement #1: fires exactly once — the entry is gone.
    assert broker.due_quiet(now=T0 + timedelta(minutes=30)) == []


def test_quiet_window_push_is_cancelled_if_work_resumes_before_it_elapses() -> None:
    broker = _broker(waiting_quiet=timedelta(minutes=15))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))  # parked
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # resumes before it elapses
    assert broker.due_quiet(now=T0 + timedelta(minutes=30)) == []


def test_quiet_window_push_is_cancelled_by_a_fresh_question() -> None:
    """A question means the turn was not actually over — the parked "finished"
    push is wrong the moment that becomes true, so a question wins instead."""
    broker = _broker(waiting_quiet=timedelta(minutes=15))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))  # parked
    out = broker.evaluate(
        _delta(_session(AgentActivityState.BLOCKED, questions=(_question("q1"),)))
    )
    assert len(out) == 1
    assert out[0].trigger == "question"
    assert broker.due_quiet(now=T0 + timedelta(minutes=30)) == []


def test_first_observation_never_queues_a_quiet_push_even_if_already_waiting() -> None:
    """Idempotence requirements #2/#3: the very first observation of a session
    always seeds (``previous is None``), so a workspace that was already
    WAITING/settled before this broker existed — a daemon restart, or a
    workspace quiet since before the daemon ever started — can never enter the
    quiet-window queue from that observation. Only a genuine transition
    observed AFTER startup can queue one."""
    broker = _broker(waiting_quiet=timedelta(minutes=15))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING))) == []
    assert broker.due_quiet(now=T0 + timedelta(days=1)) == []


def test_waiting_quiet_zero_restores_immediate_fire() -> None:
    """The escape hatch: ``waiting_quiet_minutes: 0`` fires the instant every
    known tracker agrees nothing is left, exactly like before this feature —
    the busy-gate still applies even with the wait disabled."""
    broker = _broker(waiting_quiet=timedelta(0))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING, active_subagents=1))) == []
    out = broker.evaluate(_delta(_session(AgentActivityState.WAITING, active_subagents=0)))
    assert len(out) == 1
    assert out[0].state is AgentActivityState.WAITING


def test_custom_notify_states_can_include_idle_and_exclude_error() -> None:
    broker = _broker(notify_states=frozenset({AgentActivityState.IDLE}))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.ERROR))) == []  # not a target
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    idle = broker.evaluate(_delta(_session(AgentActivityState.IDLE)))[0]
    assert (idle.reason, idle.severity) == ("went idle", "low")


def test_non_attention_notify_states_are_filtered_out() -> None:
    """WORKING/STARTING can never be notify targets even if misconfigured."""
    broker = _broker(notify_states=frozenset({AgentActivityState.WORKING}))
    broker.evaluate(_delta(_session(AgentActivityState.WAITING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WORKING))) == []


def test_error_edge_summarizes_the_error_detail_not_the_stale_task() -> None:
    """An ERROR push must carry *why it broke*. ``current_task`` is the last thing
    the agent was doing — stale and misleading next to the word "error" — while
    ``error_detail`` is the reason the adapter actually captured."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(
        _delta(
            _session(
                AgentActivityState.ERROR,
                task="refactoring the auth module",
                error="rate limit exceeded (429)",
            )
        )
    )
    assert len(out) == 1
    assert out[0].summary == "rate limit exceeded (429)"
    assert "rate limit exceeded (429)" in out[0].markdown()
    assert "refactoring the auth module" not in out[0].body()


def test_error_edge_falls_back_to_the_task_when_no_detail_was_captured() -> None:
    """Most adapters never populate ``error_detail`` — the task line is better than
    nothing, so the fallback keeps a bare ERROR push informative."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(_delta(_session(AgentActivityState.ERROR, task="running the suite")))
    assert out[0].summary == "running the suite"


def test_a_non_error_state_never_borrows_the_error_detail() -> None:
    """``error_detail`` can linger on a session that has since recovered; only the
    ERROR edge itself may render it, or a finished turn would read as a failure."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(
        _delta(_session(AgentActivityState.WAITING, task="done", error="an old, healed error"))
    )
    assert out[0].summary == "done"


def test_no_deep_link_base_yields_none() -> None:
    broker = NotificationBroker(channels=[], clock=lambda: T0, waiting_quiet=timedelta(0))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.WAITING)))[0].deep_link is None


# ─── question edge ────────────────────────────────────────────────────────────


def test_fresh_question_fires_once_with_prompt_and_options() -> None:
    """The question trigger: a new unanswered question is high-severity and carries
    the prompt + the options into both renderings, so the phone shows the choice."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed the session
    out = broker.evaluate(
        _delta(_session(AgentActivityState.WORKING, questions=(_question("q1"),)))
    )
    assert len(out) == 1
    n = out[0]
    assert n.trigger == "question"
    assert n.event == "question"
    assert n.severity == "high"
    assert n.reason == "asked you a question"
    assert [q.id for q in n.questions] == ["q1"]
    body = n.body()
    assert "claude asked you a question" in body
    assert "Deploy — Ship it?" in body
    assert "- Yes — ship to prod" in body
    assert "- No" in body
    markdown = n.markdown()
    assert "**Deploy** — Ship it?" in markdown
    assert "- Yes — ship to prod" in markdown
    assert n.deep_link == "https://grove.example.com/w/ws1"


def test_same_question_id_never_refires() -> None:
    """Dedupe is by question id: the same open question on every subsequent tick
    is the same thing the human already owes an answer to."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    q1 = _question("q1")
    assert len(broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,))))) == 1
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,)))) == []
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,)))) == []


def test_second_question_fires_inside_the_debounce_window() -> None:
    """The non-obvious guarantee: the question edge is deduped by id, NEVER by time.
    A second question 5s after the first is a second answer the agent is waiting on —
    suppressing it would strand the agent with the human believing they were done."""
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], debounce=timedelta(seconds=30))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    q1, q2 = _question("q1"), _question("q2", prompt="Also bump the version?")
    assert len(broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,))))) == 1

    clock["t"] = T0 + timedelta(seconds=5)  # deep inside the debounce window
    out = broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1, q2))))
    assert len(out) == 1
    assert [q.id for q in out[0].questions] == ["q2"]  # only the fresh one is carried
    assert "Also bump the version?" in out[0].body()


def test_two_fresh_questions_at_once_ride_one_notification() -> None:
    """An ``AskUserQuestion`` group is answered atomically, so a batch is one push —
    phrased plural so the human knows how many answers they owe."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(
        _delta(
            _session(
                AgentActivityState.BLOCKED,
                questions=(_question("q1"), _question("q2", prompt="Bump the version?")),
            )
        )
    )
    assert len(out) == 1
    assert out[0].reason == "asked you a question (2 questions)"
    assert [q.id for q in out[0].questions] == ["q1", "q2"]


def test_open_question_on_first_sight_of_a_session_does_not_fire() -> None:
    """The restart storm guard: a daemon restarted while a question is on screen
    must seed it silently, not re-push every question the human is already looking at."""
    broker = _broker()
    q1 = _question("q1")
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,)))) == []
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,)))) == []


def test_a_question_on_a_session_first_seen_by_a_WARM_broker_fires() -> None:
    """The new-workspace case, and the counterpart to the restart guard above.

    `grove create --initial-prompt …` boots an agent that reads its task and
    asks something inside the first activity tick. That session is unseen for
    the same reason a pre-restart session is unseen, so seeding on "unseen
    alone" swallowed the push exactly when the human was most blocked. Once the
    broker is warm, an unseen session is genuinely new and its first question
    is owed.
    """
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], warmup=timedelta(seconds=5))
    # Any first fold opens the warm-up window; this one is a different session.
    broker.evaluate(_delta(_session(AgentActivityState.WORKING, sid="pre-existing")))
    clock["t"] = T0 + timedelta(seconds=30)

    out = broker.evaluate(
        _delta(_session(AgentActivityState.BLOCKED, sid="brand-new", questions=(_question("q1"),)))
    )

    assert len(out) == 1
    assert out[0].trigger == "question"


def test_a_cold_broker_stays_silent_for_every_session_it_meets_at_once() -> None:
    """A restart with a fleet of open questions pushes nothing, however many
    workspaces arrive — the guard is the broker's own warm-up, so it covers
    sessions it has never met rather than only the first one."""
    broker = _broker(warmup=timedelta(seconds=5))  # clock frozen at T0 → always cold
    out = [
        broker.evaluate(
            _delta(
                _session(AgentActivityState.BLOCKED, sid=sid, questions=(_question(f"q-{sid}"),))
            )
        )
        for sid in ("s1", "s2", "s3")
    ]
    assert out == [[], [], []]


def test_answered_question_never_fires() -> None:
    """Only *unanswered* questions are an edge — a resolved one is history."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    answered = _question("q1", answered=True)
    resolved = _delta(_session(AgentActivityState.WORKING, questions=(answered,)))
    assert broker.evaluate(resolved) == []


def test_on_question_false_disables_the_question_edge() -> None:
    """With questions off, a BLOCKED-with-question episode falls back to the state
    edge — one push, from the other trigger. The pending questions still ride along
    on it (same edge, rendered richer); what the switch turns off is the *trigger*."""
    broker = _broker(notify_questions=False)
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(
        _delta(_session(AgentActivityState.BLOCKED, questions=(_question("q1"),)))
    )
    assert len(out) == 1
    assert out[0].trigger == "agent_state"
    assert out[0].reason == "needs your input"
    assert [q.id for q in out[0].questions] == ["q1"]


def test_question_outranks_the_state_edge_behind_it() -> None:
    """A session going WORKING→BLOCKED *with* a fresh question is ONE attention
    episode: the (richer) question push wins and the state edge behind it stays quiet."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    out = broker.evaluate(
        _delta(_session(AgentActivityState.BLOCKED, questions=(_question("q1"),)))
    )
    assert len(out) == 1
    assert out[0].trigger == "question"


def test_question_episode_does_not_buzz_again_once_the_debounce_expires() -> None:
    """The state edge behind a question is *suppressed*, not merely deferred: an
    unanswered question still open past the debounce window must not ring a second
    time as a bare BLOCKED push — it is the same episode, and the human knows."""
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], debounce=timedelta(seconds=30))
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    q1 = _question("q1")
    assert len(broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,))))) == 1

    clock["t"] = T0 + timedelta(seconds=120)  # long past the debounce window
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED, questions=(q1,)))) == []


def test_questions_are_deduped_per_session_not_per_workspace() -> None:
    """Two concurrent sessions in one workspace each own their question memory, and
    the second session's question is not swallowed by the first one's debounce."""
    broker = _broker()
    a, b = _question("q1"), _question("q2", prompt="Rebase?")
    broker.evaluate(
        _delta(_session(AgentActivityState.WORKING), _session(AgentActivityState.WORKING, sid="s2"))
    )
    out = broker.evaluate(
        _delta(
            _session(AgentActivityState.BLOCKED, questions=(a,)),
            _session(AgentActivityState.BLOCKED, sid="s2", questions=(b,)),
        )
    )
    assert [q.id for n in out for q in n.questions] == ["q1", "q2"]


# ─── lifecycle edge ───────────────────────────────────────────────────────────


def test_lifecycle_error_fires_with_the_phase_and_error_in_the_summary() -> None:
    """The "the work was interrupted" arm: a create that blew up at ``worktree_add``
    is diagnosable from the phone, not just "something failed"."""
    broker = _broker()
    out = broker.evaluate(_lifecycle("error", phase="worktree_add", error="fatal: exists"))
    assert len(out) == 1
    n = out[0]
    assert n.trigger == "lifecycle"
    assert n.event == "error"
    assert n.severity == "high"
    assert n.state is None
    assert n.reason == "hit a workspace error"
    assert n.summary == "worktree_add: fatal: exists"
    assert "worktree_add: fatal: exists" in n.body()


def test_lifecycle_offline_and_orphaned_are_default_targets() -> None:
    broker = _broker(debounce=timedelta(0))
    offline = broker.evaluate(_lifecycle("offline_detected"))[0]
    assert (offline.reason, offline.severity) == ("went offline — its session vanished", "normal")
    orphaned = broker.evaluate(_lifecycle("orphaned_detected"))[0]
    assert orphaned.severity == "high"


def test_non_notifiable_lifecycle_event_is_ignored() -> None:
    """``message_sent`` has no reason phrase, so it can never push — the notifiable
    set IS the ``LIFECYCLE`` table's keys."""
    broker = _broker()
    assert broker.evaluate(_lifecycle("message_sent", chars="42")) == []


def test_lifecycle_renders_identity_from_the_activity_cache() -> None:
    """A lifecycle delta carries no activity row, so identity comes from the cache
    the session-activity arm populates — the push reads with the real title/branch."""
    broker = _broker()
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seeds the identity cache
    n = broker.evaluate(_lifecycle("error", error="boom"))[0]
    assert n.title() == "proj · fix-auth"
    assert n.workspace.branch == "grove/fix-auth"
    assert n.workspace.agent_name == "claude"
    assert "`grove/fix-auth`" in n.markdown()


def test_lifecycle_falls_back_to_unresolved_identity_and_still_deep_links() -> None:
    """A create that fails at ``worktree_add`` errors before any activity poll saw
    it: the identity is thin (short id, repo from the delta) but the deep link — the
    one thing that has to work — is derived from the id and is always correct."""
    broker = _broker()
    n = broker.evaluate(_lifecycle("error", workspace_id="abcdef0123456789", error="boom"))[0]
    assert n.workspace == WorkspaceIdentity(
        workspace_id="abcdef0123456789",
        title="abcdef01",  # short id — nothing better is known
        repo_name="proj",
        branch="",
        agent_name="",
    )
    assert n.title() == "proj · abcdef01"
    assert n.deep_link == "https://grove.example.com/w/abcdef0123456789"
    assert n.body().startswith("agent hit a workspace error")


def test_lifecycle_is_debounced_per_workspace() -> None:
    """A failing create rolls back through several phases and errors once per phase;
    the human needs the first one, not all of them."""
    clock = {"t": T0}
    broker = _broker(clock=lambda: clock["t"], debounce=timedelta(seconds=30))
    assert len(broker.evaluate(_lifecycle("error", phase="worktree_add"))) == 1
    clock["t"] = T0 + timedelta(seconds=5)
    assert broker.evaluate(_lifecycle("error", phase="init_script")) == []
    clock["t"] = T0 + timedelta(seconds=40)
    assert len(broker.evaluate(_lifecycle("error", phase="tmux"))) == 1


def test_custom_notify_lifecycle_set_narrows_the_events() -> None:
    broker = _broker(notify_lifecycle=frozenset({"killed"}))
    assert broker.evaluate(_lifecycle("error", error="boom")) == []
    killed = broker.evaluate(_lifecycle("killed"))[0]
    assert (killed.reason, killed.severity) == ("was killed", "low")


# ─── deliver fan-out (best-effort isolation) ──────────────────────────────────


class _FlakyChannel(NotificationChannel):
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, notification: Notification) -> None:
        self.calls += 1
        raise GotifyError("boom")


class _CapturingChannel(NotificationChannel):
    name = "capture"

    def __init__(self) -> None:
        self.received: list[Notification] = []

    def deliver(self, notification: Notification) -> None:
        self.received.append(notification)


def test_dispatch_isolates_a_failing_channel() -> None:
    """A channel that raises never blocks the others (the activity-path guard)."""
    flaky, good = _FlakyChannel(), _CapturingChannel()
    broker = NotificationBroker(channels=[flaky, good], clock=lambda: T0)
    n = _state_notification()
    broker.dispatch(n)  # must not raise
    assert flaky.calls == 1
    assert good.received == [n]


class _FakeBus:
    """A minimal stand-in for ``ActivityService.subscribe`` — one subscriber."""

    def __init__(self) -> None:
        self.callback: object = None

    def subscribe(self, callback: object) -> object:
        self.callback = callback
        return lambda: setattr(self, "callback", None)


def test_bind_delivers_edge_through_the_dispatch_pool() -> None:
    """End-to-end of the async path: a bus delta on the rising edge reaches the
    channel via the dispatch worker, and ``close`` unsubscribes + stops it."""
    bus = _FakeBus()
    channel = _CapturingChannel()
    broker = NotificationBroker(channels=[channel], clock=lambda: T0, waiting_quiet=timedelta(0))
    broker.bind(bus.subscribe)  # type: ignore[arg-type]
    try:
        assert callable(bus.callback)
        bus.callback(_delta(_session(AgentActivityState.WORKING)))  # type: ignore[operator]  # seed
        bus.callback(_delta(_session(AgentActivityState.WAITING)))  # type: ignore[operator]  # fire
        deadline = time.monotonic() + 2.0
        while not channel.received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(channel.received) == 1
        assert channel.received[0].state is AgentActivityState.WAITING
    finally:
        broker.close()
    assert bus.callback is None  # unsubscribed on close


def test_bind_delivers_a_lifecycle_delta_too() -> None:
    """The lifecycle arm rides the same bus + pool — no second wiring path."""
    bus = _FakeBus()
    channel = _CapturingChannel()
    broker = NotificationBroker(channels=[channel], clock=lambda: T0)
    broker.bind(bus.subscribe)  # type: ignore[arg-type]
    try:
        assert callable(bus.callback)
        bus.callback(_lifecycle("orphaned_detected"))  # type: ignore[operator]
        deadline = time.monotonic() + 2.0
        while not channel.received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [n.trigger for n in channel.received] == ["lifecycle"]
    finally:
        broker.close()


# ─── notification builders for the channel tests (real constructors) ──────────


def _state_notification(deep_link_base: str = DEEP_LINK_BASE) -> Notification:
    """A WAITING agent-state edge — severity ``normal``."""
    session = _session(AgentActivityState.WAITING, task="ran the suite")
    return Notification.from_activity(
        _row(session),
        session,
        state=AgentActivityState.WAITING,
        deep_link_base=deep_link_base,
        now=T0,
    )


def _question_notification() -> Notification:
    """A question edge — severity ``high``, carries the prompt + options."""
    question = _question("q1")
    session = _session(AgentActivityState.BLOCKED, task="deploying", questions=(question,))
    return Notification.for_questions(
        _row(session),
        session,
        questions=(question,),
        deep_link_base=DEEP_LINK_BASE,
        now=T0,
    )


def _lifecycle_notification(event: str = "paused") -> Notification:
    """A lifecycle edge — ``paused`` is the routine, severity ``low`` one."""
    return Notification.for_lifecycle(
        WorkspaceIdentity.from_state(_ws_state()),
        event=event,
        detail={},
        deep_link_base=DEEP_LINK_BASE,
        now=T0,
    )


class _Capture:
    """Records the one request an ``httpx.MockTransport`` handler sees."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.url = ""
        self.headers: httpx.Headers = httpx.Headers()
        self.body: dict[str, object] = {}

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.url = str(request.url)
            self.headers = request.headers
            self.body = json.loads(request.content)
            return httpx.Response(self.status, json={"id": 1})

        return httpx.MockTransport(handler)


# ─── Gotify channel ───────────────────────────────────────────────────────────


def test_gotify_posts_markdown_message_with_token_and_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "secret-app-token")
    cap = _Capture()
    cfg = GotifyChannelConfig(enabled=True, server_url="https://gotify.example.com/")
    notification = _state_notification()
    GotifyNotificationChannel(cfg, transport=cap.transport()).deliver(notification)

    assert cap.url == "https://gotify.example.com/message"
    assert cap.headers.get("X-Gotify-Key") == "secret-app-token"
    assert cap.body["title"] == "proj · fix-auth"
    assert cap.body["message"] == notification.markdown()  # the rich body, not the plain one
    assert cap.body["priority"] == 5  # severity "normal" → the default map
    extras = cap.body["extras"]
    assert isinstance(extras, dict)
    assert extras["client::display"] == {"contentType": "text/markdown"}
    assert extras["client::notification"]["click"]["url"] == "https://grove.example.com/w/ws1"


def test_gotify_maps_severity_onto_its_priority_dial(monkeypatch: pytest.MonkeyPatch) -> None:
    """The urgency seam: a pending question lands at 8 (heads-up + sound on Android),
    a routine pause at 2 (silent). One config map, no per-channel policy branch."""
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com")

    high = _Capture()
    GotifyNotificationChannel(cfg, transport=high.transport()).deliver(_question_notification())
    assert high.body["priority"] == 8
    assert "Deploy" in str(high.body["message"])  # the question rides into the body

    low = _Capture()
    GotifyNotificationChannel(cfg, transport=low.transport()).deliver(_lifecycle_notification())
    assert low.body["priority"] == 2


def test_gotify_priority_map_is_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-tuning the dial is config, never a code change; an unmapped severity falls
    back to ``priority``."""
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cfg = GotifyChannelConfig(
        enabled=True,
        server_url="https://g.example.com",
        priorities={"high": 10},
        priority=1,
    )
    high, normal = _Capture(), _Capture()
    GotifyNotificationChannel(cfg, transport=high.transport()).deliver(_question_notification())
    GotifyNotificationChannel(cfg, transport=normal.transport()).deliver(_state_notification())
    assert high.body["priority"] == 10
    assert normal.body["priority"] == 1  # "normal" is unmapped → the fallback


def test_gotify_markdown_off_sends_the_plain_body_and_no_display_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cap = _Capture()
    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com", markdown=False)
    notification = _question_notification()
    GotifyNotificationChannel(cfg, transport=cap.transport()).deliver(notification)

    assert cap.body["message"] == notification.body()
    assert "**" not in str(cap.body["message"])
    extras = cap.body["extras"]
    assert isinstance(extras, dict)
    assert "client::display" not in extras  # nothing to render → do not claim markdown
    assert extras["client::notification"]["click"]["url"] == "https://grove.example.com/w/ws1"


def test_gotify_omits_click_when_no_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cap = _Capture()
    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com")
    GotifyNotificationChannel(cfg, transport=cap.transport()).deliver(_state_notification(""))
    extras = cap.body["extras"]
    assert isinstance(extras, dict)
    assert "client::notification" not in extras
    assert extras["client::display"] == {"contentType": "text/markdown"}


def test_gotify_raises_typed_error_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_GOTIFY_TOKEN", "t")
    cfg = GotifyChannelConfig(enabled=True, server_url="https://g.example.com")
    channel = GotifyNotificationChannel(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized"))
    )
    with pytest.raises(GotifyError):
        channel.deliver(_state_notification())


# ─── webhook channel ──────────────────────────────────────────────────────────


def test_webhook_posts_json_with_topic_and_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROVE_NTFY_TOKEN", "tk_abc")
    cap = _Capture()
    cfg = WebhookChannelConfig(
        enabled=True, url="https://ntfy.sh", token_env="GROVE_NTFY_TOKEN", topic="grove-alerts"
    )
    notification = _state_notification()
    WebhookNotificationChannel(cfg, transport=cap.transport()).deliver(notification)

    assert cap.url == "https://ntfy.sh"
    assert cap.headers.get("Authorization") == "Bearer tk_abc"
    assert cap.body["topic"] == "grove-alerts"
    assert cap.body["click"] == "https://grove.example.com/w/ws1"
    assert cap.body["state"] == "waiting"
    assert cap.body["workspace_id"] == "ws1"
    assert cap.body["repo"] == "proj"
    assert cap.body["branch"] == "grove/fix-auth"
    assert cap.body["trigger"] == "agent_state"
    assert cap.body["event"] == "waiting"
    assert cap.body["severity"] == "normal"
    assert cap.body["tags"] == ["waiting", "normal"]
    assert cap.body["markdown"] == notification.markdown()
    assert cap.body["message"] == notification.body()
    assert cap.body["questions"] == []


def test_webhook_priority_is_an_int_on_ntfys_scale() -> None:
    """ntfy's ``priority`` field is an integer 1-5, not a state string — severity
    maps through config onto that scale."""
    cfg = WebhookChannelConfig(enabled=True, url="https://ntfy.sh", topic="t")
    high, normal, low = _Capture(), _Capture(), _Capture()
    WebhookNotificationChannel(cfg, transport=high.transport()).deliver(_question_notification())
    WebhookNotificationChannel(cfg, transport=normal.transport()).deliver(_state_notification())
    WebhookNotificationChannel(cfg, transport=low.transport()).deliver(_lifecycle_notification())
    assert [high.body["priority"], normal.body["priority"], low.body["priority"]] == [4, 3, 2]
    assert all(isinstance(cap.body["priority"], int) for cap in (high, normal, low))


def test_webhook_serializes_the_open_questions() -> None:
    """A relay that wants to *answer* the question needs the id, the options, and
    the multiselect flag — not just the prose body."""
    cap = _Capture()
    cfg = WebhookChannelConfig(enabled=True, url="https://h.example.com/hook")
    WebhookNotificationChannel(cfg, transport=cap.transport()).deliver(_question_notification())

    assert cap.body["trigger"] == "question"
    assert cap.body["severity"] == "high"
    assert cap.body["questions"] == [
        {
            "id": "q1",
            "kind": "single_select",
            "prompt": "Ship it?",
            "header": "Deploy",
            "multiselect": False,
            "options": [
                {"label": "Yes", "description": "ship to prod"},
                {"label": "No", "description": None},
            ],
        }
    ]


def test_webhook_lifecycle_body_has_no_state() -> None:
    """A lifecycle push has no agent-state axis at all — the field is honestly null."""
    cap = _Capture()
    cfg = WebhookChannelConfig(enabled=True, url="https://h.example.com/hook")
    WebhookNotificationChannel(cfg, transport=cap.transport()).deliver(
        _lifecycle_notification("orphaned_detected")
    )
    assert cap.body["state"] is None
    assert cap.body["trigger"] == "lifecycle"
    assert cap.body["event"] == "orphaned_detected"


def test_webhook_raises_typed_error_on_5xx() -> None:
    cfg = WebhookChannelConfig(enabled=True, url="https://h.example.com/hook")
    channel = WebhookNotificationChannel(
        cfg, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(WebhookError):
        channel.deliver(_state_notification())


# ─── from_config factory ────────────────────────────────────────────────────


def test_from_config_disabled_returns_none() -> None:
    assert NotificationBroker.from_config(GroveConfig().notifications) is None


def test_from_config_enabled_with_no_channel_returns_none() -> None:
    cfg = GroveConfig.model_validate({"notifications": {"enabled": True}})
    assert NotificationBroker.from_config(cfg.notifications) is None


def test_from_config_builds_only_enabled_channels_and_resolves_states() -> None:
    cfg = GroveConfig.model_validate(
        {
            "notifications": {
                "enabled": True,
                "on": ["waiting", "error"],
                "gotify": {"enabled": True, "server_url": "https://g.example.com"},
                "webhook": {"enabled": False, "url": "https://h.example.com"},
            }
        }
    )
    broker = NotificationBroker.from_config(cfg.notifications)
    assert broker is not None
    assert [c.name for c in broker.channels] == ["gotify"]
    # The "on" set coerced to the enum and seeded the edge detector.
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert broker.evaluate(_delta(_session(AgentActivityState.BLOCKED))) == []  # not in "on"
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))
    assert len(broker.evaluate(_delta(_session(AgentActivityState.ERROR)))) == 1


def test_from_config_defaults_push_questions_and_the_interruption_lifecycle() -> None:
    """The out-of-the-box policy: questions on, lifecycle narrowed to the events the
    user did NOT initiate (a pause needs no push back to whoever paused it)."""
    cfg = GroveConfig.model_validate(
        {
            "notifications": {
                "enabled": True,
                "webhook": {"enabled": True, "url": "https://h.example.com"},
            }
        }
    )
    broker = NotificationBroker.from_config(cfg.notifications)
    assert broker is not None
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed
    out = broker.evaluate(
        _delta(_session(AgentActivityState.WORKING, questions=(_question("q1"),)))
    )
    assert [n.trigger for n in out] == ["question"]
    # Distinct workspaces: the debounce is per-workspace and shared across triggers,
    # so ws1's question fire (real clock, no injection through from_config) would
    # otherwise swallow a lifecycle push landing in the same window.
    assert broker.evaluate(_lifecycle("paused", workspace_id="ws2")) == []  # routine verb: off
    assert len(broker.evaluate(_lifecycle("offline_detected", workspace_id="ws3"))) == 1


def test_default_deep_link_base_is_the_local_webapp() -> None:
    """The whole point of a push is the tap that follows it. An unset base URL used
    to mean *no link at all*, so an out-of-the-box notification was a dead end. The
    default is the webapp's own local origin (its `next start` port), which is right
    for the overwhelmingly common single-host install and is overridden by one line
    of config for a reachable/remote one."""
    assert GroveConfig().notifications.deep_link_base_url == "http://localhost:3000"


def test_a_default_config_broker_produces_a_tappable_link() -> None:
    """End to end through the factory: default config in, deep link out.

    Driven by a QUESTION rather than a WAITING edge, and the difference is the
    whole point of `waiting_quiet_minutes`. A default-config broker deliberately
    does NOT push the instant a turn stops generating — it waits out the quiet
    window first, because "the top-level turn ended" is not "the work is done"
    while a backgrounded shell command Grove cannot see may still be running. A
    question is the one trigger exempt from both the debounce and that window,
    so it is the only edge that fires immediately under real defaults, which is
    exactly why it is the right one to prove the link through the factory.

    The seeding delta is not ceremony: `from_config` injects no clock, so the
    broker is genuinely COLD, and a cold broker treats the first question it
    ever sees on a session as one that predates it (the daemon-restart guard).
    Seeding the session first is what makes the second delta a real edge.
    """
    cfg = GroveConfig(
        notifications={  # type: ignore[arg-type]
            "enabled": True,
            "gotify": {"enabled": True, "server_url": "https://gotify.example.com"},
        }
    ).notifications
    broker = NotificationBroker.from_config(cfg)
    assert broker is not None
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed
    out = broker.evaluate(
        _delta(_session(AgentActivityState.WORKING, questions=(_question("q1"),)))
    )
    assert out[0].deep_link == "http://localhost:3000/w/ws1"


@pytest.mark.parametrize(
    ("base", "loopback"),
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://[::1]:3000", True),
        ("http://0.0.0.0:3000", True),
        ("https://grove.example.com", False),
        ("http://192.168.1.10:3000", False),  # a LAN address IS reachable from a phone
        ("", False),  # no link rendered at all — nothing to warn about
    ],
)
def test_a_loopback_deep_link_knows_it_is_unreachable(base: str, loopback: bool) -> None:
    """The feature's quietest failure: you tap the push on your phone and land on
    *the phone's own* localhost. Nothing errors — the tap just dies. The config can
    tell you that about itself, so the daemon can say it out loud at startup."""
    assert NotificationsConfig(deep_link_base_url=base).deep_link_is_loopback is loopback


def test_from_config_resolves_on_question_and_on_lifecycle() -> None:
    """Both new switches cascade like everything else — off/narrowed by config alone."""
    cfg = GroveConfig.model_validate(
        {
            "notifications": {
                "enabled": True,
                "on_question": False,
                "on_lifecycle": ["paused"],
                "gotify": {"enabled": True, "server_url": "https://g.example.com"},
            }
        }
    )
    broker = NotificationBroker.from_config(cfg.notifications)
    assert broker is not None
    broker.evaluate(_delta(_session(AgentActivityState.WORKING)))  # seed
    out = broker.evaluate(
        _delta(_session(AgentActivityState.BLOCKED, questions=(_question("q1"),)))
    )
    assert [n.trigger for n in out] == ["agent_state"]  # questions disabled → the state edge only
    # Distinct workspaces: ws1 just fired, and the debounce is per-workspace.
    assert broker.evaluate(_lifecycle("error", workspace_id="ws2", error="boom")) == []
    assert len(broker.evaluate(_lifecycle("paused", workspace_id="ws3"))) == 1
