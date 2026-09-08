"""Authenticated assignment-delivery shapes at the tracker-to-daemon boundary.

The daemon authenticates the forwarding hop with ``Authorization: Bearer
<forwarding-token>`` before it validates :class:`AssignmentTicketEvent`.  The
forge's original signature is verified by the forwarder; Grove receives a
small, normalized wake-up rather than either forge's sprawling webhook body.

An assignment event never carries enough authority to start work by itself.
The assignment owner resolves its target without scanning the fleet, then
re-reads that one ticket from the configured provider.  ``generation`` fences
older queued deliveries for a target and ``delivery_id`` makes retry delivery
idempotent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(extra="forbid", frozen=True)

AssignmentProvider = Literal["gitea", "github"]
"""Trackers whose normalized assignment delivery Grove currently accepts."""

AssignmentChange = Literal["assigned", "unassigned"]
"""The forge edge that caused a delivery. It is a wake-up, not trusted state."""

LifecycleAssignmentKind = Literal[
    "created",
    "updated",
    "paused",
    "resumed",
    "respawned",
    "killed",
]
"""Workspace edges that can change the assignment owner's indexed holders."""

FORWARDING_AUTHORIZATION_HEADER = "Authorization"
FORWARDING_AUTHORIZATION_SCHEME = "Bearer"
"""The required daemon-forwarder authentication shape; the token stays off-wire."""


class AssignmentTicketIdentity(BaseModel):
    """One forge ticket, identified without a host-private repository path."""

    model_config = _FROZEN

    provider: AssignmentProvider
    owner: str = Field(min_length=1, max_length=255)
    repo: str = Field(min_length=1, max_length=255)
    ticket_id: str = Field(pattern=r"^[1-9][0-9]*$", max_length=32)

    @property
    def key(self) -> str:
        """Stable target key for admission and generation fencing."""
        return f"{self.provider}:{self.owner}/{self.repo}#{self.ticket_id}"


class AssignmentTicketEvent(BaseModel):
    """A normalized, authenticated Gitea or GitHub assignee delivery.

    The daemon accepts this exact body only after authenticating its forwarding
    peer. ``delivery_id`` is unique within one provider and must be retained for
    replay protection; ``generation`` is the forwarder's monotonically
    increasing target revision, so a late older delivery cannot replace a newer
    pending one.
    """

    model_config = _FROZEN

    target: AssignmentTicketIdentity
    change: AssignmentChange
    delivery_id: str = Field(pattern=r"^\S(?:.*\S)?$", max_length=512)
    generation: int = Field(ge=0)


class AssignmentLifecycleEvent(BaseModel):
    """A Grove lifecycle wake-up for one workspace's assignment index entry.

    These events are constructed inside Grove rather than accepted from a
    network caller. They use the same delivery and generation fence as tracker
    events because a late ``updated`` after ``killed`` must not resurrect an
    assignment holder from stale state.
    """

    model_config = _FROZEN

    workspace_id: str = Field(min_length=1, max_length=128)
    lifecycle: LifecycleAssignmentKind
    delivery_id: str = Field(pattern=r"^\S(?:.*\S)?$", max_length=512)
    generation: int = Field(ge=0)


__all__ = [
    "FORWARDING_AUTHORIZATION_HEADER",
    "FORWARDING_AUTHORIZATION_SCHEME",
    "AssignmentChange",
    "AssignmentLifecycleEvent",
    "AssignmentProvider",
    "AssignmentTicketEvent",
    "AssignmentTicketIdentity",
    "LifecycleAssignmentKind",
]
