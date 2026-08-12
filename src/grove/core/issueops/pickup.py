"""The assignee work queue: one path from "this ticket is Grove's" to a workspace.

`PickupEngine` is the single engine path shared by the daemon's background poll
(:mod:`grove.core.issueops.poller`) and the human's own `grove tickets` verbs.
That sharing is the design constraint, not a convenience: a command that hands a
ticket over by a different route than the poll would claim its marker
differently, render a different prompt, or skip the assignment, and the
divergence would only ever show up in production.

Three verbs, and the asymmetry between them is deliberate:

* **hand over** — claim the durable marker, render the prompt from the whole
  thread, create the workspace with the ticket attached AT CREATION (nothing
  publishes for a ref that is not attached, so a late attach leaves no history
  on that thread), and assign the bot on the way.
* **owned** — what Grove has been handed, read straight off the durable log.
* **hand back** — unassign the bot, and KEEP the marker. Removing it would let
  the very next tick pick the ticket straight back up, which is the loop the
  marker exists to prevent; a human who genuinely wants Grove on it again says
  so with `hand over`, which is explicit and therefore allowed to override.

The eligibility decision (:meth:`PickupEngine.plan`) is pure, so the ceiling and
the deferral rule are testable with no forge, no store and no clock — the
I/O-dispatch split every subscriber in this package makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketProviderName, TicketSelector
from grove.core.errors import GroveError, TicketProviderNotConfigured
from grove.core.issueops.handover import HandoverKey, HandoverLog, HandoverSource
from grove.core.issueops.prompt import IssuePrompt

if TYPE_CHECKING:
    from grove.core.contracts.tickets import TicketRef
    from grove.core.manager import WorkspaceManager
    from grove.core.tickets.provider import TicketProvider
    from grove.core.workspace import WorkspaceState

# What the pickup path puts in ``{command_text}``. The template asks "how were
# you engaged"; on this path the honest answer is that nobody typed anything,
# and saying so beats leaving the section blank — an agent handed an empty
# instruction reasonably wonders what it missed.
ASSIGNED_COMMAND_TEXT = (
    "Nobody typed an instruction. You were assigned this ticket on the tracker, "
    "which is how work reaches you here — the ticket itself is the whole brief."
)


@dataclass(frozen=True, slots=True)
class PickupCandidate:
    """One ticket that could become a workspace, with the repo it belongs to."""

    repo_root: Path
    key: HandoverKey
    ref: TicketRef


@dataclass(frozen=True, slots=True)
class PickupPlan:
    """What one tick decided: what to start, what to defer, and why.

    ``defer`` is a first-class part of the answer rather than a dropped
    remainder — the (N+1)th ticket has to be *named* somewhere or a user who
    assigned forty issues has no way to tell "Grove is pacing itself" from
    "Grove lost my ticket".
    """

    take: tuple[PickupCandidate, ...] = ()
    defer: tuple[PickupCandidate, ...] = ()
    active: int = 0


class PickupEngine:
    """Hand a ticket to the fleet, list what Grove owns, hand one back."""

    def __init__(self, *, log: HandoverLog | None = None) -> None:
        self._log = log if log is not None else HandoverLog()

    @property
    def log(self) -> HandoverLog:
        return self._log

    # ─── pure decision ──────────────────────────────────────────────────────

    @staticmethod
    def plan(candidates: list[PickupCandidate], *, active: int, ceiling: int) -> PickupPlan:
        """Split eligible candidates into what fits under the ceiling and what waits.

        Pure: the caller has already answered every I/O question (is it open, is
        a workspace holding it, has it been handed over) and passes only the
        survivors plus how many pickup-held tickets are already being worked.
        The ceiling counts LIVE work rather than starts per tick, because the
        thing that must not run away is the fleet, not the tick.
        """
        capacity = max(0, ceiling - active)
        ordered = sorted(candidates, key=lambda c: c.key.wire)
        return PickupPlan(
            take=tuple(ordered[:capacity]),
            defer=tuple(ordered[capacity:]),
            active=active,
        )

    # ─── the three verbs ────────────────────────────────────────────────────

    def hand_over(
        self,
        mgr: WorkspaceManager,
        *,
        key: HandoverKey,
        source: HandoverSource,
        now: datetime,
    ) -> WorkspaceState:
        """Start a workspace for this ticket. The one path both callers take.

        Reads the thread FIRST, so a tracker that cannot answer costs nothing and
        the ticket is retried on the next tick; claims the marker SECOND, before
        any create, for the reason spelled out on
        :meth:`~grove.core.issueops.handover.HandoverLog.claim`; and assigns the
        bot best-effort, because an unassignable tracker must not cost the
        workspace.

        The ticket is attached AT CREATION through ``CreateWorkspaceRequest``
        rather than by a follow-up ``attach_ticket``: the status publisher only
        mirrors onto refs a workspace already names, so a late attach leaves the
        first minutes of work unrecorded on the thread it belongs to.
        """
        provider = mgr.ticket_providers.get(key.provider)
        thread = provider.read_thread(key.ticket_id)
        ic = mgr.config.issueops

        self._log.claim(key, source=source, now=now)
        self.assign_bot(provider, key.ticket_id)

        title = (thread.ref.title or "").strip()[:120] or f"issue-{key.ticket_id}"
        request = CreateWorkspaceRequest(
            agent_name=ic.agent,
            title=title,
            ticket=TicketSelector(provider=key.provider, id=key.ticket_id),
            initial_prompt=IssuePrompt.render(
                ic.prompt_template,
                number=key.ticket_id,
                title=thread.ref.title or "",
                body=thread.body,
                url=thread.ref.url or "",
                command_text=ASSIGNED_COMMAND_TEXT,
                comments=IssuePrompt.render_thread(thread.comments),
            ),
        )
        state = mgr.create(request)
        self._log.record_workspace(key, state.id)
        logger.info("issue-ops pickup started workspace {} for {}", state.id, key.wire)
        return state

    def hand_back(self, mgr: WorkspaceManager, *, key: HandoverKey, now: datetime) -> None:
        """Give the ticket back to the humans: unassign the bot, keep the marker.

        The marker stays on purpose. It records that the ticket was handed to
        Grove ONCE, which is exactly the fact the poll needs; dropping it would
        make the next tick re-take a ticket a human just took away, and a
        remaining assignee would keep that loop running indefinitely.

        The workspace, if any, is deliberately untouched — stopping work is
        ``kill``'s job, and a hand-back that silently killed a running agent
        would destroy uncommitted work to update a field on a tracker.
        """
        provider = mgr.ticket_providers.get(key.provider)
        self._log.claim(key, source="command", now=now)
        if not provider.can_assign:
            logger.warning(
                "issue-ops cannot unassign {} — the {} provider has no assignee write "
                "or no credential; the marker was recorded, so pickup will leave it alone",
                key.wire,
                key.provider,
            )
            return
        provider.unassign_self(key.ticket_id)

    def owned(self) -> list[tuple[HandoverKey, str | None, datetime]]:
        """Every ticket Grove has been handed: key, workspace id (if any), when."""
        return [(e.key, e.workspace_id, e.claimed_at) for e in self._log.entries()]

    # ─── the outbound half ──────────────────────────────────────────────────

    def assign_bot(self, provider: TicketProvider, ticket_id: str) -> bool:
        """Put the provider's own account on the ticket. Best-effort, and LOUD.

        Assigning needs repo write, which commenting does not, so a deployment
        where Grove can comment and cannot assign is an ordinary configuration
        rather than a bug — but it must never be a silent no-op. Both refusals
        name the ticket and the reason: no capability/credential at all (debug,
        because it is a standing state), and an actual rejection by the forge
        (warning, because it is almost always a missing permission a human can
        grant).

        ``True`` means THIS call assigned the ticket, which is narrower than "the
        ticket is now assigned" — an account a human already put there answers
        ``False``. The caller records the difference to decide what it may later
        release, so widening this to "is assigned" would hand Grove permission to
        undo assignments it never made.
        """
        if not provider.can_assign:
            logger.debug(
                "issue-ops not assigning {}#{}: the {} provider backs no assignee write "
                "or has no credential",
                provider.name,
                ticket_id,
                provider.name,
            )
            return False
        try:
            return provider.assign_self(ticket_id)
        except GroveError as exc:
            logger.warning(
                "issue-ops could not assign {}#{} to the configured account — this needs "
                "repo WRITE access, which commenting does not: {}",
                provider.name,
                ticket_id,
                exc,
            )
            return False

    def release_bot(self, provider: TicketProvider, ticket_id: str) -> bool:
        """Take the bot's account back off the ticket. Best-effort, same discipline.

        The counterpart to :meth:`assign_bot`, for when the workspace that
        occasioned the assignment has ended. It is NOT :meth:`hand_back`, and the
        difference is the marker: a hand-back is a human saying "Grove should not
        have this", so it claims the durable marker to stop the poll re-taking the
        ticket. This is Grove observing that its own work finished, which says
        nothing about whether the ticket may be handed over again later.

        Every human assignee is left alone — the provider's unassign removes only
        :meth:`viewer_login`.
        """
        if not provider.can_assign:
            return False
        try:
            provider.unassign_self(ticket_id)
        except GroveError as exc:
            # Worth a warning rather than a debug: the visible symptom is a board
            # that keeps claiming Grove is working a ticket it has finished.
            logger.warning(
                "issue-ops could not unassign {}#{} after the workspace ended — the "
                "ticket will keep reading as assigned to Grove: {}",
                provider.name,
                ticket_id,
                exc,
            )
            return False
        logger.info(
            "issue-ops released {}#{} — the workspace holding it has ended",
            provider.name,
            ticket_id,
        )
        return True

    # ─── shared resolution ──────────────────────────────────────────────────

    @staticmethod
    def key_for(mgr: WorkspaceManager, provider: TicketProviderName, ticket_id: str) -> HandoverKey:
        """The durable key for a ticket on this repo's configured provider.

        Generic over the provider the way the issue-ops engine's repo resolution
        is: the provider NAME is exactly a field name on ``TicketsConfig``, so
        one ``getattr`` reaches the right submodel. A tracker with no owner/repo
        (Linear) cannot be keyed, and that is a typed refusal rather than a
        fabricated key — two repos' issue #7 would otherwise share one marker.
        """
        cfg = getattr(mgr.config.tickets, provider, None)
        owner = getattr(cfg, "owner", "") or ""
        repo = getattr(cfg, "repo", "") or ""
        if not (owner and repo):
            raise TicketProviderNotConfigured(
                f"ticket provider {provider!r} has no owner/repo configured for this repo, "
                "so a ticket on it cannot be handed to Grove"
            )
        return HandoverKey(provider=provider, owner=owner, repo=repo, ticket_id=ticket_id)


__all__ = [
    "ASSIGNED_COMMAND_TEXT",
    "PickupCandidate",
    "PickupEngine",
    "PickupPlan",
]
