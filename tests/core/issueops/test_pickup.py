"""The assignee work queue: durable marker, ceiling, and the self-trigger loop.

In-memory fakes only — no threads, no network, no git. The poller is driven
through :meth:`AssigneePoller.tick`, the seam that exists precisely so the timer
never has to fire in a test, and the fake provider folds ``can_assign`` from its
two real halves (``assignees_supported`` AND ``configured``) exactly as
``HttpTicketProvider`` does, so a test that trips one half exercises the real
predicate rather than a fake's convenience flag.

The test this file exists for is
``test_the_bots_own_assignment_does_not_re_trigger_pickup``: Grove assigns the
bot, the poll then sees a bot-assigned issue, and without a durable marker it
starts a second workspace, forever.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import (
    GiteaTicketConfig,
    GroveConfig,
    IssueOpsConfig,
    TicketsConfig,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketComment, TicketProviderName, TicketRef
from grove.core.errors import GroveError, TicketProviderError
from grove.core.issueops import AssigneePoller, HandoverKey, HandoverLog, PickupEngine
from grove.core.issueops.pickup import PickupCandidate
from grove.core.tickets.provider import TicketThread

T0 = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ROOT = Path("/repo")


# ─── fakes ───────────────────────────────────────────────────────────────────


class _FakeProvider:
    """A capturing ticket provider, with the real capability folds.

    ``can_assign`` and ``can_comment`` are computed from their two halves rather
    than set directly, so a test that makes the provider tokenless exercises the
    same predicate production does.
    """

    label = "Gitea"

    def __init__(
        self,
        *,
        name: TicketProviderName = "gitea",
        assigned: Sequence[TicketRef] = (),
        configured: bool = True,
        assignees_supported: bool = True,
        login: str = "grove-ai",
        list_error: Exception | None = None,
        assign_error: Exception | None = None,
        unassign_error: Exception | None = None,
        preassigned: bool = False,
    ) -> None:
        self.name: TicketProviderName = name
        self.configured = configured
        self.assignees_supported = assignees_supported
        self.comments_supported = True
        self.context = "acme/api"
        self._assigned = list(assigned)
        self._login = login
        self.list_error = list_error
        self.assign_error = assign_error
        self.unassign_error = unassign_error
        # Who is on the ticket. `preassigned` is a HUMAN having already assigned
        # the bot before Grove ever looked — the case that separates "Grove
        # assigned this" from "this is assigned".
        self.assignees: set[str] = {login} if preassigned else set()
        self.assigned_calls: list[str] = []
        self.unassigned_calls: list[str] = []
        self.list_calls = 0
        self.viewer_calls = 0

    @property
    def can_assign(self) -> bool:
        return self.assignees_supported and self.configured

    @property
    def can_comment(self) -> bool:
        return self.comments_supported and self.configured

    def viewer_login(self) -> str:
        self.viewer_calls += 1
        return self._login

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        del status
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self._assigned)

    def assign_self(self, ticket_id: str) -> bool:
        """Additive and idempotent, reporting whether THIS call added the login.

        The fake tracks a real assignee set rather than answering a fixed value,
        because "already assigned" is the state the release rule turns on: a
        double that always said "I assigned it" would let Grove claim — and then
        release — a ticket a human had assigned.
        """
        if self.assign_error is not None:
            raise self.assign_error
        if self._login in self.assignees:
            return False
        self.assignees.add(self._login)
        self.assigned_calls.append(ticket_id)
        return True

    def unassign_self(self, ticket_id: str) -> None:
        if self.unassign_error is not None:
            raise self.unassign_error
        self.assignees.discard(self._login)
        self.unassigned_calls.append(ticket_id)

    def read_thread(self, ticket_id: str) -> TicketThread:
        return TicketThread(
            ref=TicketRef(provider=self.name, id=ticket_id, title="Fix auth", url="u"),
            body="the description",
            comments=(TicketComment(id="1", body="a comment", author="alice"),),
        )


class _FakeProviders:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def providers(self) -> list[_FakeProvider]:
        return [self._provider]

    def get(self, name: TicketProviderName) -> _FakeProvider:
        del name
        return self._provider


class _FakeState:
    def __init__(self, ws_id: str, refs: Sequence[TicketRef]) -> None:
        self.id = ws_id
        self.title = "ws"
        self.ticket_refs = list(refs)


class _FakeManager:
    def __init__(self, cfg: GroveConfig, provider: _FakeProvider) -> None:
        self._cfg = cfg
        self.provider = provider
        self.held: dict[str, _FakeState] = {}
        self.states: list[_FakeState] = []
        self.creates: list[CreateWorkspaceRequest] = []
        self.create_error: Exception | None = None

    @property
    def config(self) -> GroveConfig:
        return self._cfg

    @property
    def repo_root(self) -> Path:
        return ROOT

    @property
    def ticket_providers(self) -> _FakeProviders:
        return _FakeProviders(self.provider)

    def list(self) -> list[_FakeState]:
        return list(self.states)

    def find_by_ticket(self, provider: str, ticket_id: str) -> _FakeState | None:
        del provider
        return self.held.get(ticket_id)

    def create(self, request: CreateWorkspaceRequest) -> _FakeState:
        if self.create_error is not None:
            raise self.create_error
        self.creates.append(request)
        ticket = request.ticket
        assert ticket is not None
        state = _FakeState(f"ws-{ticket.id}", [TicketRef(provider=ticket.provider, id=ticket.id)])
        self.states.append(state)
        return state


class _FakeRegistry:
    def __init__(self, manager: _FakeManager) -> None:
        self._manager = manager

    def known_roots(self) -> list[Path]:
        return [ROOT]

    def get(self, root: Path) -> _FakeManager:
        del root
        return self._manager


# ─── builders ────────────────────────────────────────────────────────────────


def _cfg(**issueops: Any) -> GroveConfig:
    return GroveConfig(
        issueops=IssueOpsConfig(**issueops),
        tickets=TicketsConfig(
            gitea=GiteaTicketConfig(enabled=True, owner="acme", repo="api", token_env="T")
        ),
    )


def _key(ticket_id: str) -> HandoverKey:
    return HandoverKey(provider="gitea", owner="acme", repo="api", ticket_id=ticket_id)


def _poller(
    tmp_path: Path,
    provider: _FakeProvider,
    **issueops: Any,
) -> tuple[AssigneePoller, _FakeManager, HandoverLog]:
    issueops.setdefault("pickup_enabled", True)
    mgr = _FakeManager(_cfg(**issueops), provider)
    log = HandoverLog(tmp_path / "handovers.json")
    poller = AssigneePoller(
        config=mgr.config.issueops,
        registry=_FakeRegistry(mgr),  # type: ignore[arg-type]
        engine=PickupEngine(log=log),
        clock=lambda: T0,
    )
    return poller, mgr, log


# ─── the durable marker ─────────────────────────────────────────────────────


def test_claim_is_durable_across_instances(tmp_path: Path) -> None:
    log = HandoverLog(tmp_path / "h.json")
    assert log.claim(_key("42"), source="poll", now=T0) is True
    assert HandoverLog(tmp_path / "h.json").contains(_key("42")) is True


def test_claim_is_the_race_arbiter(tmp_path: Path) -> None:
    """Only the first writer gets True — which is what makes claim-before-create safe."""
    log = HandoverLog(tmp_path / "h.json")
    assert log.claim(_key("42"), source="poll", now=T0) is True
    assert log.claim(_key("42"), source="command", now=T0) is False


def test_a_corrupt_log_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """Reading empty here IS the loop: every ticket would look never-handed."""
    path = tmp_path / "h.json"
    path.write_text("{ not json")
    with pytest.raises(GroveError, match="corrupt handover log"):
        HandoverLog(path).keys()


def test_an_unreadable_log_skips_the_whole_tick(tmp_path: Path) -> None:
    """Fail closed: no marker read, no pickup — never "nothing was handed over"."""
    (tmp_path / "handovers.json").write_text("{ not json")
    provider = _FakeProvider(assigned=[TicketRef(provider="gitea", id="42")])
    poller, mgr, _ = _poller(tmp_path, provider)
    plan = poller.tick()
    assert (plan.take, plan.defer) == ((), ())
    assert mgr.creates == []


# ─── the self-trigger loop, which is the whole design risk ──────────────────


def test_the_bots_own_assignment_does_not_re_trigger_pickup(tmp_path: Path) -> None:
    """Grove assigns → the poll sees an assigned issue → it must NOT start again.

    The second tick runs with the workspace already killed (nothing holds the
    ticket) and the issue still assigned to the bot — exactly the state where an
    answer derived from the tracker would loop. Only the durable marker stops it.
    """
    provider = _FakeProvider(assigned=[TicketRef(provider="gitea", id="42")])
    poller, mgr, _ = _poller(tmp_path, provider)

    first = poller.tick()
    assert len(first.take) == 1
    assert len(mgr.creates) == 1

    mgr.held.clear()  # the workspace is gone; the assignment is not
    mgr.states.clear()
    second = poller.tick()
    assert (second.take, second.defer) == ((), ())
    assert len(mgr.creates) == 1


def test_an_assignment_later_removed_still_counts_as_handed_over(tmp_path: Path) -> None:
    """The marker is durable, not a read of current state."""
    provider = _FakeProvider(assigned=[TicketRef(provider="gitea", id="42")])
    poller, mgr, log = _poller(tmp_path, provider)
    poller.tick()

    # A human strips every assignee and later puts the bot back.
    mgr.held.clear()
    assert log.contains(_key("42")) is True
    assert poller.tick().take == ()


def test_a_live_workspace_blocks_pickup_and_counts_toward_the_ceiling(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(
        assigned=[TicketRef(provider="gitea", id="42"), TicketRef(provider="gitea", id="43")]
    )
    poller, mgr, _ = _poller(tmp_path, provider, pickup_max_active=1)
    mgr.held["42"] = _FakeState("ws-42", [TicketRef(provider="gitea", id="42")])
    plan = poller.tick()
    assert plan.active == 1
    assert (plan.take, [c.key.ticket_id for c in plan.defer]) == ((), ["43"])


# ─── the ceiling ────────────────────────────────────────────────────────────


def test_plan_defers_rather_than_dropping_the_overflow() -> None:
    """The (N+1)th is DEFERRED and nameable — never silently lost."""
    candidates = [
        PickupCandidate(repo_root=ROOT, key=_key(i), ref=TicketRef(provider="gitea", id=i))
        for i in ("1", "2", "3", "4")
    ]
    plan = PickupEngine.plan(candidates, active=1, ceiling=3)
    assert len(plan.take) == 2
    assert len(plan.defer) == 2
    assert {c.key.ticket_id for c in plan.take} | {c.key.ticket_id for c in plan.defer} == {
        "1",
        "2",
        "3",
        "4",
    }


def test_a_full_fleet_takes_nothing() -> None:
    candidates = [
        PickupCandidate(repo_root=ROOT, key=_key("9"), ref=TicketRef(provider="gitea", id="9"))
    ]
    plan = PickupEngine.plan(candidates, active=5, ceiling=3)
    assert plan.take == () and len(plan.defer) == 1


def test_the_ceiling_bounds_one_tick(tmp_path: Path) -> None:
    provider = _FakeProvider(
        assigned=[TicketRef(provider="gitea", id=str(n)) for n in range(1, 11)]
    )
    poller, mgr, _ = _poller(tmp_path, provider, pickup_max_active=3)
    plan = poller.tick()
    assert len(plan.take) == 3
    assert len(plan.defer) == 7
    assert len(mgr.creates) == 3


# ─── eligibility ────────────────────────────────────────────────────────────


def test_a_closed_or_pull_request_ref_is_never_picked_up(tmp_path: Path) -> None:
    provider = _FakeProvider(
        assigned=[
            TicketRef(provider="gitea", id="1", status="closed"),
            TicketRef(provider="gitea", id="2", kind="pull_request"),
            TicketRef(provider="gitea", id="3"),
        ]
    )
    poller, _, _ = _poller(tmp_path, provider)
    assert [c.key.ticket_id for c in poller.tick().take] == ["3"]


def test_pickup_disabled_polls_nothing(tmp_path: Path) -> None:
    provider = _FakeProvider(assigned=[TicketRef(provider="gitea", id="42")])
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    poller.tick()
    assert provider.list_calls == 0
    assert mgr.creates == []


def test_from_config_is_none_until_a_half_is_enabled() -> None:
    registry = _FakeRegistry(_FakeManager(_cfg(), _FakeProvider()))
    assert AssigneePoller.from_config(IssueOpsConfig(), registry=registry) is None  # type: ignore[arg-type]
    assert (
        AssigneePoller.from_config(IssueOpsConfig(assign_bot=True), registry=registry)  # type: ignore[arg-type]
        is not None
    )
    assert (
        AssigneePoller.from_config(IssueOpsConfig(pickup_enabled=True), registry=registry)  # type: ignore[arg-type]
        is not None
    )


# ─── rate limits ────────────────────────────────────────────────────────────


def test_a_provider_error_backs_the_provider_off_and_stops_hammering(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(list_error=TicketProviderError("429 rate limited"))
    poller, _, _ = _poller(tmp_path, provider, pickup_backoff_seconds=300)
    poller.tick(T0)
    assert provider.list_calls == 1
    poller.tick(T0 + timedelta(seconds=60))
    assert provider.list_calls == 1  # still backed off — no second call
    provider.list_error = None
    poller.tick(T0 + timedelta(seconds=301))
    assert provider.list_calls == 2


def test_a_successful_poll_clears_the_backoff(tmp_path: Path) -> None:
    provider = _FakeProvider(list_error=TicketProviderError("boom"))
    poller, _, _ = _poller(tmp_path, provider, pickup_backoff_seconds=10)
    poller.tick(T0)
    provider.list_error = None
    poller.tick(T0 + timedelta(seconds=11))
    poller.tick(T0 + timedelta(seconds=12))
    assert provider.list_calls == 3


def test_an_unconfigured_provider_is_never_polled(tmp_path: Path) -> None:
    provider = _FakeProvider(configured=False)
    poller, _, _ = _poller(tmp_path, provider)
    poller.tick()
    assert provider.list_calls == 0


# ─── hand over / hand back ──────────────────────────────────────────────────


def test_hand_over_attaches_the_ticket_at_creation_and_bakes_the_thread_in(
    tmp_path: Path,
) -> None:
    """Nothing publishes for an unattached ref, so a late attach loses history."""
    provider = _FakeProvider()
    mgr = _FakeManager(_cfg(), provider)
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    engine.hand_over(mgr, key=_key("42"), source="command", now=T0)  # type: ignore[arg-type]

    request = mgr.creates[0]
    assert request.ticket is not None
    assert (request.ticket.provider, request.ticket.id) == ("gitea", "42")
    prompt = request.initial_prompt or ""
    assert "the description" in prompt
    assert "a comment" in prompt  # the {comments} placeholder rendered the thread
    assert "alice" in prompt


def test_hand_over_claims_before_it_creates(tmp_path: Path) -> None:
    """A create that dies must not leave an unclaimed ticket for the next tick."""
    provider = _FakeProvider()
    mgr = _FakeManager(_cfg(), provider)
    mgr.create_error = GroveError("boom")
    log = HandoverLog(tmp_path / "h.json")
    with pytest.raises(GroveError):
        PickupEngine(log=log).hand_over(mgr, key=_key("42"), source="poll", now=T0)  # type: ignore[arg-type]
    assert log.contains(_key("42")) is True


def test_hand_over_assigns_the_bot_on_the_way(tmp_path: Path) -> None:
    provider = _FakeProvider()
    mgr = _FakeManager(_cfg(), provider)
    PickupEngine(log=HandoverLog(tmp_path / "h.json")).hand_over(
        mgr,  # type: ignore[arg-type]
        key=_key("42"),
        source="command",
        now=T0,
    )
    assert provider.assigned_calls == ["42"]


def test_hand_back_unassigns_but_keeps_the_marker(tmp_path: Path) -> None:
    """Dropping the marker would let the very next tick take the ticket back."""
    provider = _FakeProvider()
    mgr = _FakeManager(_cfg(), provider)
    log = HandoverLog(tmp_path / "h.json")
    engine = PickupEngine(log=log)
    engine.hand_back(mgr, key=_key("42"), now=T0)  # type: ignore[arg-type]
    assert provider.unassigned_calls == ["42"]
    assert log.contains(_key("42")) is True


def test_owned_reports_the_workspace_it_started(tmp_path: Path) -> None:
    provider = _FakeProvider()
    mgr = _FakeManager(_cfg(), provider)
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    engine.hand_over(mgr, key=_key("42"), source="command", now=T0)  # type: ignore[arg-type]
    assert engine.owned() == [(_key("42"), "ws-42", T0)]


# ─── the outbound half ──────────────────────────────────────────────────────


def test_assignment_is_reconciled_over_live_workspaces_once(tmp_path: Path) -> None:
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()
    poller.tick()
    assert provider.assigned_calls == ["7"]  # memoized — one round-trip, not one per tick


def test_a_pull_request_ref_is_never_assigned(tmp_path: Path) -> None:
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7", kind="pull_request")])]
    poller.tick()
    assert provider.assigned_calls == []


def test_the_publish_edge_never_assigns_a_pull_request(tmp_path: Path) -> None:
    """The edge seam must apply the issues-only rule the reconcile sweep applies.

    `assign_now` is what the status publisher calls, and the publisher mirrors
    onto EVERY ref it holds — the pull request included.
    """
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    assert poller.assign_now(str(mgr.repo_root), "gitea", "7", "pull_request") is False
    assert provider.assigned_calls == []


def test_a_pull_request_assigned_on_the_edge_does_not_flap(tmp_path: Path) -> None:
    """The defect this closes: an assign/unassign storm once per poll interval.

    The edge seam assigned a pull request and recorded it as Grove-owned, while
    the reconcile sweep skipped pull requests and so never counted it as live —
    so the very next tick released it, and the next publish assigned it again,
    forever, on somebody else's tracker.
    """
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7", kind="pull_request")])]
    poller.assign_now(str(mgr.repo_root), "gitea", "7", "pull_request")
    poller.tick()
    poller.tick()
    assert provider.assigned_calls == []
    assert provider.unassigned_calls == []


def test_a_provider_that_cannot_assign_degrades_without_failing(tmp_path: Path) -> None:
    """A missing permission costs the assignment and nothing else."""
    provider = _FakeProvider(assignees_supported=False)
    mgr = _FakeManager(_cfg(), provider)
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    assert engine.assign_bot(provider, "42") is False  # type: ignore[arg-type]
    engine.hand_over(mgr, key=_key("42"), source="command", now=T0)  # type: ignore[arg-type]
    assert len(mgr.creates) == 1  # the workspace still got made


def test_a_forge_refusal_to_assign_is_swallowed(tmp_path: Path) -> None:
    provider = _FakeProvider(assign_error=TicketProviderError("403 write access required"))
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    assert engine.assign_bot(provider, "42") is False  # type: ignore[arg-type]


# ─── releasing the assignment when the work ends ────────────────────────────


def test_the_assignment_is_released_when_the_workspace_ends(tmp_path: Path) -> None:
    """The board must say who is working a ticket NOW, not who once did."""
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()
    assert provider.assigned_calls == ["7"]

    mgr.states = []  # the workspace was killed — its record is gone
    poller.tick()
    assert provider.unassigned_calls == ["7"]


def test_a_ticket_grove_never_assigned_is_never_released(tmp_path: Path) -> None:
    """A human assigning the bot by hand must not have it undone on the next tick.

    The memo is what separates the two: it holds only what THIS process assigned,
    so anything else on the tracker is somebody else's decision.
    """
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = []
    poller.tick()
    assert provider.unassigned_calls == []


def test_an_assignment_a_human_already_made_is_never_released(tmp_path: Path) -> None:
    """The regression that matters: reproduced against a real forge before it was fixed.

    A human assigns the bot, then hands Grove the ticket. Grove's own assign is
    idempotent, so it sends nothing and the finished state is identical to one
    Grove created — which is exactly why "is it assigned" cannot stand in for
    "did I assign it". Taking the first reading let the release strip assignees a
    person had put there by hand, on live issues.
    """
    provider = _FakeProvider(preassigned=True)
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()
    assert provider.assigned_calls == []  # already there — nothing to send

    mgr.states = []  # the workspace ends
    poller.tick()
    assert provider.unassigned_calls == []
    assert provider.assignees == {"grove-ai"}  # the human's decision survives


def test_a_live_ticket_is_not_released_while_its_workspace_holds_it(tmp_path: Path) -> None:
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()
    poller.tick()
    assert provider.unassigned_calls == []


def test_an_unreadable_repo_releases_nothing(tmp_path: Path) -> None:
    """Failing to READ a repo is not the same fact as its workspaces having ended.

    Treating the error as "no live workspaces" would unassign every ticket on
    that repo over a transient config error — so the release skips a repo that
    did not answer, and the assignment survives to be re-checked next tick.
    """
    provider = _FakeProvider()
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()

    def _boom() -> list[_FakeState]:
        raise GroveError("unreadable .grove/config.json")

    mgr.list = _boom  # type: ignore[method-assign]
    poller.tick()
    assert provider.unassigned_calls == []


def test_releasing_is_not_a_hand_back_and_keeps_no_marker(tmp_path: Path) -> None:
    """Grove finishing is not a human saying "do not take this again"."""
    provider = _FakeProvider()
    poller, mgr, log = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    poller.tick()
    mgr.states = []
    poller.tick()
    assert provider.unassigned_calls == ["7"]
    assert log.contains(_key("7")) is False


def test_a_provider_that_cannot_assign_releases_nothing(tmp_path: Path) -> None:
    provider = _FakeProvider(assignees_supported=False)
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    assert engine.release_bot(provider, "42") is False  # type: ignore[arg-type]
    assert provider.unassigned_calls == []


def test_a_forge_refusal_to_release_is_swallowed(tmp_path: Path) -> None:
    """A failed release costs one stale assignee, never the tick."""
    provider = _FakeProvider(unassign_error=TicketProviderError("403 write access required"))
    engine = PickupEngine(log=HandoverLog(tmp_path / "h.json"))
    assert engine.release_bot(provider, "42") is False  # type: ignore[arg-type]


def test_assignment_alone_never_creates_a_workspace(tmp_path: Path) -> None:
    """Assignment is an OUTPUT of Grove working a ticket, never an input.

    The whole point of the two flags being independent: a deployment that wants
    board transparency must not thereby get an agent spawned by whatever appears
    in the tracker's assignee field.
    """
    provider = _FakeProvider(assigned=[TicketRef(provider="gitea", id="99")])
    poller, mgr, _ = _poller(tmp_path, provider, pickup_enabled=False, assign_bot=True)
    mgr.states = [_FakeState("ws1", [TicketRef(provider="gitea", id="7")])]
    plan = poller.tick()
    assert mgr.creates == []
    assert plan.take == ()


# ─── the identity the poll runs as ──────────────────────────────────────────


def test_the_polls_identity_is_resolved_once(tmp_path: Path) -> None:
    """Whose queue this is gets said out loud — and asked for exactly once."""
    provider = _FakeProvider(assigned=[])
    poller, _, _ = _poller(tmp_path, provider)
    poller.tick(T0)
    poller.tick(T0 + timedelta(seconds=60))
    assert provider.viewer_calls == 1
