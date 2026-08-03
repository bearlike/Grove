"""TicketProviderRegistry — the one place that holds the enabled providers.

Built from ``cfg.tickets``, it is the single surface both the engine (pure
branch parse/format) and the daemon (network list/get) speak to, so the
TUI/API/MCP never re-implement parsing or linking. It owns the one piece of
logic that is *not* provider-specific: aggregating each enabled provider's
branch matches into ``ticket_refs`` and flagging ambiguity when more than one
provider/key claims the same branch — and, for manually typed input, resolving
a parsed :class:`TicketLink` against the enabled set (``resolve_link``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import httpx

from grove.core.config import TicketsConfig
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.errors import (
    TicketLinkAmbiguous,
    TicketLinkError,
    TicketProviderNotConfigured,
)
from grove.core.tickets.credentials import TicketEnv
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider
from grove.core.tickets.link import TicketLink
from grove.core.tickets.provider import TicketProvider


class TicketProviderRegistry:
    """Holds the enabled :class:`TicketProvider` instances for one config.

    ``env`` and ``transport`` are the test seams: ``env`` supplies the token
    lookup without touching ``os.environ`` or the configured source, ``transport``
    injects an ``httpx.MockTransport`` so a provider's I/O methods run against a
    fake wire.

    **Nothing here captures a credential**, which is what makes it safe for a
    ``WorkspaceManager`` to cache this registry for a daemon's whole life: each
    provider holds the ``env`` MAPPING and looks its own ``token_env`` up per
    request. In production that mapping is a
    :class:`~grove.core.tickets.credentials.TicketEnv`, which reads the section's
    ``env_file`` / ``env_command`` first (so a credential produced after the
    process started is found) and the process environment second. ``repo_root``
    is what a repo-relative ``env_file`` resolves against — the same repo whose
    cascade produced ``cfg``.
    """

    def __init__(
        self,
        cfg: TicketsConfig,
        *,
        repo_root: Path | None = None,
        env: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved_env = env if env is not None else TicketEnv(cfg, repo_root=repo_root)
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

    def resolve_link(self, text: str) -> TicketSelector:
        """Turn human-typed text (a URL, ``#42``, ``42``, ``owner/repo#42``) into a selector.

        The counterpart of :meth:`parse_workspace_refs` for *manual* input, and
        the second piece of non-provider-specific logic this class owns: the
        parse is pure (:class:`TicketLink`), and only the enabled set — which
        lives here — can say which provider a reference belongs to.

        Ambiguity is loud. Where a branch that two providers claim yields refs
        flagged ``ambiguous`` (the association is a guess the UI can question),
        an explicit attach must name one ticket, so more than one candidate
        raises :class:`TicketLinkAmbiguous` naming them rather than silently
        taking the first. A pull-request link resolves to the same selector
        shape carrying ``kind="pull_request"``.
        """
        link = TicketLink.parse(text)
        candidates = [p for p in self._providers.values() if link.candidate(p)]
        if not candidates:
            raise TicketLinkError(
                f"no enabled ticket provider owns {text!r} "
                f"(enabled: {', '.join(p.name for p in self._providers.values()) or 'none'})"
            )
        if len(candidates) > 1:
            raise TicketLinkAmbiguous(
                f"{text!r} could be {' or '.join(f'{p.name}#{link.id}' for p in candidates)}"
                " — qualify it with a full URL or owner/repo#id"
            )
        return TicketSelector(provider=candidates[0].name, id=link.id, kind=link.kind)

    def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()


__all__ = ["TicketProviderRegistry"]
