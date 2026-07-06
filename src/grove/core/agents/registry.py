"""Select an :class:`AgentAdapter` by its ``kind`` discriminator.

The one place that maps ``AgentSpec.kind`` → a concrete adapter. Adapters are
stateless, so a single shared instance per kind is returned — no per-call
construction. Adding a tomorrow's tool (opencode, codex) is a one-line entry
here plus its adapter module; nothing else in the engine changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from grove.core.agents.base import AgentAdapter
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.codex import CodexAdapter
from grove.core.agents.generic import GenericAdapter
from grove.core.agents.mewbo import MewboAdapter

# Built once; adapters carry no mutable state so sharing is safe and cheap.
_CLAUDE_CODE = ClaudeCodeAdapter()
_CODEX = CodexAdapter()
_GENERIC = GenericAdapter()
_MEWBO = MewboAdapter()

_ADAPTERS: dict[str, AgentAdapter] = {
    ClaudeCodeAdapter.kind: _CLAUDE_CODE,
    CodexAdapter.kind: _CODEX,
    GenericAdapter.kind: _GENERIC,
    MewboAdapter.kind: _MEWBO,
}


def get_adapter(kind: str) -> AgentAdapter:
    """Return the adapter for ``kind``, falling back to the no-op generic one.

    The fallback (rather than a raise) keeps the dashboard robust to a config
    that names an unrecognised kind — that agent simply shows no metrics instead
    of breaking the whole snapshot. ``AgentSpec.kind`` is a closed ``Literal``,
    so the fallback is defence in depth, not the expected path.
    """
    return _ADAPTERS.get(kind, _GENERIC)


def all_adapters() -> tuple[AgentAdapter, ...]:
    """Every registered adapter, for callers that scan a directory with each
    introspection-capable tool (the session explorer). The generic adapter is
    included — its scans are no-ops, so filtering it would be policy the
    no-op already provides."""
    return tuple(_ADAPTERS.values())


MODEL_CATALOG_CAP = 10
"""Max models any create-form picker OFFERS for one agent (the ≤10 rule). Config
or live discovery may name more; the surface stays scannable. The cap is display
only — a caller can still submit any id (the provider boundary)."""


def resolve_models(*, kind: str, command: str, configured: Sequence[str]) -> tuple[str, ...]:
    """The single per-agent model catalog a picker offers: configured override,
    else the adapter's live discovery — de-duped, order-preserving, capped.

    ``configured`` (``AgentSpec.models``) wins WHOLESALE when non-empty — a
    pin/curate/reorder seam (mechanism, not policy). Empty falls through to
    ``get_adapter(kind).available_models(command)`` (Codex reads ``codex debug
    models``; Claude Code offers its stable aliases; remote/shell offer none).
    This is the ONE place the catalog is composed, so every surface (webapp,
    TUI, CLI, MCP) shows the same list. Never an allowlist: create still
    forwards any model id verbatim, so an id absent here is valid."""
    raw = tuple(configured) if configured else get_adapter(kind).available_models(command)
    seen: dict[str, None] = {}
    for model in raw:
        if model and model not in seen:
            seen[model] = None
    return tuple(seen)[:MODEL_CATALOG_CAP]
