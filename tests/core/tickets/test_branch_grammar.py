"""Pure branch parse/format grammar per provider — the source-of-truth contract.

No I/O: every provider is constructed with an empty env (no token) and never
touches the network. These pin the canonical-key extraction and the
ticket-aware branch stem the manager prepends ``worktree.branch_prefix`` to.
"""

from __future__ import annotations

from grove.core.config import GiteaTicketConfig, GitHubTicketConfig, LinearTicketConfig
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider


def _gitea(**kw: object) -> GiteaProvider:
    return GiteaProvider(GiteaTicketConfig(**kw), env={})


def _github(**kw: object) -> GitHubProvider:
    return GitHubProvider(GitHubTicketConfig(**kw), env={})


def _linear(**kw: object) -> LinearProvider:
    return LinearProvider(LinearTicketConfig(**kw), env={})


# ─── Linear: keyed {TEAM}-{N} anywhere ───────────────────────────────────────


def test_linear_parses_team_key_anywhere() -> None:
    p = _linear(team_key="ENG")
    assert p.parse_branch_refs("grove/ENG-123-fix-auth") == ["ENG-123"]
    assert p.parse_branch_refs("feature/eng-7-low") == ["ENG-7"]  # case-insensitive → canonical
    assert p.parse_branch_refs("alice/ENG-42") == ["ENG-42"]


def test_linear_team_scoped_ignores_other_keys() -> None:
    p = _linear(team_key="ENG")
    assert p.parse_branch_refs("grove/OPS-9-thing") == []


def test_linear_without_team_key_matches_any_uppercase_key() -> None:
    p = _linear()
    assert p.parse_branch_refs("grove/OPS-9-thing") == ["OPS-9"]


def test_linear_no_false_positive_on_numeric_or_sluggy_branches() -> None:
    p = _linear(team_key="ENG")
    assert p.parse_branch_refs("grove/123-fix") == []
    assert p.parse_branch_refs("grove/fix-v2-bug") == []


def test_linear_format_branch_name() -> None:
    p = _linear(team_key="ENG")
    assert p.format_branch_name("ENG-123", "Fix Auth Callback") == "ENG-123-fix-auth-callback"
    assert p.format_branch_name("ENG-123", None) == "ENG-123-ws"


# ─── Gitea / GitHub: numeric, with leading-segment + keyword forms ────────────


def test_numeric_leading_segment() -> None:
    assert _gitea().parse_branch_refs("grove/123-fix") == ["123"]
    assert _github().parse_branch_refs("123-fix") == ["123"]
    assert _gitea().parse_branch_refs("grove/123") == ["123"]


def test_numeric_no_false_positive_mid_slug() -> None:
    assert _gitea().parse_branch_refs("grove/fix-v2-bug") == []
    assert _github().parse_branch_refs("feature/add-2fa-support") == []


def test_builtin_keyword_prefixes() -> None:
    assert _github().parse_branch_refs("grove/gh-42-thing") == ["42"]
    assert _gitea().parse_branch_refs("grove/gtea-5-x") == ["5"]
    assert _gitea().parse_branch_refs("grove/gitea-9") == ["9"]
    # cross-keyword isolation: a gh- branch is not a gitea ref
    assert _gitea().parse_branch_refs("grove/gh-42-thing") == []


def test_configured_branch_prefix_is_recognized() -> None:
    p = _gitea(branch_prefix="iss-")
    assert p.parse_branch_refs("grove/iss-77-x") == ["77"]
    assert p.format_branch_name("77", "My Task") == "iss-77-my-task"


def test_numeric_format_default_is_bare() -> None:
    assert _github().format_branch_name("42", "Fix Auth") == "42-fix-auth"
    assert _gitea().format_branch_name("5", None) == "5-ws"


def test_dedupes_same_id_from_two_rules() -> None:
    # "123" matches the leading-segment rule once; no duplicate from keywords.
    assert _gitea().parse_branch_refs("grove/123-and-gtea-123") == ["123"]
