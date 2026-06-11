"""Select an :class:`AgentAdapter` by its ``kind`` discriminator.

The one place that maps ``AgentSpec.kind`` → a concrete adapter. Adapters are
stateless, so a single shared instance per kind is returned — no per-call
construction. Adding a tomorrow's tool (opencode, codex) is a one-line entry
here plus its adapter module; nothing else in the engine changes.
"""

from __future__ import annotations

from grove.core.agents.base import AgentAdapter
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.generic import GenericAdapter

# Built once; adapters carry no mutable state so sharing is safe and cheap.
_CLAUDE_CODE = ClaudeCodeAdapter()
_GENERIC = GenericAdapter()

_ADAPTERS: dict[str, AgentAdapter] = {
    ClaudeCodeAdapter.kind: _CLAUDE_CODE,
    GenericAdapter.kind: _GENERIC,
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
