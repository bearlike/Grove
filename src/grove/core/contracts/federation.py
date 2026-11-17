"""What two federated Grove daemons say to each other on ``/federation/*``.

A hub holds metadata only — which nodes exist and which node owns which
workspace — and rebuilds it from re-registration after a restart, so every
shape here is something a node can re-send in full.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.project_identity import ProjectIdentity

NodeId = Annotated[str, Field(pattern=r"^nd_[a-f0-9]{32}$")]
WorkspaceId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]

# `unreachable` is a STATE, never a verdict: a node out of line of sight keeps
# its workspaces and resumes on its next heartbeat.
NodeState = Literal["reachable", "unreachable"]

_STRICT = ConfigDict(extra="forbid", frozen=True)


class JoinRequest(BaseModel):
    """A node presenting a one-time join token to the hub."""

    model_config = _STRICT

    token: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    advertise_url: str = Field(min_length=1, max_length=2048)


class JoinResponse(BaseModel):
    """The node's identity and its shared secret, sent exactly once."""

    model_config = _STRICT

    node_id: NodeId
    secret: str


class RegisterRequest(BaseModel):
    """A node's full current state; the hub replaces whatever it held for the node."""

    model_config = _STRICT

    name: str = Field(min_length=1, max_length=128)
    advertise_url: str = Field(min_length=1, max_length=2048)
    workspaces: list[WorkspaceId] = Field(default_factory=list)
    projects: list[ProjectIdentity] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    capacity: int | None = None
    draining: bool = False
    runtimes: list[str] = Field(default_factory=list)
    """The resident ``runtimes.<name>`` blocks this node can create a remote
    workspace on; a create naming one is placed only where it is."""


class NodeView(BaseModel):
    """One node as the hub sees it."""

    model_config = _STRICT

    node_id: NodeId
    name: str
    advertise_url: str
    state: NodeState
    last_seen_at: datetime | None
    workspaces: int
    draining: bool
    labels: dict[str, str] = Field(default_factory=dict)
    capacity: int | None = None
    runtimes: list[str] = Field(default_factory=list)


class NodeIdentityView(BaseModel):
    """Who answered a ``/federation/ping``: the identity check, from either side."""

    model_config = _STRICT

    role: Literal["hub", "node"]
    node_id: NodeId | None
    name: str


class NodeSelfView(BaseModel):
    """This daemon's federation role, and for a node the state of its hub link."""

    model_config = _STRICT

    role: Literal["hub", "node"]
    node_id: NodeId | None
    name: str
    hub_url: str | None = None
    link: Literal["joining", "connected", "unreachable", "refused"] | None = None
    last_contact_at: datetime | None = None
    last_error: str | None = None
    draining: bool = False


class NodeDrainRequest(BaseModel):
    """Stop (or resume) accepting new workspaces on this node."""

    model_config = _STRICT

    draining: bool = True


PingHop = Literal["resolve", "connect", "auth"]


class PingHopView(BaseModel):
    """One hop of a node→hub ping, so DNS, TLS, a refused key and slowness differ."""

    model_config = _STRICT

    hop: PingHop
    ok: bool
    latency_ms: float | None = None
    detail: str = ""


class PingView(BaseModel):
    """Every hop that ran, in order; the first failing one is the last entry."""

    model_config = _STRICT

    hub_url: str
    hops: list[PingHopView]


class NodeJoinRequest(BaseModel):
    """Ask THIS daemon (a node) to join its configured hub with a one-time token."""

    model_config = _STRICT

    token: str = Field(min_length=1, max_length=256)


__all__ = [
    "JoinRequest",
    "JoinResponse",
    "NodeDrainRequest",
    "NodeId",
    "NodeIdentityView",
    "NodeJoinRequest",
    "NodeSelfView",
    "NodeState",
    "NodeView",
    "PingHop",
    "PingHopView",
    "PingView",
    "RegisterRequest",
]
