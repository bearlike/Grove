"""Send one configured trigger rule on demand, to the workspaces a selector picks.

The manual half of the trigger backbone, and the reason it shares one watch
rather than owning a second road: a rule sent by ``grove trigger fire`` and the
same rule fired by the scheduler must count against ONE ``max_sends`` cap and
reach a session through ONE delivery that refuses a dead one. So a dispatch
never sends anything itself; it finds each matched workspace's standing trigger
watch and asks the scheduler to advance it.

Only a RUNNING workspace holds a trigger watch, so a paused, killed or errored
one is reported ``skipped`` without being touched, and the live-status check in
:meth:`TriggerWatcher.fire` still refuses a running workspace whose session has
gone offline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from grove.core.config import TriggerRule
from grove.core.contracts.watches import (
    TriggerFireRequest,
    TriggerFireResult,
    TriggerFireView,
    TriggerPredicate,
    TriggerTarget,
    WatchOutcome,
    WatchPredicate,
    WatchView,
)
from grove.core.errors import GroveError
from grove.core.phase import PhaseReport
from grove.core.watches.scheduler import WatchScheduler
from grove.core.watches.triggers import TriggerWatcher
from grove.core.workspace import WorkspaceState

#: Every workspace on the host, each with its current phase report and its repo's rules.
Fleet = Callable[[], Iterable[tuple[WorkspaceState, PhaseReport | None, tuple[TriggerRule, ...]]]]


class TriggerDispatch:
    """Resolve a selector to workspaces and advance each one's trigger watch."""

    def __init__(self, *, scheduler: WatchScheduler, watcher: TriggerWatcher, fleet: Fleet) -> None:
        self._scheduler = scheduler
        self._watcher = watcher
        self._fleet = fleet

    def fire(self, request: TriggerFireRequest) -> TriggerFireView:
        """Send ``request.rule`` to every matched workspace and report each outcome.

        Raises :class:`GroveError` for a selector that names nothing, so an
        empty filter can never broadcast by accident.
        """
        if request.target.is_empty:
            raise GroveError("name the workspaces to reach: ids, a filter, or `all`")
        watches = {
            row.recipient.workspace_id: row.id
            for row in self._scheduler.list().watches
            if row.state == "pending" and row.predicate.kind == "trigger"
        }
        results: list[TriggerFireResult] = []
        for state, phase, rules in self._fleet():
            if not self.matches(request.target, state, phase):
                continue
            rule = next((item for item in rules if item.name == request.rule), None)
            receipt, detail = self._send(watches.get(state.id), rule, request.rule)
            results.append(
                TriggerFireResult(
                    workspace_id=state.id, title=state.title, receipt=receipt, detail=detail
                )
            )
        return TriggerFireView(rule=request.rule, results=results)

    def _send(
        self, watch_id: str | None, rule: TriggerRule | None, name: str
    ) -> tuple[str, str | None]:
        if rule is None:
            return "skipped", f"no trigger rule named {name!r} in this workspace's config"
        if not rule.enabled:
            return "skipped", f"trigger rule {name!r} is disabled"

        def step(row: WatchView) -> tuple[WatchPredicate, WatchOutcome] | str:
            predicate = row.predicate
            assert isinstance(predicate, TriggerPredicate)  # filtered by kind above
            fired = self._watcher.fire(predicate, rule, self._scheduler.now())
            if isinstance(fired, str):
                return fired
            counted, text = fired
            return counted, WatchOutcome(ok=True, summary=text)

        # A workspace that is not RUNNING holds no trigger watch, and `advance`
        # is the one place that refuses a missing or settled one.
        return self._scheduler.advance(watch_id or "", step)

    @staticmethod
    def matches(target: TriggerTarget, state: WorkspaceState, phase: PhaseReport | None) -> bool:
        """Pure: whether *state* is one of the workspaces *target* names. Every field narrows."""
        checks = (
            not target.workspace_ids or state.id in target.workspace_ids,
            not target.repo or str(state.repo_root) == target.repo,
            not target.agent or state.agent_name == target.agent,
            not target.phase or (phase is not None and phase.phase == target.phase),
            not target.ticket or target.ticket in {ref.key for ref in state.ticket_refs},
            not target.title_prefix or state.title.startswith(target.title_prefix),
        )
        return not target.is_empty and all(checks)


__all__ = ["Fleet", "TriggerDispatch"]
