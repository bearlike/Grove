"""How a model catalog READS: one composer, every picker.

``registry.resolve_models`` answers *which* models an agent offers and is the
single composer of that list. This is the display layer over it: the same ids,
each joined to the name an operator declared and the context window a configured
pricing source published. Two lookups over one already-resolved tuple.

It lives here rather than in ``agents/`` because it is not a fact about a
provider — an adapter knows what it can launch, not what a human calls it — and
not in ``usage/`` because a picker is not an audit. The dependency runs one way:
this module reads the usage package's snapshot, and nothing there knows a picker
exists.

**The join never invents a row.** A model absent from the catalog does not
appear because something priced it, and a model absent from the snapshot appears
with no window rather than a zero. Both halves are enrichment over the catalog,
which stays authoritative about membership.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from loguru import logger

from grove.core import paths
from grove.core.agents.registry import resolve_models
from grove.core.config import AgentSpec, GroveConfig
from grove.core.contracts.agents import ModelOptionView
from grove.core.usage.pricing_sources import PricingCatalog


def model_options(
    spec: AgentSpec,
    *,
    cfg: GroveConfig,
    context_windows: Mapping[str, int] | None = None,
) -> tuple[ModelOptionView, ...]:
    """The enriched catalog one agent offers, in ``resolve_models``' own order.

    ``context_windows`` is injected so a caller listing several agents reads the
    price snapshot once rather than per agent; omitted, this reads it itself.
    Order is the catalog's — a curated ``AgentSpec.models`` list is an operator's
    stated preference, and sorting it by name would discard that.
    """
    windows = context_windows if context_windows is not None else published_context_windows(cfg)
    return tuple(
        ModelOptionView(
            id=model_id,
            name=cfg.models.display_name(model_id),
            context_window=windows.get(model_id),
        )
        for model_id in resolve_models(kind=spec.kind, command=spec.command, configured=spec.models)
    )


def published_context_windows(cfg: GroveConfig) -> dict[str, int]:
    """Context windows from the cached pricing snapshot; never fetches, never raises.

    Best-effort at the edge: the snapshot is a cache of somebody else's endpoint,
    and a picker that failed to open because a JSON file was unreadable would
    trade the whole control for a second line on its rows. An empty map is the
    honest answer, and it renders as every row simply having no window.
    """
    if not cfg.usage.pricing.sources:
        return {}
    try:
        return PricingCatalog(
            cfg.usage.pricing, cache_path=paths.usage_pricing_path()
        ).context_windows()
    except OSError as exc:  # pragma: no cover - defensive, the catalog already tolerates most
        logger.debug("context windows unavailable: {}", type(exc).__name__)
        return {}


def agent_model_options(
    specs: Sequence[AgentSpec], *, cfg: GroveConfig
) -> dict[str, tuple[ModelOptionView, ...]]:
    """Every agent's enriched catalog, keyed by agent name, over one snapshot read.

    The shape ``GET /models`` answers with no ``agent`` filter. Codex discovery
    shells out per agent, so the whole map belongs in an executor.
    """
    windows = published_context_windows(cfg)
    return {spec.name: model_options(spec, cfg=cfg, context_windows=windows) for spec in specs}
