"""Gitea Issues provider — the first tracker (Grove's own issues live in Gitea).

Wire facts (Gitea REST ``/api/v1``): auth is ``Authorization: token <pat>``;
``GET /repos/issues/search?type=issues&assigned=true`` returns the authenticated
user's assigned issues across repos (scoped down to ``owner/repo`` client-side);
``GET /repos/{owner}/{repo}/issues/{index}`` fetches one. An issue carries
``number`` (the canonical key), ``title``, ``html_url``, ``state``
(``open``/``closed``), and ``assignees`` — normalized into the neutral
:class:`TicketRef` here, never reshaped for semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from grove.core.config import GiteaTicketConfig
from grove.core.contracts.tickets import TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import NumberTicketProvider


class GiteaProvider(NumberTicketProvider):
    """Gitea Issues, bound to one ``GiteaTicketConfig``."""

    name: ClassVar[TicketProviderName] = "gitea"
    label: ClassVar[str] = "Gitea"
    builtin_prefixes: ClassVar[tuple[str, ...]] = ("gitea", "gtea")

    def __init__(
        self,
        cfg: GiteaTicketConfig,
        *,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = env.get(cfg.token_env, "")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        super().__init__(
            base_url=cfg.base_url.rstrip("/"),
            headers=headers,
            configured=bool(token),
            transport=transport,
            branch_prefix=cfg.branch_prefix,
        )
        self._owner = cfg.owner
        self._repo = cfg.repo

    @property
    def context(self) -> str | None:
        return f"{self._owner}/{self._repo}" if self._owner and self._repo else None

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        params = {
            "type": "issues",
            "state": status or "open",
            "assigned": "true",
            "limit": "50",
        }
        payload = self._request("GET", "/api/v1/repos/issues/search", params=params)
        issues = payload if isinstance(payload, list) else []
        scope = self.context
        refs: list[TicketRef] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if scope is not None and self._repo_full_name(issue) not in (scope, None):
                continue
            refs.append(self._to_ref(issue))
        return refs

    def get_ticket(self, ticket_id: str) -> TicketRef:
        if not (self._owner and self._repo):
            raise TicketProviderError(
                "gitea provider needs owner+repo configured to fetch a ticket by id"
            )
        path = f"/api/v1/repos/{self._owner}/{self._repo}/issues/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"gitea returned an unexpected shape for issue {ticket_id}")
        return self._to_ref(payload)

    # ─── shape normalization ────────────────────────────────────────────────

    def _to_ref(self, issue: dict[str, Any]) -> TicketRef:
        return TicketRef(
            provider="gitea",
            id=str(issue.get("number", "")),
            title=issue.get("title") or None,
            url=issue.get("html_url") or None,
            status=issue.get("state") or None,
            assignee=self._assignee(issue),
        )

    @staticmethod
    def _assignee(issue: dict[str, Any]) -> str | None:
        assignees = issue.get("assignees")
        if isinstance(assignees, list):
            for entry in assignees:
                if isinstance(entry, dict) and entry.get("login"):
                    return str(entry["login"])
        single = issue.get("assignee")
        if isinstance(single, dict) and single.get("login"):
            return str(single["login"])
        return None

    @staticmethod
    def _repo_full_name(issue: dict[str, Any]) -> str | None:
        repo = issue.get("repository")
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str):
            return str(repo["full_name"])
        return None


__all__ = ["GiteaProvider"]
