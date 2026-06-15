"""TicketProviderRegistry — provider selection, ambiguity, and provider views."""

from __future__ import annotations

import pytest

from grove.core.config import TicketsConfig
from grove.core.contracts.tickets import TicketSelector
from grove.core.errors import TicketProviderNotConfigured
from grove.core.tickets import TicketProviderRegistry


def _registry(**enabled: bool) -> TicketProviderRegistry:
    cfg = TicketsConfig.model_validate(
        {
            "gitea": {"enabled": enabled.get("gitea", False), "owner": "o", "repo": "r"},
            "github": {"enabled": enabled.get("github", False), "owner": "o", "repo": "r"},
            "linear": {"enabled": enabled.get("linear", False), "team_key": "ENG"},
        }
    )
    return TicketProviderRegistry(cfg, env={})


def test_only_enabled_providers_are_built() -> None:
    reg = _registry(gitea=True, linear=True)
    assert [p.name for p in reg.providers()] == ["gitea", "linear"]


def test_get_unknown_provider_raises() -> None:
    reg = _registry(gitea=True)
    with pytest.raises(TicketProviderNotConfigured):
        reg.get("linear")


def test_single_match_is_unambiguous() -> None:
    reg = _registry(linear=True)
    refs = reg.parse_workspace_refs("grove/ENG-12-fix")
    assert len(refs) == 1
    assert (refs[0].provider, refs[0].id, refs[0].ambiguous) == ("linear", "ENG-12", False)


def test_bare_number_across_two_numeric_providers_is_ambiguous() -> None:
    reg = _registry(gitea=True, github=True)
    refs = reg.parse_workspace_refs("grove/123-fix")
    assert {(r.provider, r.id) for r in refs} == {("gitea", "123"), ("github", "123")}
    assert all(r.ambiguous for r in refs)


def test_keyed_and_numeric_do_not_collide() -> None:
    reg = _registry(gitea=True, github=True, linear=True)
    # gh- keyword is GitHub-only; no gitea/linear match → unambiguous.
    refs = reg.parse_workspace_refs("grove/gh-42-thing")
    assert [(r.provider, r.id, r.ambiguous) for r in refs] == [("github", "42", False)]


def test_no_match_yields_empty() -> None:
    reg = _registry(gitea=True, github=True, linear=True)
    assert reg.parse_workspace_refs("grove/just-a-slug") == []


def test_format_branch_name_delegates_to_named_provider() -> None:
    reg = _registry(linear=True)
    stem = reg.format_branch_name(TicketSelector(provider="linear", id="ENG-9"), "Do A Thing")
    assert stem == "ENG-9-do-a-thing"


def test_provider_views_report_configured_state() -> None:
    cfg = TicketsConfig.model_validate(
        {"gitea": {"enabled": True, "owner": "o", "repo": "r", "token_env": "T_GITEA"}}
    )
    # token present → configured True; absent → False.
    assert TicketProviderRegistry(cfg, env={"T_GITEA": "x"}).provider_views()[0].configured is True
    view = TicketProviderRegistry(cfg, env={}).provider_views()[0]
    assert view.configured is False
    assert view.provider == "gitea"
    assert view.label == "Gitea"
    assert view.context == "o/r"
