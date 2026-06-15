"""TicketProviderRegistry — the one place that holds the enabled providers.

Built from ``cfg.tickets``, it is the single surface both the engine (pure
branch parse/format) and the daemon (network list/get) speak to, so the
TUI/API/MCP never re-implement parsing or linking. It owns the one piece of
logic that is *not* provider-specific: aggregating each enabled provider's
branch matches into ``ticket_refs`` and flagging ambiguity when more than one
provider/key claims the same branch.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from grove.core.config import TicketsConfig
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.errors import TicketProviderNotConfigured
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider
from grove.core.tickets.provider import TicketProvider


class TicketProviderRegistry:
    """Holds the enabled :class:`TicketProvider` instances for one config.

    ``env`` and ``transport`` are the test seams: ``env`` supplies the token
    lookup without touching ``os.environ``, ``transport`` injects an
    ``httpx.MockTransport`` so a provider's I/O methods run against a fake wire.
    """

    def __init__(
        self,
        cfg: TicketsConfig,
        *,
        env: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved_env = env if env is not None else os.environ
        providers: dict[TicketProviderName, TicketProvider] = {}
        if cfg.gitea.enabled:
            providers["gitea"] = GiteaProvider(cfg.gitea, env=resolved_env, transport=transport)
        if cfg.github.enabled:
            providers["github"] = GitHubProvider(cfg.github, env=resolved_env, transport=transport)
        if cfg.linear.enabled:
            providers["linear"] = LinearProvider(cfg.linear, env=resolved_env, transport=transport)
        self._providers = providers

    # ─── access ─────────────────────────────────────────────────────────────

    def providers(self) -> list[TicketProvider]:
        """Every enabled provider, in canonical (gitea, github, linear) order."""
        return list(self._providers.values())

    def get(self, name: TicketProviderName) -> TicketProvider:
        """The named provider, or raise if it isn't enabled for this repo."""
        provider = self._providers.get(name)
        if provider is None:
            raise TicketProviderNotConfigured(f"ticket provider {name!r} is not enabled")
        return provider

    def provider_views(self) -> list[TicketProviderView]:
        return [
            TicketProviderView(
                provider=p.name,
                label=p.label,
                configured=p.configured,
                context=p.context,
            )
            for p in self._providers.values()
        ]

    # ─── pure: branch ⇄ refs (the engine's only entry point) ────────────────

    def parse_workspace_refs(self, branch: str) -> list[TicketRef]:
        """Derive ``ticket_refs`` from a branch name across every enabled provider.

        The branch name is the source of truth: each provider contributes the
        keys it recognizes. When the total number of matches across all
        providers exceeds one, every resulting ref is marked ``ambiguous`` — the
        association is uncertain and the user should confirm via attach/detach.
        A single deterministic match stays unambiguous.
        """
        matches: list[tuple[TicketProviderName, str]] = []
        for provider in self._providers.values():
            for key in provider.parse_branch_refs(branch):
                matches.append((provider.name, key))
        ambiguous = len(matches) > 1
        return [TicketRef(provider=name, id=key, ambiguous=ambiguous) for name, key in matches]

    def format_branch_name(self, selector: TicketSelector, title: str | None) -> str:
        """The ``<key>-<slug>`` branch stem for a selected ticket (no worktree prefix)."""
        return self.get(selector.provider).format_branch_name(selector.id, title)

    def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()


__all__ = ["TicketProviderRegistry"]
