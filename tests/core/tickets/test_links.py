"""Links: the human-input parser, PR metadata, and the capability predicates.

Four things under test, all part of "a pull request is another ref in the same
list": :class:`TicketLink` parsing (pure), ``resolve_link`` against the enabled
set (pure — no provider I/O happens for a bare id beyond the branch grammar),
``get_pull_request`` over ``httpx.MockTransport`` (the pulls namespace and the
merged-is-not-closed rule), and the ``can_comment`` capability seam the sticky
publisher gates on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from grove.core.config import (
    GiteaTicketConfig,
    GitHubTicketConfig,
    GroveConfig,
    LinearTicketConfig,
    TicketsConfig,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketRef, TicketSelector
from grove.core.errors import (
    TicketLinkAmbiguous,
    TicketLinkError,
    TicketPullRequestsUnsupported,
)
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider
from grove.core.tickets.link import TicketLink
from grove.core.tickets.provider import HttpTicketProvider
from grove.core.tickets.registry import TicketProviderRegistry
from tests.conftest import FakeTmux


def _transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


# ─── parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("42", ("42", "issue", None, None)),
        ("#42", ("42", "issue", None, None)),
        ("  #42  ", ("42", "issue", None, None)),
        ("owner/repo#42", ("42", "issue", "owner/repo", None)),
        ("ENG-123", ("ENG-123", "issue", None, None)),
        (
            "https://git.example.com/owner/repo/issues/42",
            ("42", "issue", "owner/repo", "git.example.com"),
        ),
        (
            "https://git.example.com/owner/repo/pulls/301",
            ("301", "pull_request", "owner/repo", "git.example.com"),
        ),
        (
            "https://github.com/Owner/Repo/pull/301",
            ("301", "pull_request", "owner/repo", "github.com"),
        ),
        (
            "https://github.com/o/r/pull/301/files#diff-1",
            ("301", "pull_request", "o/r", "github.com"),
        ),
        (
            "https://host.example/git/o/r/issues/7",  # gitea served under a subpath
            ("7", "issue", "o/r", "host.example"),
        ),
    ],
)
def test_parse_accepts_every_human_form(
    text: str, expected: tuple[str, str, str | None, str | None]
) -> None:
    link = TicketLink.parse(text)
    assert (link.id, link.kind, link.repo, link.host) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a ticket",
        "#abc",
        "-42",
        "owner/repo#abc",
        "https://github.com/o/r/releases/1",  # a URL, but not an issue/PR one
        "https://github.com/issues/1",  # no owner/repo before the marker
        "https://github.com/o/r/pull/",  # marker with no number after it
    ],
)
def test_parse_refuses_what_is_not_a_reference(text: str) -> None:
    with pytest.raises(TicketLinkError):
        TicketLink.parse(text)


# ─── resolution against the enabled set ───────────────────────────────────


def _registry(**enabled: bool) -> TicketProviderRegistry:
    cfg = TicketsConfig(
        gitea=GiteaTicketConfig(
            enabled=enabled.get("gitea", False),
            base_url="https://git.example.com",
            owner="o",
            repo="r",
        ),
        github=GitHubTicketConfig(enabled=enabled.get("github", False), owner="gh", repo="proj"),
        linear=LinearTicketConfig(enabled=enabled.get("linear", False), team_key="ENG"),
    )
    return TicketProviderRegistry(cfg, env={})


def test_resolve_link_infers_provider_and_kind_from_a_url() -> None:
    registry = _registry(gitea=True, github=True)
    assert registry.resolve_link("https://git.example.com/o/r/pulls/301") == TicketSelector(
        provider="gitea", id="301", kind="pull_request"
    )
    assert registry.resolve_link("https://github.com/gh/proj/issues/9") == TicketSelector(
        provider="github", id="9", kind="issue"
    )


def test_resolve_link_matches_github_api_host_against_the_browser_host() -> None:
    # base_url is api.github.com; the pasted URL says github.com.
    assert _registry(github=True).resolve_link("https://github.com/gh/proj/pull/2").provider == (
        "github"
    )


def test_resolve_link_uses_owner_repo_to_disambiguate() -> None:
    registry = _registry(gitea=True, github=True)
    assert registry.resolve_link("gh/proj#5").provider == "github"
    assert registry.resolve_link("o/r#5").provider == "gitea"


def test_resolve_link_bare_number_with_two_numeric_trackers_is_ambiguous() -> None:
    with pytest.raises(TicketLinkAmbiguous) as excinfo:
        _registry(gitea=True, github=True).resolve_link("#42")
    assert "gitea#42" in str(excinfo.value) and "github#42" in str(excinfo.value)


def test_resolve_link_bare_number_with_one_numeric_tracker_resolves() -> None:
    # Linear is enabled too, but its key grammar does not claim a bare number.
    registry = _registry(gitea=True, linear=True)
    assert registry.resolve_link("42") == TicketSelector(provider="gitea", id="42", kind="issue")


def test_resolve_link_keyed_id_goes_to_linear() -> None:
    assert _registry(gitea=True, linear=True).resolve_link("ENG-7").provider == "linear"


def test_resolve_link_refuses_a_repo_no_enabled_provider_serves() -> None:
    with pytest.raises(TicketLinkError):
        _registry(gitea=True).resolve_link("stranger/repo#5")


def test_resolve_link_refuses_a_host_no_enabled_provider_serves() -> None:
    with pytest.raises(TicketLinkError):
        _registry(gitea=True).resolve_link("https://github.com/o/r/issues/1")


# ─── PR metadata over the pulls namespace ─────────────────────────────────


def test_gitea_get_pull_request_reports_kind_and_merged_status() -> None:
    routes = {
        "/api/v1/repos/o/r/pulls/301": {
            "number": 301,
            "title": "Link contract",
            "html_url": "https://git/o/r/pulls/301",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-07-31T10:00:00Z",
            "assignees": [{"login": "alice"}],
        }
    }
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    ref = provider.get_pull_request("301")
    assert (ref.provider, ref.id, ref.kind, ref.status, ref.assignee) == (
        "gitea",
        "301",
        "pull_request",
        "merged",  # NOT "closed" — an orchestrator acts on the difference
        "alice",
    )


def test_github_get_pull_request_open_keeps_its_state() -> None:
    routes = {
        "/repos/o/r/pulls/7": {
            "number": 7,
            "title": "WIP",
            "html_url": "https://github.com/o/r/pull/7",
            "state": "open",
            "merged": False,
        }
    }
    provider = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(routes),
    )
    ref = provider.get_pull_request("7")
    assert (ref.kind, ref.status) == ("pull_request", "open")


def test_get_ticket_on_a_pr_number_reports_pull_request_kind() -> None:
    """The issues endpoint answers for a PR too — say so instead of lying."""
    gitea = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(
            {
                "/api/v1/repos/o/r/issues/301": {
                    "number": 301,
                    "state": "closed",
                    "pull_request": {"merged": True, "merged_at": "2026-07-31T10:00:00Z"},
                }
            }
        ),
    )
    assert (gitea.get_ticket("301").kind, gitea.get_ticket("301").status) == (
        "pull_request",
        "merged",
    )
    github = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport(
            {
                "/repos/o/r/issues/301": {
                    "number": 301,
                    "state": "open",
                    "pull_request": {"merged_at": None},
                }
            }
        ),
    )
    assert (github.get_ticket("301").kind, github.get_ticket("301").status) == (
        "pull_request",
        "open",
    )


def test_plain_issue_keeps_the_issue_default() -> None:
    provider = GitHubProvider(
        GitHubTicketConfig(enabled=True, owner="o", repo="r", token_env="T"),
        env={"T": "tok"},
        transport=_transport({"/repos/o/r/issues/5": {"number": 5, "state": "open"}}),
    )
    assert provider.get_ticket("5").kind == "issue"


def test_linear_has_no_pull_requests() -> None:
    provider = LinearProvider(LinearTicketConfig(enabled=True), env={})
    with pytest.raises(TicketPullRequestsUnsupported):
        provider.get_pull_request("ENG-1")


# ─── capability seams ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cls", [GiteaProvider, GitHubProvider, LinearProvider])
def test_comments_supported_agrees_with_what_the_class_overrides(
    cls: type[HttpTicketProvider],
) -> None:
    """The declaration and the implementation must not drift apart."""
    overrides = cls.post_comment is not HttpTicketProvider.post_comment
    assert cls.comments_supported is overrides


def test_can_comment_is_capability_and_credential() -> None:
    """The seam the sticky publisher must gate on before claiming a ref."""
    tokenless = GiteaProvider(GiteaTicketConfig(enabled=True, owner="o", repo="r"), env={})
    tokened = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T"), env={"T": "tok"}
    )
    linear = LinearProvider(LinearTicketConfig(enabled=True, token_env="L"), env={"L": "tok"})
    assert tokenless.can_comment is False  # enabled, capable, no credential
    assert linear.can_comment is False  # credentialed, enabled, not capable
    assert tokened.can_comment is True


def test_host_strips_the_api_prefix_only_where_there_is_one() -> None:
    assert GitHubProvider(GitHubTicketConfig(enabled=True), env={}).host == "github.com"
    assert (
        GiteaProvider(
            GiteaTicketConfig(enabled=True, base_url="https://git.example.com"), env={}
        ).host
        == "git.example.com"
    )


# ─── engine: attaching a link ─────────────────────────────────────────────


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "grove/"},
            "tmux": {"session_prefix": "test-"},
            "tickets": {
                "gitea": {
                    "enabled": True,
                    "base_url": "https://git.example.com",
                    "owner": "o",
                    "repo": "r",
                }
            },
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _workspace_id(manager: WorkspaceManager) -> str:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="Link work")).id


def test_attach_link_stores_a_pull_request_ref(manager: WorkspaceManager) -> None:
    ws_id = _workspace_id(manager)
    state = manager.attach_link(ws_id, "https://git.example.com/o/r/pulls/301")
    assert TicketRef(provider="gitea", id="301", kind="pull_request") in state.ticket_refs
    # Round-trips through the store with no migration (kind defaults to issue).
    assert manager.get(ws_id).ticket_refs == state.ticket_refs


def test_attach_link_is_idempotent_and_corrects_a_stale_kind(manager: WorkspaceManager) -> None:
    ws_id = _workspace_id(manager)
    first = manager.attach_ticket(ws_id, TicketSelector(provider="gitea", id="301"))
    assert [r.kind for r in first.ticket_refs] == ["issue"]

    corrected = manager.attach_link(ws_id, "https://git.example.com/o/r/pulls/301")
    assert [(r.id, r.kind) for r in corrected.ticket_refs] == [("301", "pull_request")]

    again = manager.attach_link(ws_id, "https://git.example.com/o/r/pulls/301")
    assert again.updated_at == corrected.updated_at  # no-op: nothing re-saved


def test_attach_link_refuses_an_ambiguous_reference_before_touching_the_store(
    manager: WorkspaceManager, tmp_path: Path, tmp_repo: Path
) -> None:
    cfg = manager.config.model_copy(
        update={
            "tickets": TicketsConfig(
                gitea=GiteaTicketConfig(enabled=True, base_url="https://git.example.com"),
                github=GitHubTicketConfig(enabled=True),
            )
        }
    )
    both = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state2.json")
    )
    ws_id = _workspace_id(both)
    with pytest.raises(TicketLinkAmbiguous):
        both.attach_link(ws_id, "#42")
    assert both.get(ws_id).ticket_refs == []
