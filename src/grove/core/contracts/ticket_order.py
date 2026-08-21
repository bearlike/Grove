"""Canonical attached-ticket ordering shared by Grove clients.

The webapp mirrors :func:`ticket_sort_key`; ``webapp/tests/unit/ticket-order-drift.test.ts``
is the drift test that keeps its implementation aligned with this contract.
"""

from __future__ import annotations

from typing import Literal

from grove.core.contracts.tickets import TicketKind
from grove.core.phase import PHASE_ORDER, TaskPhase

_STATE_RANK = {
    "open": 0,
    "draft": 1,
    "unknown": 2,
    "merged": 3,
    "closed": 3,
}
_NumericIdRank = tuple[Literal[0], int, str]
_StringIdRank = tuple[Literal[1], str, str]


def ticket_sort_key(
    kind: TicketKind,
    state: str,
    phase: TaskPhase | None,
    ticket_id: str,
) -> tuple[int, int, int, int, _NumericIdRank | _StringIdRank]:
    """Return the key that orders attached tickets consistently across clients.

    The webapp mirrors this function; ``webapp/tests/unit/ticket-order-drift.test.ts``
    is the drift test. Unknown state stays above settled work so an unenriched
    ref does not sink below closed tickets and visibly jump when enrichment
    arrives.
    """
    kind_rank = 0 if kind == "pull_request" else 1
    state_rank = _STATE_RANK.get(state, _STATE_RANK["unknown"])

    if phase is None:
        progress_rank = (2, 0)
    elif phase == "done":
        progress_rank = (1, 0)
    else:
        progress_rank = (0, -PHASE_ORDER.index(phase))

    try:
        id_rank: _NumericIdRank | _StringIdRank = (0, int(ticket_id), ticket_id)
    except ValueError:
        id_rank = (1, ticket_id, ticket_id)

    return kind_rank, state_rank, *progress_rank, id_rank


__all__ = ["ticket_sort_key"]
