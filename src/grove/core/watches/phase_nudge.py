"""Remind a working agent to report its phase, and nobody else.

The reminder is cheap to send and expensive to send WRONGLY: delivery can wake a
session, a dead native session is revived by an ordinary message, and every
line lands in a transcript the agent re-reads each turn. So the whole module is
one question, answered by a pure function over evidence the daemon gathers:
*is this agent demonstrably working, has it reported a phase before, and has
that report stood still for a whole interval while the work went on?*

Each condition below rules out a case a timer alone would get wrong:

* **The workspace is live** (``ACTIVE``/``IDLE`` once reconciled). Paused,
  offline, errored, orphaned and provisioning workspaces are never reminded.
* **The primary agent is WORKING.** Idle, waiting on a person, blocked on a
  prompt, errored or still starting are all left alone.
* **The agent has reported**, which a seeded file does not count as: Grove
  seeds bare ``scope`` claims for attached tickets, so a claim counts only once
  it has moved past ``scope`` or carries a note.
* **The report is not terminal.** ``handoff`` means nothing is left to report,
  and ``blocked`` is the agent's own statement that it cannot go on.
* **The report has stood for a whole interval.**
* **The agent made progress during that interval**, read as the primary
  session's assistant replies plus tool calls moving between two checks. This
  is what separates working on from one long tool call or a stalled session,
  in both of which the counts stand still.
* **It has not been reminded about this report already.** One reminder per
  report: an agent that read the reminder and had nothing to change is not
  nagged every interval after.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from grove.core.agents.model import AgentActivityState
from grove.core.contracts.watches import PhaseNudgePredicate, WatchOutcome
from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.watches.watcher import StillWatching, Watcher
from grove.core.workspace import LIVE_STATUSES, WorkspaceStatus

if TYPE_CHECKING:
    from grove.core.activity import WorkspaceActivity


@dataclass(frozen=True, slots=True)
class NudgeEvidence:
    """What the daemon observed about one workspace, freshly, at one check."""

    status: WorkspaceStatus
    activity: AgentActivityState | None
    #: The primary session's assistant replies plus tool calls, or ``None`` with no session.
    progress: int | None
    phase: PhaseReport | None

    @classmethod
    def of(cls, row: WorkspaceActivity) -> NudgeEvidence:
        """Distil a freshly computed activity row into the facts the rule reads."""
        primary = row.primary
        return cls(
            status=row.state.status,
            activity=primary.state if primary else None,
            progress=primary.assistant_replies + primary.tool_calls if primary else None,
            phase=row.phase,
        )


#: Reads fresh evidence for a workspace id, or ``None`` when it cannot be told.
EvidenceLookup = Callable[[str], NudgeEvidence | None]


class PhaseNudgeWatcher(Watcher[PhaseNudgePredicate]):
    """The standing check behind the phase reminder. Never settles on its own."""

    kind = "phase_nudge"

    def __init__(self, evidence: EvidenceLookup, *, every: timedelta) -> None:
        self._evidence = evidence
        self._every = every

    def observe(self, predicate: PhaseNudgePredicate, now: datetime) -> StillWatching | None:
        """Read the evidence and apply :meth:`decide`. ``None`` when nothing can be told."""
        seen = self._evidence(predicate.workspace_id)
        if seen is None:
            return None
        return self.decide(predicate, seen, now=now, every=self._every)

    @staticmethod
    def decide(
        predicate: PhaseNudgePredicate,
        seen: NudgeEvidence,
        *,
        now: datetime,
        every: timedelta,
    ) -> StillWatching | None:
        """Pure: remind, record a new baseline silently, or change nothing.

        The baseline always moves to what was just seen, so the next check
        compares against THIS observation rather than an older one; that is
        what makes "progress during the interval" mean the last interval.
        """
        phase = seen.phase
        baseline = predicate.model_copy(
            update={
                "progress": seen.progress,
                "phase_at": phase.updated_at if phase else None,
            }
        )
        if PhaseNudgeWatcher._due(predicate, seen, now=now, every=every):
            assert phase is not None  # _due requires a report
            reminded = baseline.model_copy(update={"nudged_at": phase.updated_at})
            return StillWatching(reminded, PhaseNudgeWatcher._outcome(phase, now))
        return StillWatching(baseline) if baseline != predicate else None

    @staticmethod
    def _due(
        predicate: PhaseNudgePredicate,
        seen: NudgeEvidence,
        *,
        now: datetime,
        every: timedelta,
    ) -> bool:
        phase = seen.phase
        return (
            seen.status in LIVE_STATUSES
            and seen.activity is AgentActivityState.WORKING
            and phase is not None
            and (phase.phase != PHASE_ORDER[0] or phase.note is not None)
            and not phase.is_terminal
            and now - phase.updated_at >= every
            and predicate.nudged_at != phase.updated_at
            and predicate.progress is not None
            and seen.progress is not None
            and seen.progress != predicate.progress
        )

    @staticmethod
    def _outcome(phase: PhaseReport, now: datetime) -> WatchOutcome:
        minutes = int((now - phase.updated_at).total_seconds() // 60)
        return WatchOutcome(
            ok=True,
            summary=(
                "Automatic reminder from the Grove daemon, not from the user. "
                f"Your reported phase is still `{phase.phase}` ({minutes}m ago) while "
                "you keep working. If it moved, update it now with `grove phase <phase> "
                '--note "…"` (or the grove_set_workspace_phase tool); '
                "`grove skills show working-in-grove` has the details."
            ),
        )


__all__ = ["EvidenceLookup", "NudgeEvidence", "PhaseNudgeWatcher"]
