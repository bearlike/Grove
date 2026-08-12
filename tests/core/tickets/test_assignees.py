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
from grove.core.errors import TicketAssigneesUnsupported, TicketBodyUnsupported
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


@pytest.mark.parametrize("cls", [GiteaProvider, GitHubProvider, LinearProvider])
def test_body_supported_agrees_with_what_the_class_overrides(
    cls: type[HttpTicketProvider],
) -> None:
    """The same drift guard the other two capabilities carry, for the third."""
    overrides = cls.update_body is not HttpTicketProvider.update_body
    assert cls.body_supported is overrides


def test_can_edit_body_is_capability_and_credential() -> None:
    tokenless = GiteaProvider(GiteaTicketConfig(enabled=True, owner="o", repo="r"), env={})
    tokened = GiteaProvider(_GITEA_CFG, env=_ENV)
    linear = LinearProvider(LinearTicketConfig(enabled=True, token_env="L"), env={"L": "tok"})
    assert tokenless.can_edit_body is False  # enabled and capable, no credential
    assert tokened.can_edit_body is True
    assert linear.can_edit_body is False  # credentialed, no capability


def test_linear_refuses_a_body_write_with_the_typed_error() -> None:
    """A body write is its own capability, so it gets its own typed refusal.

    Rewriting a description edits what a HUMAN wrote, which is a strictly larger
    claim than adding a comment beside it — so a caller must be able to tell this
    refusal from the comment one rather than inferring it.
    """
    provider = LinearProvider(LinearTicketConfig(enabled=True, token_env="L"), env={"L": "tok"})
    with pytest.raises(TicketBodyUnsupported):
        provider.update_body("ENG-1", "text")


def test_gitea_body_round_trip_reads_then_patches() -> None:
    wire = _Wire(
        {
            "/api/v1/repos/o/r/issues/42": {"number": 42, "state": "open", "body": "the original"},
        }
    )
    provider = GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport())
    assert provider.read_body("42") == "the original"
    provider.update_body("42", "the original\n\nfooter")
    assert wire.seen[-1] == (
        "PATCH",
        "/api/v1/repos/o/r/issues/42",
        {"body": "the original\n\nfooter"},
    )


def test_github_body_round_trip_reads_then_patches() -> None:
    wire = _Wire({"/repos/o/r/issues/42": {"number": 42, "state": "open", "body": "the original"}})
    provider = GitHubProvider(_GITHUB_CFG, env=_ENV, transport=wire.transport())
    assert provider.read_body("42") == "the original"
    provider.update_body("42", "next")
    assert wire.seen[-1] == ("PATCH", "/repos/o/r/issues/42", {"body": "next"})


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
    assert GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    patches = [b for m, _, b in wire.seen if m == "PATCH"]
    assert patches == [{"assignees": ["alice", "grove-ai"]}]


def test_gitea_assign_when_already_assigned_sends_no_patch_at_all() -> None:
    """Idempotent at the WIRE, not merely in effect — a repeated tick is free.

    The same unchanged-list branch is what makes the return value honest: no
    PATCH means this call did not assign the ticket, which is what stops a
    caller claiming — and later releasing — an assignment it did not make.
    """
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
    assert not GiteaProvider(_GITEA_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
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


# ─── GitHub: the additive sub-resource, plus a read that answers "who did it" ─


def test_github_assign_posts_to_the_additive_subresource() -> None:
    wire = _Wire(
        {
            "/user": {"login": "grove-ai"},
            "/repos/o/r/issues/42": {"number": 42, "state": "open", "assignees": []},
            "/repos/o/r/issues/42/assignees": {},
        }
    )
    assert GitHubProvider(_GITHUB_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    assert wire.seen[-1] == ("POST", "/repos/o/r/issues/42/assignees", {"assignees": ["grove-ai"]})


def test_github_assign_reports_false_when_the_login_is_already_there() -> None:
    """The read exists ONLY to answer this, and the answer gates a later unassign.

    GitHub's sub-resource is additive, so the POST alone would succeed
    identically whether or not a human had already assigned the bot — and a
    caller that read that as "I assigned it" would later unassign somebody
    else's decision. The extra GET buys exactly that distinction.
    """
    wire = _Wire(
        {
            "/user": {"login": "grove-ai"},
            "/repos/o/r/issues/42": {
                "number": 42,
                "state": "open",
                "assignees": [{"login": "grove-ai"}],
            },
        }
    )
    assert not GitHubProvider(_GITHUB_CFG, env=_ENV, transport=wire.transport()).assign_self("42")
    assert not any(method == "POST" for method, _, _ in wire.seen)


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
