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
"""Max models DISCOVERY may offer for one agent, so a picker stays scannable.

Display only — a caller can still submit any id (the provider boundary).

**It does not apply to a configured list, and that asymmetry is the point.** The
cap exists because live discovery answers with whatever a tool happens to
publish: `codex debug models` returns everything, and a gateway measured on a
real host returned 22 models under one prefix. Nobody chose those, so trimming
them is a kindness. A list in `AgentSpec.models` is the opposite — somebody
typed it, in order, to say *these are the ones I use*. Capping that silently
discards the back half of an explicit answer with nothing said, which is the
failure this repo keeps re-learning: a truncated list is indistinguishable from
a complete one, so it reads as Grove ignoring its own config."""


CONTEXT_VARIANT_SUFFIX = "[1m]"
"""The long-context marker a gateway appends to a SECOND id for the same model.

Both ids exist because Claude Code needs them distinct — the marked one is how a
caller asks for the extended window explicitly — so neither can be renamed or
dropped from what Grove will launch. To a person choosing a model they are one
choice twice: measured on the reference gateway, all 7 pairs report identical
input/output rates and an identical ``max_input_tokens``.
"""


def _collapse_context_variants(ids: Sequence[str]) -> tuple[str, ...]:
    """Drop ``x`` where ``x[1m]`` is also offered, keeping the MARKED id.

    Structural, like the clients' namespace fold: the catalog itself says
    whether it has redundant pairs, so there is no vendor table and a catalog
    with no pairs is returned untouched. The marked id is the survivor because
    it is the one that names the capability — dropping it would silently take
    the extended window away from anyone picking from a list.

    Relative order is otherwise untouched — nothing is re-sorted, so a curated
    list still reads in the order somebody typed it. This narrows what is
    OFFERED and never what is accepted: ``create`` forwards any id verbatim, so
    the plain variant stays launchable by hand, by config, and by every
    workspace already pinned to one.
    """
    present = set(ids)
    return tuple(i for i in ids if i + CONTEXT_VARIANT_SUFFIX not in present)


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
    curated = bool(configured)
    raw = tuple(configured) if curated else get_adapter(kind).available_models(command)
    seen: dict[str, None] = {}
    for model in raw:
        if model and model not in seen:
            seen[model] = None
    # Both halves of a `[1m]` pair are one choice to a reader, so the redundant
    # one is folded out BEFORE the cap — otherwise a catalog of pairs spends
    # half its allowance showing each model twice.
    models = _collapse_context_variants(tuple(seen))
    # A curated list is returned WHOLE; only discovery is capped. See
    # MODEL_CATALOG_CAP for why silently trimming an explicit answer is worse
    # than a long picker.
    return models if curated else models[:MODEL_CATALOG_CAP]
