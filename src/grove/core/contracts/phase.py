"""Wire shape for the task-phase axis (:mod:`grove.core.phase`).

The engine's ``PhaseReport`` is a plain dataclass read off disk; this is the
Pydantic mirror clients receive. It reaches the wire only through
``WorkspaceActivityView.phase`` — the ``TodoListView``/``FileEditView``
precedent — so it is not re-exported from ``contracts/__init__``.

``TaskPhase`` is imported from the engine rather than duplicated as a local
``Literal``. That is the opposite call to ``sessions.TodoStatus``, and
deliberately: the duplication there exists so ``contracts`` never drags
``grove.core.agents`` (adapters, transcript caches, the whole parsing spine) in
at runtime, whereas ``grove.core.phase`` imports nothing from Grove at all. With
no weight to avoid, a second copy of the vocabulary would buy nothing and add a
way for the two to disagree.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from grove.core.phase import NOTE_CAP, PHASE_ORDER, TaskPhase

if TYPE_CHECKING:
    from grove.core.phase import PhaseReport


class TicketPhaseView(BaseModel):
    """Wire mirror of ``grove.core.phase.TicketClaim`` — one ticket's phase claim.

    A separate class from :class:`PhaseView` rather than the same shape reused,
    because ``ticket`` (the ``f"{provider}:{id}"`` join key) means nothing on
    the workspace's own claim — there is no ticket to name — and a nullable
    field that is populated on one axis of one list and never on the other is
    exactly the "degraded answer indistinguishable from a confident one" trap
    this package's own docstring already warns against twice.
    """

    model_config = ConfigDict(frozen=True)

    ticket: str
    phase: TaskPhase
    note: str | None = None
    blocked: bool = False
    index: int


class PhaseView(BaseModel):
    """Wire mirror of ``grove.core.phase.PhaseReport`` — one agent's phase claim
    plus every per-ticket claim beside it.

    A workspace whose agent has reported nothing carries ``phase=None`` on its
    parent view; there is no "unreported" member here, because absence of a
    claim is not a claim. Clients must render those two differently — an agent
    that has never reported is a fleet-health signal, not a task at step zero.
    """

    model_config = ConfigDict(frozen=True)

    phase: TaskPhase
    note: str | None = None
    blocked: bool = False
    """Mirrors ``PhaseClaim.blocked`` — see that field for why this is a flag
    beside the phase rather than a seventh member of the ramp."""

    updated_at: datetime
    index: int
    """Zero-based position in the phase order. Sent rather than re-derived
    client-side so the TUI and the webapp cannot disagree about the ordering,
    the same reason the status palette is served from one place."""

    total: int = len(PHASE_ORDER)
    """How many phases exist, so a client can render "4 of 6" without pinning
    its own copy of the vocabulary — a client built against a Grove with five
    phases keeps rendering correctly against one with six."""

    tickets: list[TicketPhaseView] = []
    """Per-ticket claims, ordered by ticket key — mirrors
    ``PhaseReport.tickets`` verbatim rather than re-sorting, since the engine
    side already fixed the order for the same reason (a stable fingerprint
    for the activity tick). Empty, never omitted, for a workspace with no
    per-ticket claims — a client renders an empty list as "nothing to show"
    with no null check of its own."""

    @classmethod
    def from_report(cls, r: PhaseReport) -> PhaseView:
        return cls(
            phase=r.phase,
            note=r.note,
            blocked=r.blocked,
            updated_at=r.updated_at,
            index=r.index,
            tickets=[
                TicketPhaseView(
                    ticket=t.ticket,
                    phase=t.phase,
                    note=t.note,
                    blocked=t.blocked,
                    index=t.index,
                )
                for t in r.tickets
            ],
        )


class SetPhaseRequest(BaseModel):
    """Body for ``POST /workspaces/{id}/phase`` — set or correct a phase claim
    from outside the agent, the workspace's own or one attached ticket's.

    The in-workspace agent never sends this (it writes the per-agent file Grove
    names in its launch env, per ``grove.core.phase``'s file-channel design);
    this is for a
    human or an orchestrator to set/correct the claim over HTTP/MCP — the
    phase analogue of ``RemapSessionRequest`` (``sessions.py``), which
    likewise lives beside the views for its own concern rather than in
    ``requests.py``. ``extra="forbid"``, unlike the tolerant-inward
    ``PhaseDocument``: a caller here is Grove's own client code, so an unknown
    field is a bug worth surfacing loudly, not a stray key to shrug off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: TaskPhase
    note: str | None = Field(default=None, max_length=NOTE_CAP)
    blocked: bool = False
    ticket: str | None = None
    """The ``f"{provider}:{id}"`` key of the ticket this claim is about, or
    ``None`` for the workspace's own claim — mirrors ``PhaseFile.write``'s own
    ``ticket`` parameter, which this request forwards to verbatim rather than
    reinterpreting."""
