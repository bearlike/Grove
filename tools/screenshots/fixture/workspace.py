"""One live-fleet workspace as the demo declares it — identity, tickets, phase.

Every field here is something a capture has to be able to point at: the card's
title and description, the Info tab's ticket list, the phase badge and the
per-ticket phase meter. Nothing here creates a worktree; `planter/fleet.py`
does that.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.tickets import TicketKind, TicketProviderName
from grove.core.phase import TaskPhase

from .transcript import Transcript

_FROZEN = ConfigDict(extra="forbid", frozen=True)

DemoAgent = Literal["claude", "codex", "aider", "shell"]
"""The demo agent-registry names, which are what a workspace is created with.

Narrow because it drives branching twice: which stub script runs, and which
transcript writer plants the session. It is the demo's own roster (see
`planter/config.py`), not Grove's agent KINDS.
"""


class DemoTicket(BaseModel):
    """One attached ticket, pre-enriched the way a provider fetch would leave it.

    Mirrors `grove.core.contracts.tickets.TicketRef` field for field rather than
    embedding it, because a fixture is allowed to be stricter than the wire: the
    demo always states a title, a URL and a status, and `ambiguous` is a fact
    about branch inference that no hand-authored fixture can honestly claim.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    id: str = Field(min_length=1)
    kind: TicketKind = "issue"
    title: str
    url: str
    status: str
    assignee: str | None = None

    @property
    def key(self) -> str:
        """``provider:id`` — the identity a per-ticket phase claim keys on.

        Composed the same way `TicketRef.key` composes it, and asserted against
        it in the fixture tests so a claim can never name a ticket that is not
        attached.
        """
        return f"{self.provider}:{self.id}"


class TicketClaim(BaseModel):
    """A phase claimed for ONE attached ticket, keyed by `DemoTicket.key`."""

    model_config = _FROZEN

    ticket: str = Field(min_length=1)
    phase: TaskPhase
    note: str


class DemoPhase(BaseModel):
    """The workspace's own phase claim, plus any per-ticket claims under it.

    The same file an agent inside the workspace writes for itself — see
    `grove.core.phase` and the `working-in-grove` skill.
    """

    model_config = _FROZEN

    phase: TaskPhase
    note: str
    tickets: tuple[TicketClaim, ...] = ()


class DemoWorkspace(BaseModel):
    """One workspace of the live fleet, in the order it is created.

    ``working`` leaves an uncommitted edit so the peek summary shows a dirty
    count; ``ahead`` adds one commit on the branch; ``offline`` kills the tmux
    session out from under the reconciler once seeding is done, so the list
    shows an offline row and the respawn key.
    """

    model_config = _FROZEN

    title: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    agent: DemoAgent
    branch: str = Field(min_length=1)
    description: str
    working: bool = False
    ahead: bool = False
    offline: bool = False
    tickets: tuple[DemoTicket, ...] = ()
    phase: DemoPhase
    transcript: Transcript | None = None

    @property
    def ticket_keys(self) -> frozenset[str]:
        return frozenset(ticket.key for ticket in self.tickets)
