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
from grove.core.errors import TicketCommentsUnsupported, TicketProviderError
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


def test_gitea_get_pull_request_maps_draft() -> None:
    routes = {
        "/api/v1/repos/o/r/pulls/5": {
            "number": 5,
            "title": "Work in progress",
            "html_url": "https://git/x/5",
            "state": "open",
            "draft": True,
        }
    }
    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    r = p.get_pull_request("5")
    assert (r.kind, r.draft) == ("pull_request", True)


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


def test_github_get_pull_request_maps_draft() -> None:
    routes = {
        "/repos/o/r/pulls/7": {
            "number": 7,
            "title": "Work in progress",
            "html_url": "https://gh/7",
            "state": "open",
            "draft": True,
        }
    }
    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    r = p.get_pull_request("7")
    assert (r.kind, r.draft) == ("pull_request", True)


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


# ─── comment I/O ─────────────────────────────────────────────────────────


def test_gitea_list_comments_normalizes() -> None:
    routes = {
        "/api/v1/repos/o/r/issues/5/comments": [
            {
                "id": 100,
                "body": "hello",
                "user": {"login": "alice"},
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]
    }
    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    comments = p.list_comments("5")
    assert len(comments) == 1
    c = comments[0]
    assert (c.id, c.body, c.author) == ("100", "hello", "alice")
    assert c.created_at is not None


def test_gitea_post_comment_returns_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={"id": 200, "body": "posted", "user": {"login": "bot"}, "created_at": None},
        )

    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    comment = p.post_comment("5", "posted")
    assert comment.id == "200"
    assert seen["path"] == "/api/v1/repos/o/r/issues/5/comments"
    assert b"posted" in seen["body"]


def test_gitea_edit_comment_patches_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": 200, "body": "edited"})

    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    assert p.edit_comment("200", "edited") is None
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/api/v1/repos/o/r/issues/comments/200"


def test_gitea_react_posts_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json=[{"content": "rocket"}])

    p = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    assert p.react("200", "rocket") is None
    assert seen["path"] == "/api/v1/repos/o/r/issues/comments/200/reactions"
    assert b"rocket" in seen["body"]


def test_gitea_comment_ops_need_owner_repo() -> None:
    p = GiteaProvider(GiteaTicketConfig(enabled=True, token_env="T"), env={"T": "tok"})
    with pytest.raises(TicketProviderError, match="owner\\+repo"):
        p.list_comments("5")
    with pytest.raises(TicketProviderError, match="owner\\+repo"):
        p.post_comment("5", "x")
    with pytest.raises(TicketProviderError, match="owner\\+repo"):
        p.edit_comment("200", "x")
    with pytest.raises(TicketProviderError, match="owner\\+repo"):
        p.react("200", "eyes")


def test_github_list_comments_normalizes() -> None:
    routes = {
        "/repos/o/r/issues/42/comments": [
            {
                "id": 300,
                "body": "hi",
                "user": {"login": "carol"},
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]
    }
    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    comments = p.list_comments("42")
    assert [(c.id, c.body, c.author) for c in comments] == [("300", "hi", "carol")]


def test_github_post_comment_returns_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": 400, "body": "posted", "user": None})

    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    comment = p.post_comment("42", "posted")
    assert comment.id == "400"
    assert comment.author is None
    assert seen["path"] == "/repos/o/r/issues/42/comments"


def test_github_edit_comment_patches_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": 400, "body": "edited"})

    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    assert p.edit_comment("400", "edited") is None
    assert seen["method"] == "PATCH"
    assert seen["path"] == "/repos/o/r/issues/comments/400"


def test_github_react_posts_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"content": "eyes"})

    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=httpx.MockTransport(handler),
    )
    assert p.react("400", "eyes") is None
    assert seen["path"] == "/repos/o/r/issues/comments/400/reactions"
    assert b"eyes" in seen["body"]


def test_github_comment_ops_need_owner_repo() -> None:
    p = GitHubProvider(GitHubTicketConfig(enabled=True, token_env="T"), env={"T": "tok"})
    with pytest.raises(TicketProviderError, match="owner\\+repo"):
        p.list_comments("42")


def test_linear_comment_methods_raise_capability_gap() -> None:
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"), env={"T": "lin_xxx"}
    )
    with pytest.raises(TicketCommentsUnsupported):
        p.list_comments("ENG-1")
    with pytest.raises(TicketCommentsUnsupported):
        p.post_comment("ENG-1", "x")
    with pytest.raises(TicketCommentsUnsupported):
        p.edit_comment("c1", "x")
    with pytest.raises(TicketCommentsUnsupported):
        p.react("c1", "eyes")


