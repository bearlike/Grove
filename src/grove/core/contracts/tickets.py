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

``TicketComment`` is the provider-neutral comment-thread shape the
issue-ops write-back path reads and returns — the I/O-face counterpart to
``TicketRef``'s read-only enrichment.

Pydantic, here in ``contracts/``, because every field travels to a non-Python
client. ``WorkspaceState`` (the dataclass) holds a ``list[TicketRef]`` and
imports this class under ``TYPE_CHECKING`` only — the engine state stays on the
depended-upon side of the import arrow, exactly as ``contracts/activity.py``
references engine dataclasses without creating a cycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TicketProviderName = Literal["linear", "github", "gitea"]
"""The three MVP trackers. A closed literal so it drives branching with the
type checker's help and rejects typos at the wire boundary."""

TicketReactionKind = Literal["+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"]
"""The reaction vocabulary Gitea and GitHub both accept, verbatim. A
closed literal so a typo'd reaction name is a type error, not a silent 4xx."""

TicketKind = Literal["issue", "pull_request"]
"""What the tracker item IS. A pull request is another ref in the SAME
``ticket_refs`` list — it reuses the provider registry, the comment I/O (both
forges thread PR comments through their issues endpoint) and the sticky
publisher — so this is a discriminator on the existing shape, never a parallel
list. ``"issue"`` is the default everywhere so every persisted record and every
older wire client keeps decoding unchanged."""

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
    kind: TicketKind = "issue"
    """Issue or pull request. Defaults to ``"issue"`` so a record persisted
    before this field existed — and a client that never sends it — decodes
    unchanged. ``status`` is the PR's real state, so a merged PR reads
    ``"merged"``, never ``"closed"``: an orchestrator acts on the difference."""
    title: str | None = None
    url: str | None = None
    status: str | None = None
    draft: bool = False
    """Whether this pull request remains a draft. Defaults false so existing
    stored refs retain their pre-draft semantics until they are enriched."""
    assignee: str | None = None
    ambiguous: bool = False
    """True when this ref was inferred from a branch that matched more than one
    provider/key. A deterministic single match leaves it False."""

    @property
    def key(self) -> str:
        """This ref's stable identity as one string — ``"gitea:498"``.

        The SINGLE composer of that string, because several surfaces now need
        to agree on it: the per-ticket task-phase claims an agent writes into
        its phase file key on it (``grove.core.phase.TicketClaim``), the sticky
        publisher picks its per-target phase by it, and the webapp joins the two
        halves it already holds by it.

        ``(provider, id)`` and not ``kind``, matching what ``attach_ticket``
        deduplicates on — re-attaching an issue as a pull request corrects the
        kind of the SAME row, so folding kind in here would silently orphan
        every claim already made about it.
        """
        return f"{self.provider}:{self.id}"


class TicketSelector(BaseModel):
    """Minimal "which ticket" envelope — the create input and attach body.

    One shape names a ticket everywhere: ``CreateWorkspaceRequest.ticket`` and
    the ``POST /workspaces/{id}/tickets`` body both carry exactly this, so a
    client never has to learn two ways to point at a ticket. ``kind`` rides
    along (defaulted, so nothing existing changes) rather than a second
    "attach a pull request" body: a parsed link resolves to exactly this.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    id: str = Field(min_length=1)
    kind: TicketKind = "issue"


class TicketComment(BaseModel):
    """One comment on a ticket thread, provider-neutral.

    ``id`` is the provider's own comment id — opaque to Grove, but the exact
    handle ``edit_comment``/``react`` round-trip. ``post_comment`` returns this
    so the issue-ops publisher can persist ``id`` as the sticky-comment marker
    and update the same comment in place on every subsequent run instead of
    posting a fresh one each time.
    """

    model_config = _FROZEN

    id: str = Field(min_length=1)
    body: str
    author: str | None = None
    created_at: datetime | None = None


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
    "TicketComment",
    "TicketKind",
    "TicketProviderName",
    "TicketProviderView",
    "TicketReactionKind",
    "TicketRef",
    "TicketSelector",
]
