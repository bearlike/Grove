"""GitHub Issues provider.

Wire facts (GitHub REST, ``api.github.com`` or an Enterprise ``/api/v3`` root):
auth is ``Authorization: Bearer <pat>`` plus the versioned ``Accept`` header;
``GET /issues?filter=assigned&state=open`` returns the authenticated user's
assigned issues across repos (scoped to ``owner/repo`` client-side);
``GET /repos/{owner}/{repo}/issues/{number}`` fetches one. The numeric ``number``
is the canonical key. Pull requests come back on the issues endpoint too — they
carry a ``pull_request`` member, which we drop so only real issues normalize
into the neutral :class:`TicketRef`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from grove.core.config import GitHubTicketConfig
from grove.core.contracts.tickets import TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import NumberTicketProvider


class GitHubProvider(NumberTicketProvider):
    """GitHub Issues, bound to one ``GitHubTicketConfig``."""

    name: ClassVar[TicketProviderName] = "github"
    label: ClassVar[str] = "GitHub"
    builtin_prefixes: ClassVar[tuple[str, ...]] = ("gh",)

    def __init__(
        self,
        cfg: GitHubTicketConfig,
        *,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = env.get(cfg.token_env, "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
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
        params = {"filter": "assigned", "state": status or "open", "per_page": "50"}
        payload = self._request("GET", "/issues", params=params)
        issues = payload if isinstance(payload, list) else []
        scope = self.context
        refs: list[TicketRef] = []
        for issue in issues:
            if not isinstance(issue, dict) or "pull_request" in issue:
                continue
            if scope is not None and self._repo_full_name(issue) not in (scope, None):
                continue
            refs.append(self._to_ref(issue))
        return refs

    def get_ticket(self, ticket_id: str) -> TicketRef:
        if not (self._owner and self._repo):
            raise TicketProviderError(
                "github provider needs owner+repo configured to fetch a ticket by id"
            )
        path = f"/repos/{self._owner}/{self._repo}/issues/{ticket_id}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise TicketProviderError(f"github returned an unexpected shape for issue {ticket_id}")
        return self._to_ref(payload)

    # ─── shape normalization ────────────────────────────────────────────────

    def _to_ref(self, issue: dict[str, Any]) -> TicketRef:
        return TicketRef(
            provider="github",
            id=str(issue.get("number", "")),
            title=issue.get("title") or None,
            url=issue.get("html_url") or None,
            status=issue.get("state") or None,
            assignee=self._assignee(issue),
        )

    @staticmethod
    def _assignee(issue: dict[str, Any]) -> str | None:
        assignee = issue.get("assignee")
        if isinstance(assignee, dict) and assignee.get("login"):
            return str(assignee["login"])
        assignees = issue.get("assignees")
        if isinstance(assignees, list):
            for entry in assignees:
                if isinstance(entry, dict) and entry.get("login"):
                    return str(entry["login"])
        return None

    @staticmethod
    def _repo_full_name(issue: dict[str, Any]) -> str | None:
        # The list endpoint nests a `repository`; the single-issue endpoint
        # doesn't, but `repository_url` ends with `/repos/{owner}/{repo}`.
        repo = issue.get("repository")
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str):
            return str(repo["full_name"])
        url = issue.get("repository_url")
        if isinstance(url, str) and "/repos/" in url:
            return url.split("/repos/", 1)[1]
        return None


__all__ = ["GitHubProvider"]
