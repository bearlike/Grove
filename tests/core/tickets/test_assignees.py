"""Assignee writes and thread reads — the two forges' shapes, over MockTransport.

The interesting half is that the two trackers do NOT agree: GitHub ships an
additive ``issues/{n}/assignees`` sub-resource, Gitea has only a whole-list
``PATCH`` that replaces. So the Gitea arm is a read-modify-write, and the test
that matters is the one proving a human assignee SURVIVES it — a blind
``PATCH {"assignees": ["grove-ai"]}`` would evict every other assignee and look
perfectly correct in a test that only asserted the bot ended up on the ticket.

Requests are captured rather than merely routed, because the whole point here is
which method, which path and which body reached the wire.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from grove.core.config import GiteaTicketConfig, GitHubTicketConfig, LinearTicketConfig
from grove.core.errors import TicketAssigneesUnsupported
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider
from grove.core.tickets.provider import HttpTicketProvider

_GITEA_CFG = GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T")
_GITHUB_CFG = GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T")
_ENV = {"T": "tok"}


class _Wire:
    """Captures every request and answers from a path table."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.seen: list[tuple[str, str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            self.seen.append((request.method, request.url.path, body))
            payload = self.routes.get(request.url.path)
            if payload is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=payload)

        return httpx.MockTransport(handler)


# ─── the capability seam ───────────────────────────────────────────────────


@pytest.mark.parametrize("cls", [GiteaProvider, GitHubProvider, LinearProvider])
def test_assignees_supported_agrees_with_what_the_class_overrides(
    cls: type[HttpTicketProvider],
) -> None:
    """The declaration and the implementation must not drift apart."""
    overrides = cls.assign_self is not HttpTicketProvider.assign_self
    assert cls.assignees_supported is overrides


def test_can_assign_is_capability_and_credential() -> None:
    tokenless = GiteaProvider(GiteaTicketConfig(enabled=True, owner="o", repo="r"), env={})
    tokened = GiteaProvider(_GITEA_CFG, env=_ENV)
    linear = LinearProvider(LinearTicketConfig(enabled=True, token_env="L"), env={"L": "tok"})
    assert tokenless.can_assign is False  # enabled and capable, no credential
    assert tokened.can_assign is True
    assert linear.can_assign is False  # credentialed, no capability


def test_linear_refuses_assignment_with_the_typed_error() -> None:
    provider = LinearProvider(LinearTicketConfig(enabled=True, token_env="L"), env={"L": "tok"})
    with pytest.raises(TicketAssigneesUnsupported):
        provider.assign_self("ENG-1")


# ─── Gitea: read-modify-write, and the humans must survive it ──────────────


def test_gitea_assign_preserves_the_existing_human_assignee() -> None:
    """The bug a blind PATCH would ship: evicting whoever else was on the ticket."""
    wire = _Wire(
        {
            "/api/v1/user": {"login": "grove-ai"},
            "/api/v1/repos/o/r/issues/42": {
                "number": 42,
                "state": "open",
                "assignees": [{"login": "alice"}],
            },
        }
    )
    GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    patches = [b for m, _, b in wire.seen if m == "PATCH"]
    assert patches == [{"assignees": ["alice", "grove-ai"]}]


def test_gitea_assign_when_already_assigned_sends_no_patch_at_all() -> None:
    """Idempotent at the WIRE, not merely in effect — a repeated tick is free."""
    wire = _Wire(
        {
            "/api/v1/user": {"login": "grove-ai"},
            "/api/v1/repos/o/r/issues/42": {
                "number": 42,
                "state": "open",
                "assignees": [{"login": "grove-ai"}],
            },
        }
    )
    GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    assert [m for m, _, _ in wire.seen] == ["GET", "GET"]  # viewer, issue — no PATCH


def test_gitea_unassign_removes_only_the_bot() -> None:
    wire = _Wire(
        {
            "/api/v1/user": {"login": "grove-ai"},
            "/api/v1/repos/o/r/issues/42": {
                "number": 42,
                "state": "open",
                "assignees": [{"login": "alice"}, {"login": "grove-ai"}],
            },
        }
    )
    GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport()).unassign_self("42")
    patches = [b for m, _, b in wire.seen if m == "PATCH"]
    assert patches == [{"assignees": ["alice"]}]


# ─── GitHub: one additive round-trip, no read ──────────────────────────────


def test_github_assign_posts_to_the_additive_subresource() -> None:
    wire = _Wire({"/user": {"login": "grove-ai"}, "/repos/o/r/issues/42/assignees": {}})
    GitHubProvider(_GITHUB_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    assert wire.seen[-1] == ("POST", "/repos/o/r/issues/42/assignees", {"assignees": ["grove-ai"]})
    # No issue GET: the sub-resource is additive, so there is nothing to merge.
    assert not any(path == "/repos/o/r/issues/42" for _, path, _ in wire.seen)


def test_github_unassign_deletes_from_the_same_subresource() -> None:
    wire = _Wire({"/user": {"login": "grove-ai"}, "/repos/o/r/issues/42/assignees": {}})
    GitHubProvider(_GITHUB_CFG, env=_ENV, transport=wire.transport()).unassign_self("42")
    assert wire.seen[-1][0] == "DELETE"


# ─── the thread read that feeds the boot prompt ────────────────────────────


@pytest.mark.parametrize(
    ("provider_factory", "issue_path", "comments_path"),
    [
        (
            lambda t: GiteaProvider(_GITEA_CFG, env=_ENV, transport=t),
            "/api/v1/repos/o/r/issues/42",
            "/api/v1/repos/o/r/issues/42/comments",
        ),
        (
            lambda t: GitHubProvider(_GITHUB_CFG, env=_ENV, transport=t),
            "/repos/o/r/issues/42",
            "/repos/o/r/issues/42/comments",
        ),
    ],
)
def test_read_thread_carries_the_body_and_the_comments_in_order(
    provider_factory: Any, issue_path: str, comments_path: str
) -> None:
    """The body is the field ``TicketRef`` deliberately does not carry."""
    wire = _Wire(
        {
            issue_path: {"number": 42, "title": "Fix auth", "body": "the description"},
            comments_path: [
                {"id": 1, "body": "first", "user": {"login": "alice"}},
                {"id": 2, "body": "second", "user": {"login": "bob"}},
            ],
        }
    )
    thread = provider_factory(wire.transport()).read_thread("42")
    assert thread.body == "the description"
    assert thread.ref.title == "Fix auth"
    assert [c.body for c in thread.comments] == ["first", "second"]


def test_viewer_login_raises_when_the_forge_reports_no_account() -> None:
    """An empty identity must never become an empty assignee list."""
    wire = _Wire({"/api/v1/user": {}})
    provider = GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport())
    with pytest.raises(Exception, match="authenticated account"):
        provider.viewer_login()