# ─── link/web-root URL builders (commit_url / branch_url / web_root) ───────
#
# All pure: no network, no transport injected. web_root strips the leading
# "api." the same way `host` does (so a pasted-link match and a built link
# agree on which forge they mean) but keeps the PORT, which `host` drops —
# a self-hosted forge on a non-default port is unreachable without it.


def test_gitea_commit_and_branch_url_shape() -> None:
    p = GiteaProvider(
        GiteaTicketConfig(
            enabled=True, owner="o", repo="r", token_env="T", base_url="https://git.example.com"
        ),
        env={"T": "tok"},
    )
    assert p.commit_url("abc123") == "https://git.example.com/o/r/commit/abc123"
    assert p.branch_url("main") == "https://git.example.com/o/r/src/branch/main"


def test_github_commit_and_branch_url_shape() -> None:
    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"), env={"T": "tok"}
    )
    assert p.commit_url("abc123") == "https://github.com/o/r/commit/abc123"
    assert p.branch_url("main") == "https://github.com/o/r/tree/main"


def test_github_web_root_never_carries_the_api_subdomain() -> None:
    """A built link must land on github.com, never on the api host it was built from."""
    p = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"), env={"T": "tok"}
    )
    assert p.web_root == "https://github.com"
    assert "api." not in (p.commit_url("abc123") or "")


def test_github_enterprise_base_url_strips_api_v3_to_the_browser_root() -> None:
    """A GHE base_url (`https://host/api/v3`) has no `api.` subdomain to strip —
    the `/api/v3` path is dropped by urlsplit's own path/host split, and the
    browser root is just the bare host."""
    p = GitHubProvider(
        GitHubTicketConfig(
            enabled=True,
            owner="o",
            repo="r",
            token_env="T",
            base_url="https://ghe.example.com/api/v3",
        ),
        env={"T": "tok"},
    )
    assert p.web_root == "https://ghe.example.com"
    assert p.commit_url("abc123") == "https://ghe.example.com/o/r/commit/abc123"


def test_web_root_keeps_a_non_default_port_that_host_deliberately_drops() -> None:
    """`host` (the link-matching side) has no use for a port and would gain a
    false mismatch by keeping one; `web_root` (the link-building side) needs it
    back, or a forge on a non-standard port produces an unreachable URL."""
    p = GiteaProvider(
        GiteaTicketConfig(
            enabled=True, owner="o", repo="r", token_env="T", base_url="http://git.example.com:3000"
        ),
        env={"T": "tok"},
    )
    assert p.host == "git.example.com"
    assert p.web_root == "http://git.example.com:3000"
    assert p.commit_url("abc123") == "http://git.example.com:3000/o/r/commit/abc123"


def test_branch_url_keeps_slashes_but_percent_quotes_everything_else() -> None:
    """A branch name's `/` is a path separator in the forge's own URL and must
    survive unquoted; any other character that would otherwise read as syntax
    (space, `#`, `?`) is quoted so the name can never be mistaken for it."""
    p = GiteaProvider(
        GiteaTicketConfig(
            enabled=True, owner="o", repo="r", token_env="T", base_url="https://git.example.com"
        ),
        env={"T": "tok"},
    )
    assert (
        p.branch_url("feature/my branch")
        == "https://git.example.com/o/r/src/branch/feature/my%20branch"
    )
    assert p.branch_url("fix#42?") == "https://git.example.com/o/r/src/branch/fix%2342%3F"


def test_unscoped_provider_has_no_repo_to_link_into() -> None:
    """No owner/repo configured means no repo_web_url and no commit/branch link —
    `None` is the honest answer, never a link that lands nowhere."""
    p = GiteaProvider(GiteaTicketConfig(enabled=True, token_env="T"), env={"T": "tok"})
    assert p.repo_web_url is None
    assert p.commit_url("abc123") is None
    assert p.branch_url("main") is None


def test_linear_has_no_commit_or_branch_destination() -> None:
    """Linear fronts no git repo at all, so it inherits HttpTicketProvider's base
    `None` default for both — a caller renders plain text rather than a link
    that lands nowhere (the same contract the unscoped-forge case pins above)."""
    p = LinearProvider(
        LinearTicketConfig(enabled=True, team_key="ENG", token_env="T"), env={"T": "lin_xxx"}
    )
    assert p.commit_url("abc123") is None
    assert p.branch_url("main") is None
