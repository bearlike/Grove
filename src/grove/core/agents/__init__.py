"""Tool-agnostic agent-session introspection.

The DRY core of the Activity Dashboard (epic #11 §3): an :class:`AgentAdapter`
turns one agent tool's transcript into the normalized :class:`AgentActivity`
model, and ``get_adapter(kind)`` selects the implementation. Clients and the
``ActivityService`` consume only the model types here — never a tool's native
format — so a new tool slots in behind this surface without touching them.

Public surface only. Concrete adapters (``ClaudeCodeAdapter``, ``GenericAdapter``)
are reached through ``get_adapter``; their modules stay internal.
"""

from __future__ import annotations

from grove.core.agents.base import AgentAdapter
from grove.core.agents.model import (
    ATTENTION_STATES,
    AgentActivity,
    AgentActivityState,
    AgentSession,
    DigestEntry,
    OrderedDigest,
    SessionProvenance,
    SessionSummary,
    SessionTurn,
)
from grove.core.agents.registry import all_adapters, get_adapter

__all__ = [
    "ATTENTION_STATES",
    "AgentActivity",
    "AgentActivityState",
    "AgentAdapter",
    "AgentSession",
    "DigestEntry",
    "OrderedDigest",
    "SessionProvenance",
    "SessionSummary",
    "SessionTurn",
    "all_adapters",
    "get_adapter",
]
