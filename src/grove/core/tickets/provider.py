"""The ticket-provider interface and its HTTP plumbing base classes.

One ``TicketProvider`` Protocol, implemented once per tracker and reused by
every client (TUI / API / MCP) through the :class:`TicketProviderRegistry` — no
client re-parses or re-links. A provider has two faces:

* **pure** — ``parse_branch_refs`` / ``format_branch_name`` translate between a
  branch name and canonical ticket keys with no I/O. These are the engine's
  only entry point (the branch name is the source of truth); they must never
  touch the network so ``create()`` stays deterministic and offline-safe.
* **I/O** — ``list_assigned`` / ``get_ticket`` reach the tracker's REST/GraphQL
  API. Side effects at the edge: only the daemon's ``/tickets`` routes call
  these, never the engine.

``HttpTicketProvider`` carries the shared wire plumbing (lazy ``httpx`` client,
one typed-error ``_request``) exactly as ``core/mewbo.py`` does. Concrete
providers normalize *shape* into the neutral :class:`TicketRef`, never semantics
(the provider-boundary rule). ``NumberTicketProvider`` factors the branch
grammar shared by the two numeric trackers (Gitea, GitHub); Linear's keyed
grammar lives in its own class.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx
from loguru import logger

from grove.core.contracts.tickets import TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.workspace import slug


@runtime_checkable
class TicketProvider(Protocol):
    """The single interface every tracker implements and every client consumes."""

    # ClassVar so concrete providers can pin these as class attributes (they are
    # per-class constants, not per-instance) and still satisfy the Protocol.
    name: ClassVar[TicketProviderName]
    label: ClassVar[str]

    @property
    def configured(self) -> bool:
        """True when a credential is present so the I/O methods can succeed."""
        ...

    @property
    def context(self) -> str | None:
        """Human-readable scope (``owner/repo`` or a Linear team key), or None."""
        ...

    def parse_branch_refs(self, branch: str) -> list[str]:
        """Canonical ticket keys this provider recognizes in ``branch``. Pure."""
        ...

    def format_branch_name(self, ticket_id: str, title: str | None) -> str:
        """The ``<key>-<slug>`` branch stem for a ticket (no worktree prefix). Pure."""
        ...

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        """Tickets assigned to the authenticated user. Network I/O."""
        ...

    def get_ticket(self, ticket_id: str) -> TicketRef:
        """Fetch one ticket by its canonical key. Network I/O."""
        ...


class HttpTicketProvider(ABC):
    """Shared HTTP plumbing for a tracker bound to one configuration.

    Subclasses supply ``name`` / ``label``, the auth ``headers`` and ``base_url``
    at construction, and implement the four interface methods. The ``httpx``
    client is built lazily on the first I/O call, so a provider used only for
    pure branch parsing (the engine's path) never opens a socket.
    """

    name: ClassVar[TicketProviderName]
    label: ClassVar[str]

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        configured: bool,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._headers = headers
        self._configured = configured
        self._transport = transport
        self._timeout = timeout
        self._http: httpx.Client | None = None

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    @abstractmethod
    def context(self) -> str | None: ...

    @abstractmethod
    def parse_branch_refs(self, branch: str) -> list[str]: ...

    @abstractmethod
    def format_branch_name(self, ticket_id: str, title: str | None) -> str: ...

    @abstractmethod
    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]: ...

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> TicketRef: ...

    # ─── wire plumbing (mirrors core/mewbo.py) ──────────────────────────────

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=self._headers,
                transport=self._transport,
            )
        return self._http

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """One wire round-trip; every failure mode narrows to TicketProviderError.

        Returns parsed JSON as-is (a dict or a list depending on the endpoint) —
        the concrete provider narrows it. ``httpx`` exceptions never escape.
        """
        if not self._configured:
            raise TicketProviderError(
                f"{self.name} provider has no credential "
                f"(set the configured token_env); cannot {method} {path}"
            )
        try:
            response = self._client().request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:  # timeout, connect failure, protocol error
            raise TicketProviderError(f"{self.name} {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise TicketProviderError(
                f"{self.name} {method} {path} returned {response.status_code}: "
                f"{self._error_reason(response)}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise TicketProviderError(
                f"{self.name} {method} {path} returned malformed JSON"
            ) from exc

    @staticmethod
    def _error_reason(response: httpx.Response) -> str:
        """Best-effort human reason from a JSON error body, else a text snippet."""
        try:
            data = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(data, dict):
            for key in ("message", "error", "errors"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        return response.text[:200]

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _enrich_logged(self, ticket_id: str, exc: TicketProviderError) -> None:
        """Uniform debug log for a best-effort enrichment miss (callers swallow)."""
        logger.debug("ticket enrich failed for {} {}: {}", self.name, ticket_id, exc)


class NumberTicketProvider(HttpTicketProvider):
    """Branch grammar shared by trackers keyed by a bare issue number (Gitea, GitHub).

    Recognizes three forms of a reference, all anchored to a word boundary so a
    digit buried inside a slug (``fix-v2-bug``) is never mistaken for an id:

    * the **leading numeric segment** — ``123-slug`` or ``grove/123-slug`` (the
      number opens the branch or a path segment, then a dash or end);
    * a **built-in keyword** prefix — ``gh-123`` / ``gitea-123`` (per subclass);
    * the **configured** ``branch_prefix`` — whatever the repo sets.

    ``format_branch_name`` emits ``{branch_prefix}{id}-{slug}``; with an empty
    prefix that is the bare ``123-slug`` the leading-segment rule round-trips.
    The bare form is deliberately ambiguous across two enabled numeric providers
    — the registry marks such refs ``ambiguous`` rather than guessing.
    """

    builtin_prefixes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, *, branch_prefix: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._branch_prefix = branch_prefix

    def parse_branch_refs(self, branch: str) -> list[str]:
        found: list[str] = []
        # Leading numeric segment: start-of-string or just after a '/'.
        for match in re.finditer(r"(?:^|/)(\d+)(?=-|$)", branch):
            found.append(match.group(1))
        # Keyword-prefixed anywhere, at a boundary: gh-123, gtea123, <prefix>123.
        keywords = [k for k in (*self.builtin_prefixes, self._branch_prefix.rstrip("-")) if k]
        for keyword in keywords:
            pattern = rf"(?<![A-Za-z0-9]){re.escape(keyword)}-?(\d+)(?![A-Za-z0-9])"
            for match in re.finditer(pattern, branch, flags=re.IGNORECASE):
                found.append(match.group(1))
        # Preserve first-seen order, drop duplicates (same id via two rules).
        return list(dict.fromkeys(found))

    def format_branch_name(self, ticket_id: str, title: str | None) -> str:
        stem = slug(title) if title else "ws"
        return f"{self._branch_prefix}{ticket_id}-{stem}"


__all__ = [
    "HttpTicketProvider",
    "NumberTicketProvider",
    "TicketProvider",
]
