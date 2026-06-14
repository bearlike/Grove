"""AgentSummaryView — the configured-agent listing for new-workspace pickers.

The TUI builds its create-modal agent dropdown straight from ``cfg.agents``
in-process; a remote client (the webapp) can't reach the config object, so this
view exposes the same merged list over the wire. Deliberately a *summary*: only
``name`` / ``kind`` / ``description`` cross the boundary — ``command`` and
``env`` stay daemon-side because they can carry host-private paths and secrets,
and a picker needs neither.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from grove.core.config import AgentKind, AgentSpec


class AgentSummaryView(BaseModel):
    """One selectable agent as a create-form client sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    """Identifier the client submits as ``CreateWorkspaceRequest.agent_name``."""

    kind: AgentKind
    """Which adapter introspects the agent — drives whether ``initial_prompt``
    is honored (claude_code/mewbo) or ignored (a bare ``generic`` shell)."""

    description: str = ""
    """Human label for the picker row. Empty when the agent declares none."""

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> AgentSummaryView:
        return cls(name=spec.name, kind=spec.kind, description=spec.description)


__all__ = ["AgentSummaryView"]
