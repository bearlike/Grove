"""Put Grove's configured instructions into a working session, and nowhere else.

One standing watch per running workspace evaluates every rule in
``triggers.rules``. Each rule names a condition and a sentence; this module is
the one place that decides whether a condition holds, as a pure function over
evidence the daemon gathers. Sending is cheap and sending WRONGLY is not: a
dead native session is revived by an ordinary message, and every line lands in
a transcript the agent re-reads each turn. So every automatic rule shares one
gate before its own condition is consulted:

* **The workspace is live** (``ACTIVE``/``IDLE`` once reconciled). Paused,
  offline, errored, orphaned and provisioning workspaces are never contacted.
* **The primary agent is WORKING.** Idle, waiting on a person, blocked on a
  prompt, errored or still starting are left alone.
* **The agent made progress since the previous check**, read as the primary
  session's assistant replies plus tool calls. One long tool call and a stalled
  session both freeze the counts while the status can still say WORKING.

Then each condition adds what only it can know:

* ``session_start`` fires once in the session's first ``_START_WINDOW``, so a
  daemon upgrade does not brief every session already deep in its task.
* ``compaction`` fires when the session's compaction count has risen since the
  last accounted check. It waits for the shared gate rather than being dropped,
  so a ``/compact`` typed into an idle session reminds on the next working turn.
* ``phase_stale`` fires once per phase REPORT, when a report the agent made has
  stood for ``after_minutes`` while it kept working.
* ``unnamed`` fires while the title still carries the generated prefix or the
  description is empty, at least ``_UNNAMED_SPACING`` apart.

At most ONE rule fires per check, in configuration order, so a brief and a
reminder never stack into one wall of text. ``max_sends`` caps each rule per
session; the count is persisted BEFORE delivery, so a crash or a lost receipt
can only ever under-send.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from grove.core.agents.model import AgentActivityState
from grove.core.config import TriggerRule
from grove.core.contracts.watches import TriggerPredicate, WatchOutcome
from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.watches.watcher import StillWatching, Watcher
from grove.core.workspace import LIVE_STATUSES, WorkspaceState, WorkspaceStatus, WorkspaceTitle

if TYPE_CHECKING:
    from grove.core.activity import WorkspaceActivity

#: How long after a session's first record a ``session_start`` rule may still fire.
_START_WINDOW = timedelta(minutes=15)
#: The least time between two ``unnamed`` reminders to one session.
_UNNAMED_SPACING = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class TriggerEvidence:
    """What the daemon observed about one workspace, freshly, at one check."""

    state: WorkspaceState
    status: WorkspaceStatus
    activity: AgentActivityState | None
    session_id: str | None
    session_started_at: datetime | None
    #: The primary session's assistant replies plus tool calls, or ``None`` with no session.
    progress: int | None
    #: Compactions recorded in the primary session, or ``None`` when it cannot be told.
    compactions: int | None
    phase: PhaseReport | None
    #: The rules this workspace's own repo cascade resolves, enabled and automatic.
    rules: tuple[TriggerRule, ...] = ()

    @classmethod
    def of(cls, row: WorkspaceActivity, rules: tuple[TriggerRule, ...]) -> TriggerEvidence:
        """Distil a freshly computed activity row into the facts the rules read.

        A workspace created with ``brief`` off gets no ``session_start`` rule:
        that per-workspace choice predates triggers and still means the same.
        """
        primary_row = row.sessions[0] if row.sessions else None
        primary = primary_row.activity if primary_row else None
        return cls(
            state=row.state,
            status=row.state.status,
            activity=primary.state if primary else None,
            session_id=primary_row.session.session_id if primary_row else None,
            session_started_at=primary.started_at if primary else None,
            progress=primary.assistant_replies + primary.tool_calls if primary else None,
            compactions=primary_row.compactions if primary_row else None,
            phase=row.phase,
            rules=tuple(rule for rule in rules if row.state.brief or rule.when != "session_start"),
        )

    @property
    def working(self) -> bool:
        """The gate every automatic rule shares: live, and the agent is WORKING."""
        return self.status in LIVE_STATUSES and self.activity is AgentActivityState.WORKING

    def missing_name(self) -> str | None:
        """What a person would need to tell this workspace apart, or ``None`` if nothing is."""
        generated = WorkspaceTitle.is_generated(self.state.title)
        undescribed = not (self.state.description or "").strip()
        if generated and undescribed:
            return "a generated title and no description"
        if generated:
            return "a generated title"
        return "no description" if undescribed else None


#: Reads fresh evidence for a workspace id, or ``None`` when it cannot be told.
EvidenceLookup = Callable[[str], TriggerEvidence | None]
#: Renders the text one rule sends to one workspace.
TextRenderer = Callable[[TriggerRule, TriggerEvidence, Mapping[str, str]], str]


@dataclass(frozen=True, slots=True)
class Firing:
    """One rule chosen to go out, and the predicate that records having chosen it."""

    rule: TriggerRule
    predicate: TriggerPredicate


class TriggerWatcher(Watcher[TriggerPredicate]):
    """The standing check behind every configured trigger. Never settles on its own."""

    kind = "trigger"

    def __init__(self, evidence: EvidenceLookup, *, render: TextRenderer) -> None:
        self._evidence = evidence
        self._render = render

    def observe(self, predicate: TriggerPredicate, now: datetime) -> StillWatching | None:
        """Read the evidence and apply :meth:`decide`. ``None`` when nothing can be told."""
        seen = self._evidence(predicate.workspace_id)
        if seen is None:
            return None
        firing = self.decide(predicate, seen, seen.rules, now=now)
        baseline = self.baseline(firing.predicate if firing else predicate, seen)
        if firing is not None:
            text = self._render(firing.rule, seen, self.values(seen, now))
            return StillWatching(baseline, WatchOutcome(ok=True, summary=text))
        return StillWatching(baseline) if baseline != predicate else None

    def fire(
        self, predicate: TriggerPredicate, rule: TriggerRule, now: datetime
    ) -> tuple[TriggerPredicate, str] | str:
        """A manual send of *rule*: the counted predicate and its text, or why not.

        Manual sends keep the live-session gate but skip the rule's own
        condition, because a person chose to send it. They still count against
        ``max_sends``: a cap that a dispatch could exceed would protect nothing.
        """
        seen = self._evidence(predicate.workspace_id)
        if seen is None:
            return "workspace activity could not be read"
        if seen.status not in LIVE_STATUSES:
            return "workspace is not live"
        current = self._for_session(predicate, seen)
        if self._capped(current, rule):
            return f"rule {rule.name!r} reached its cap of {rule.max_sends} for this session"
        text = self._render(rule, seen, self.values(seen, now))
        return self._count(current, rule, now), text

    @classmethod
    def decide(
        cls,
        predicate: TriggerPredicate,
        seen: TriggerEvidence,
        rules: tuple[TriggerRule, ...],
        *,
        now: datetime,
    ) -> Firing | None:
        """Pure: the one rule to send now, with the predicate that counts it, or ``None``.

        The shared gate is evaluated once. Rules are tried in configuration
        order and the first due one wins, so a later rule waits for the next
        check rather than arriving in the same breath.
        """
        current = cls._for_session(predicate, seen)
        if not seen.working or current.progress is None or current.progress == seen.progress:
            return None
        for rule in rules:
            if cls._capped(current, rule) or not cls._due(rule, current, seen, now=now):
                continue
            counted = cls._count(current, rule, now)
            if rule.when == "phase_stale" and seen.phase is not None:
                counted = counted.model_copy(update={"phase_reminded_at": seen.phase.updated_at})
            if rule.when == "compaction":
                counted = counted.model_copy(update={"compactions": seen.compactions})
            return Firing(rule, counted)
        return None

    @staticmethod
    def baseline(predicate: TriggerPredicate, seen: TriggerEvidence) -> TriggerPredicate:
        """The predicate to persist after a check: the counts move to what was just seen.

        Compactions are only ever raised by a firing (or adopted on a session's
        first sight), never here, so a compaction that happened while the agent
        was idle is still owed when it next works.
        """
        current = TriggerWatcher._for_session(predicate, seen)
        return current.model_copy(update={"progress": seen.progress})

    @staticmethod
    def values(seen: TriggerEvidence, now: datetime) -> dict[str, str]:
        """The placeholders a rule's text may name, as this workspace fills them."""
        phase = seen.phase
        age = int((now - phase.updated_at).total_seconds() // 60) if phase else 0
        return {
            "title": seen.state.title,
            "phase": phase.phase if phase else "",
            "age": f"{age}m",
            "missing": seen.missing_name() or "",
        }

    @staticmethod
    def _for_session(predicate: TriggerPredicate, seen: TriggerEvidence) -> TriggerPredicate:
        """The predicate as it applies to the session the evidence names.

        A new primary session (a respawn, a rotated id) starts its counts over:
        a cap is a promise about one conversation's context window. Its
        compactions are adopted as already accounted for, so a resumed session
        is not reminded about compactions it went through before Grove saw it.
        """
        if predicate.session_id == seen.session_id:
            if predicate.compactions is None and seen.compactions is not None:
                return predicate.model_copy(update={"compactions": seen.compactions})
            return predicate
        return TriggerPredicate(
            workspace_id=predicate.workspace_id,
            session_id=seen.session_id,
            progress=None,
            compactions=seen.compactions,
        )

    @staticmethod
    def _capped(predicate: TriggerPredicate, rule: TriggerRule) -> bool:
        return rule.max_sends is not None and predicate.sent.get(rule.name, 0) >= rule.max_sends

    @staticmethod
    def _count(predicate: TriggerPredicate, rule: TriggerRule, now: datetime) -> TriggerPredicate:
        return predicate.model_copy(
            update={
                "sent": {**predicate.sent, rule.name: predicate.sent.get(rule.name, 0) + 1},
                "last_sent": {**predicate.last_sent, rule.name: now},
            }
        )

    @staticmethod
    def _due(
        rule: TriggerRule, predicate: TriggerPredicate, seen: TriggerEvidence, *, now: datetime
    ) -> bool:
        """Whether *rule*'s own condition holds. The shared gate has already passed."""
        if rule.when == "session_start":
            started = seen.session_started_at
            return started is not None and now - started <= _START_WINDOW
        if rule.when == "compaction":
            return (
                seen.compactions is not None
                and predicate.compactions is not None
                and seen.compactions > predicate.compactions
            )
        if rule.when == "phase_stale":
            phase = seen.phase
            return (
                phase is not None
                and (phase.phase != PHASE_ORDER[0] or phase.note is not None)
                and not phase.is_terminal
                and now - phase.updated_at >= timedelta(minutes=rule.after_minutes)
                and predicate.phase_reminded_at != phase.updated_at
            )
        if rule.when == "unnamed":
            last = predicate.last_sent.get(rule.name)
            return seen.missing_name() is not None and (
                last is None or now - last >= _UNNAMED_SPACING
            )
        return False


__all__ = ["EvidenceLookup", "Firing", "TextRenderer", "TriggerEvidence", "TriggerWatcher"]
