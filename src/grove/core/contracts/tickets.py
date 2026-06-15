"""Provider-neutral ticket wire shapes — the contract every tracker normalizes to.

A ``TicketRef`` is the one shape that crosses every boundary: persisted on
``WorkspaceState.ticket_refs`` (the engine stores it), returned inside
``WorkspaceStateView`` and the ``/tickets`` responses (clients read it), and
constructed by the daemon when a provider answers. It is deliberately
provider-*neutral*: a Linear issue, a GitHub issue, and a Gitea issue all
collapse to the same six fields. Each provider adapter normalizes *shape* into
this — never semantics (the provider-boundary rule).

``TicketSelector`` is the minimal "which ticket" envelope — ``{provider, id}`` —
reused by both workspace-create (the optional ``ticket`` input) and the manual
attach route, so there is one way to name a ticket on the wire.

Pydantic, here in ``contracts/``, because every field travels to a non-Python
client. ``WorkspaceState`` (the dataclass) holds a ``list[TicketRef]`` and
imports this class under ``TYPE_CHECKING`` only — the engine state stays on the
depended-upon side of the import arrow, exactly as ``contracts/activity.py``
references engine dataclasses without creating a cycle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TicketProviderName = Literal["linear", "github", "gitea"]
"""The three MVP trackers. A closed literal so it drives branching with the
type checker's help and rejects typos at the wire boundary."""

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class TicketRef(BaseModel):
    """A provider-neutral pointer to one external ticket, plus cached display fields.

    ``id`` is the *canonical key* the tracker links on — Linear's ``ENG-123``,
    GitHub/Gitea's bare issue number ``42``. It is the only required payload
    field; the rest are best-effort enrichment a client renders when present and
    omits when not (``None``). ``ambiguous`` flags a branch that more than one
    provider (or key) claimed — the association is uncertain and the UI should
    say so, letting the user attach/detach to disambiguate.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    id: str = Field(min_length=1)
    title: str | None = None
    url: str | None = None
    status: str | None = None
    assignee: str | None = None
    ambiguous: bool = False
    """True when this ref was inferred from a branch that matched more than one
    provider/key. A deterministic single match leaves it False."""


class TicketSelector(BaseModel):
    """Minimal "which ticket" envelope — the create input and attach body.

    One shape names a ticket everywhere: ``CreateWorkspaceRequest.ticket`` and
    the ``POST /workspaces/{id}/tickets`` body both carry exactly this, so a
    client never has to learn two ways to point at a ticket.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    id: str = Field(min_length=1)


class TicketProviderView(BaseModel):
    """One row of ``GET /tickets/providers`` — what a client may offer the user.

    ``configured`` is the credentials-present signal: a provider can be
    ``enabled`` in config yet have no token in the environment, in which case
    list/get calls will fail — clients gray it out rather than offer a dead
    picker. ``context`` is the human-readable scope (``owner/repo`` or a Linear
    team key) so the UI can disambiguate two repos behind the same tracker.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    label: str
    configured: bool
    context: str | None = None


__all__ = [
    "TicketProviderName",
    "TicketProviderView",
    "TicketRef",
    "TicketSelector",
]
