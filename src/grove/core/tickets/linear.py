"""Linear provider (GraphQL).

Wire facts (Linear GraphQL, ``api.linear.app/graphql``): a personal API key goes
*raw* in the ``Authorization`` header (no ``Bearer``); ``viewer.assignedIssues``
lists the caller's issues; ``issues(filter:)`` resolves one by team key + number
(the GraphQL ``issue(id:)`` query wants a UUID, not the ``ENG-123`` identifier).
GraphQL answers 200 even on error, so the error envelope rides ``body.errors`` —
checked here, never leaked. The ``identifier`` (``ENG-123``) is the canonical key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from grove.core.config import LinearTicketConfig
from grove.core.contracts.tickets import TicketProviderName, TicketRef
from grove.core.errors import TicketProviderError
from grove.core.tickets.provider import HttpTicketProvider
from grove.core.workspace import slug

# state.type values Linear uses for a closed issue — excluded from an
# "open/active" listing, included when the caller asks for everything.
_CLOSED_STATE_TYPES = frozenset({"completed", "canceled"})

_ISSUE_FIELDS = "identifier title url state { name type } assignee { displayName }"


class LinearProvider(HttpTicketProvider):
    """Linear issues, bound to one ``LinearTicketConfig``.

    Extends :class:`HttpTicketProvider` directly (not the numeric base): Linear's
    branch grammar is the alphanumeric ``{TEAM}-{number}`` key, matched anywhere
    in the branch, scoped to ``team_key`` when configured.
    """

    name: ClassVar[TicketProviderName] = "linear"
    label: ClassVar[str] = "Linear"

    def __init__(
        self,
        cfg: LinearTicketConfig,
        *,
        env: Mapping[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = env.get(cfg.token_env, "")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        super().__init__(
            base_url=cfg.base_url,
            headers=headers,
            configured=bool(token),
            transport=transport,
        )
        self._team_key = cfg.team_key

    @property
    def context(self) -> str | None:
        return self._team_key

    # ─── pure: branch grammar ───────────────────────────────────────────────

    def parse_branch_refs(self, branch: str) -> list[str]:
        team = re.escape(self._team_key) if self._team_key else r"[A-Za-z][A-Za-z0-9]*"
        pattern = rf"(?<![A-Za-z0-9])({team})-(\d+)(?![A-Za-z0-9])"
        found: list[str] = []
        for match in re.finditer(pattern, branch, flags=re.IGNORECASE):
            found.append(f"{match.group(1).upper()}-{match.group(2)}")
        return list(dict.fromkeys(found))

    def format_branch_name(self, ticket_id: str, title: str | None) -> str:
        stem = slug(title) if title else "ws"
        return f"{ticket_id}-{stem}"

    # ─── network ────────────────────────────────────────────────────────────

    def list_assigned(self, *, status: str | None = None) -> list[TicketRef]:
        query = (
            f"query {{ viewer {{ assignedIssues(first: 50) {{ nodes {{ {_ISSUE_FIELDS} }} }} }} }}"
        )
        data = self._graphql(query)
        nodes = self._nodes(data.get("viewer", {}).get("assignedIssues", {}))
        include_closed = status not in (None, "open", "active")
        refs: list[TicketRef] = []
        for node in nodes:
            if not include_closed and self._state_type(node) in _CLOSED_STATE_TYPES:
                continue
            refs.append(self._to_ref(node))
        return refs

    def get_ticket(self, ticket_id: str) -> TicketRef:
        team, _, number = ticket_id.partition("-")
        if not number.isdigit():
            raise TicketProviderError(f"linear ticket id {ticket_id!r} is not a TEAM-NUMBER key")
        filt: dict[str, Any] = {"number": {"eq": int(number)}, "team": {"key": {"eq": team}}}
        query = (
            f"query($filter: IssueFilter) {{ issues(filter: $filter, first: 1) "
            f"{{ nodes {{ {_ISSUE_FIELDS} }} }} }}"
        )
        data = self._graphql(query, {"filter": filt})
        nodes = self._nodes(data.get("issues", {}))
        if not nodes:
            raise TicketProviderError(f"linear issue {ticket_id} not found")
        return self._to_ref(nodes[0])

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables
        # POST to the absolute configured endpoint: an empty relative path makes
        # httpx append a trailing slash (`/graphql/`), which Linear rejects.
        payload = self._request("POST", self._base_url, json_body=body)
        if not isinstance(payload, dict):
            raise TicketProviderError("linear returned a non-object GraphQL response")
        if payload.get("errors"):
            raise TicketProviderError(f"linear GraphQL error: {payload['errors']}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    # ─── shape normalization ────────────────────────────────────────────────

    def _to_ref(self, node: dict[str, Any]) -> TicketRef:
        state = node.get("state")
        assignee = node.get("assignee")
        return TicketRef(
            provider="linear",
            id=str(node.get("identifier", "")),
            title=node.get("title") or None,
            url=node.get("url") or None,
            status=(state.get("name") if isinstance(state, dict) else None) or None,
            assignee=(assignee.get("displayName") if isinstance(assignee, dict) else None) or None,
        )

    @staticmethod
    def _state_type(node: dict[str, Any]) -> str | None:
        state = node.get("state")
        return state.get("type") if isinstance(state, dict) else None

    @staticmethod
    def _nodes(connection: Any) -> list[dict[str, Any]]:
        nodes = connection.get("nodes") if isinstance(connection, dict) else None
        if not isinstance(nodes, list):
            return []
        return [n for n in nodes if isinstance(n, dict)]


__all__ = ["LinearProvider"]
