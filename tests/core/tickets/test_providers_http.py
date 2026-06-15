"""Provider network methods over httpx.MockTransport — shape normalization only.

Fakes sit at the HTTP boundary; the request building, the JSON parsing, and the
TicketRef normalization all run for real. Payloads mirror each tracker's wire
shape. Auth/transport failures must narrow to TicketProviderError, never leak
httpx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from grove.core.config import GiteaTicketConfig, GitHubTicketConfig, LinearTicketConfig
from grove.core.errors import TicketProviderError
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider


def _transport(routes: dict[str, Any]) -> httpx.MockTransport:
    """Map a request path to a JSON payload (or an httpx.Response factory)."""

    def handler(request: httpx.Request) -> httpx.Response:
        for path, payload in routes.items():
            if request.url.path == path:
                if isinstance(payload, httpx.Response):
                    return payload
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


# ─── Gitea ────────────────────────────────────────────────────────────────


def test_gitea_list_assigned_normalizes_and_scopes() -> None:
    routes = {
        "/api/v1/repos/issues/search": [
            {
                "number": 5,
                "title": "Fix login",
                "html_url": "https://git/x/5",
                "state": "open",
                "assignees": [{"login": "alice"}],
                "repository": {"full_name": "o/r"},
            },
            {  # different repo → filtered out by owner/repo scope
                "number": 9,
                "title": "Elsewhere",
                "repository": {"full_name": "other/repo"},
            },
        ]
    }
    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    refs = p.list_assigned()
    assert len(refs) == 1
    r = refs[0]
    assert (r.provider, r.id, r.title, r.url, r.status, r.assignee) == (
        "gitea",
        "5",
        "Fix login",
        "https://git/x/5",
        "open",
        "alice",
    )


def test_gitea_get_ticket() -> None:
    routes = {
        "/api/v1/repos/o/r/issues/5": {
            "number": 5,
            "title": "Fix login",
            "html_url": "https://git/x/5",
            "state": "closed",
            "assignee": {"login": "bob"},
        }
    }
    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    r = p.get_ticket("5")
    assert (r.id, r.status, r.assignee) == ("5", "closed", "bob")


def test_gitea_uses_token_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json=[])

    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "secret"},
        transport=httpx.MockTransport(handler),
    )
    p.list_assigned()
    assert seen["auth"] == "token secret"


def test_unconfigured_provider_raises_before_network() -> None:
    p = GiteaProvider(GiteaTicketConfig(enabled=True, owner="o", repo="r"), env={})
    assert p.configured is False
    with pytest.raises(TicketProviderError, match="no credential"):
        p.list_assigned()


def test_http_error_narrows_to_ticket_provider_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(boom),
    )
    with pytest.raises(TicketProviderError):
        p.list_assigned()


def test_status_4xx_narrows_to_ticket_provider_error() -> None:
    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport({}),  # everything 404s
    )
    with pytest.raises(TicketProviderError, match="404"):
        p.get_ticket("123")


# ─── GitHub ─────────────────────────────────────────────────────────────────


def test_github_list_assigned_drops_pull_requests() -> None:
    routes = {
        "/issues": [
            {
                "number": 42,
                "title": "Issue",
                "html_url": "https://gh/42",
                "state": "open",
                "assignee": {"login": "carol"},
                "repository": {"full_name": "o/r"},
            },
            {  # a PR rides the issues endpoint — must be dropped
                "number": 43,
                "title": "A PR",
                "pull_request": {"url": "..."},
                "repository": {"full_name": "o/r"},
            },
        ]
    }
    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    refs = p.list_assigned()
    assert [r.id for r in refs] == ["42"]
    assert refs[0].assignee == "carol"


def test_github_get_ticket_and_bearer_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={"number": 7, "title": "T", "html_url": "u", "state": "open"},
        )

    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "ghp"},
        transport=httpx.MockTransport(handler),
    )
    r = p.get_ticket("7")
    assert r.id == "7"
    assert seen["auth"] == "Bearer ghp"


# ─── Linear (GraphQL) ────────────────────────────────────────────────────────


def test_linear_list_assigned_filters_closed_by_default() -> None:
    routes = {
        "/graphql": {
            "data": {
                "viewer": {
                    "assignedIssues": {
                        "nodes": [
                            {
                                "identifier": "ENG-1",
                                "title": "Open one",
                                "url": "https://lin/ENG-1",
                                "state": {"name": "In Progress", "type": "started"},
                                "assignee": {"displayName": "Dana"},
                            },
                            {
                                "identifier": "ENG-2",
                                "title": "Done one",
                                "url": "https://lin/ENG-2",
                                "state": {"name": "Done", "type": "completed"},
                                "assignee": {"displayName": "Dana"},
                            },
                        ]
                    }
                }
            }
        }
    }
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"),
        env={"T": "lin_xxx"},
        transport=_transport(routes),
    )
    refs = p.list_assigned()
    assert [r.id for r in refs] == ["ENG-1"]
    assert (refs[0].status, refs[0].assignee) == ("In Progress", "Dana")
    # status="all" includes the completed one
    assert [r.id for r in p.list_assigned(status="all")] == ["ENG-1", "ENG-2"]


def test_linear_get_ticket_by_identifier() -> None:
    routes = {
        "/graphql": {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "identifier": "ENG-9",
                            "title": "Found",
                            "url": "https://lin/ENG-9",
                            "state": {"name": "Todo", "type": "unstarted"},
                            "assignee": None,
                        }
                    ]
                }
            }
        }
    }
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"),
        env={"T": "lin_xxx"},
        transport=_transport(routes),
    )
    r = p.get_ticket("ENG-9")
    assert (r.id, r.title, r.assignee) == ("ENG-9", "Found", None)


def test_linear_graphql_errors_narrow() -> None:
    routes = {"/graphql": {"errors": [{"message": "bad query"}]}}
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"),
        env={"T": "lin_xxx"},
        transport=_transport(routes),
    )
    with pytest.raises(TicketProviderError, match="GraphQL"):
        p.list_assigned()


def test_linear_get_ticket_not_found() -> None:
    routes = {"/graphql": {"data": {"issues": {"nodes": []}}}}
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"),
        env={"T": "lin_xxx"},
        transport=_transport(routes),
    )
    with pytest.raises(TicketProviderError, match="not found"):
        p.get_ticket("ENG-404")


def test_linear_uses_raw_authorization_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": {"viewer": {"assignedIssues": {"nodes": []}}}})

    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"),
        env={"T": "lin_api_key"},
        transport=httpx.MockTransport(handler),
    )
    p.list_assigned()
    assert seen["auth"] == "lin_api_key"  # raw, no "Bearer "
