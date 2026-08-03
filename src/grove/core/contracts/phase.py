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


class PhaseView(BaseModel):
    """Wire mirror of ``grove.core.phase.PhaseReport`` — one agent's phase claim.

    A workspace whose agent has reported nothing carries ``phase=None`` on its
    parent view; there is no "unreported" member here, because absence of a
    claim is not a claim. Clients must render those two differently — an agent
    that has never reported is a fleet-health signal, not a task at step zero.
    """

    model_config = ConfigDict(frozen=True)

    phase: TaskPhase
    note: str | None = None
    updated_at: datetime
    index: int
    """Zero-based position in the phase order. Sent rather than re-derived
    client-side so the TUI and the webapp cannot disagree about the ordering,
    the same reason the status palette is served from one place."""

    total: int = len(PHASE_ORDER)
    """How many phases exist, so a client can render "4 of 6" without pinning
    its own copy of the vocabulary — a client built against a Grove with five
    phases keeps rendering correctly against one with six."""

    @classmethod
    def from_report(cls, r: PhaseReport) -> PhaseView:
        return cls(
            phase=r.phase,
            note=r.note,
            updated_at=r.updated_at,
            index=r.index,
        )


class SetPhaseRequest(BaseModel):
    """Body for ``POST /workspaces/{id}/phase`` — set or correct a workspace's
    task-phase claim from outside the agent.

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
