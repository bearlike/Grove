"""AgentSummaryView — the configured-agent listing for new-workspace pickers.

The TUI builds its create-modal agent dropdown straight from ``cfg.agents``
in-process; a remote client (the webapp) can't reach the config object, so this
view exposes the same merged list over the wire. Deliberately a *summary*: only
``name`` / ``kind`` / ``description`` / ``models`` cross the boundary —
``command`` and ``env`` stay daemon-side because they can carry host-private
paths and secrets, and a picker needs neither.
"""

from __future__ import annotations

from collections.abc import Sequence

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

    models: tuple[str, ...] = ()
    """Model ids the create-form picker OFFERS for this agent (≤10) — resolved
    daemon-side by ``agents.resolve_models`` (config override, else live
    discovery). A hint, not an allowlist: the client may still submit any id as
    ``CreateWorkspaceRequest.model``. Empty when the agent exposes no catalog (a
    bare shell, or discovery found none), in which case the picker offers only a
    free-text field."""

    @classmethod
    def from_spec(cls, spec: AgentSpec, *, models: Sequence[str] = ()) -> AgentSummaryView:
        """Serialize one ``AgentSpec``. ``models`` is the per-agent catalog the
        daemon already resolved (kept out of this pure view so it never runs the
        Codex subprocess itself); defaults to empty for callers that don't
        surface models."""
        return cls(
            name=spec.name,
            kind=spec.kind,
            description=spec.description,
            models=tuple(models),
        )


__all__ = ["AgentSummaryView"]
