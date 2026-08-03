"""Ticket credentials resolve at the moment of USE, from the section's env source.

The bug these pin: a provider used to read its token in ``__init__`` and freeze
both the auth header and ``configured`` there, while the registry holding it is
cached on a ``WorkspaceManager`` a daemon keeps for the whole process — so a
credential produced *after* that moment (the case the whole feature exists for:
a workspace init script writing a dotenv, a secret manager, a login) could never
be seen. Every test below therefore constructs FIRST and makes the credential
available SECOND.

Resolution runs for real — a real file on disk, a real ``sys.executable``
subprocess — because "the configured source is actually read at the right time"
is the feature; a faked resolver would pin nothing. Only the HTTP boundary is
faked, so the composed ``Authorization`` header is asserted on the wire.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from grove.core.config import (
    GiteaTicketConfig,
    GitHubTicketConfig,
    LinearTicketConfig,
    TicketsConfig,
)
from grove.core.errors import TicketProviderError
from grove.core.tickets import TicketProviderRegistry
from grove.core.tickets.credentials import TicketEnv
from grove.core.tickets.gitea import GiteaProvider
from grove.core.tickets.github import GitHubProvider
from grove.core.tickets.linear import LinearProvider

TOKEN = "s3cr3t-$NOT_EXPANDED"

#: One Gitea issue, enough for `get_ticket` to normalize.
ISSUE = {"number": 7, "title": "Late credential", "state": "open"}


def _tickets(**kwargs: object) -> TicketsConfig:
    """A tickets config with Gitea enabled and whatever env source is under test."""
    return TicketsConfig.model_validate(
        {
            "gitea": {"enabled": True, "owner": "o", "repo": "r", "token_env": "T_GITEA"},
            **kwargs,
        }
    )


def _capturing_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ISSUE)

    return httpx.MockTransport(handler)


# ─── the headline: a credential that appears AFTER construction ─────────────


def test_env_file_written_after_construction_is_picked_up(tmp_path: Path) -> None:
    """The init-script case, end to end: build the registry while the file the
    config names holds no token yet, THEN have the script write one, and the
    provider is live — no restart, no rebuilt registry, nothing invalidated."""
    target = tmp_path / ".grove" / "tickets.env"
    target.parent.mkdir(parents=True)
    target.write_text("# written by .grove/init.sh\n", encoding="utf-8")

    cfg = _tickets(env_file=".grove/tickets.env")
    provider = TicketProviderRegistry(cfg, repo_root=tmp_path, env=None).get("gitea")
    assert provider.configured is False

    target.write_text(f"T_GITEA={TOKEN}\n", encoding="utf-8")
    assert provider.configured is True


def test_a_request_after_the_file_appears_carries_the_new_token(tmp_path: Path) -> None:
    """`configured` flipping is not enough — the HEADER must be composed from the
    late value too, which the old baked-at-construction header could never do."""
    target = tmp_path / "tickets.env"
    seen: list[httpx.Request] = []
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T_GITEA"),
        env=TicketEnv(_tickets(env_file="tickets.env"), repo_root=tmp_path),
        transport=_capturing_transport(seen),
    )
    # A configured-but-missing file is a hard error, never a silent empty env.
    with pytest.raises(TicketProviderError, match="could not be resolved"):
        provider.get_ticket("7")

    target.write_text(f"T_GITEA={TOKEN}\n", encoding="utf-8")
    assert provider.get_ticket("7").id == "7"
    assert seen[-1].headers["Authorization"] == f"token {TOKEN}"


def test_a_rotated_value_is_seen_without_rebuilding_anything(tmp_path: Path) -> None:
    """The other half of "never cached": the second request must not replay the
    first resolution, or a daemon serves a token the store already rotated."""
    target = tmp_path / "tickets.env"
    target.write_text("T_GITEA=first\n", encoding="utf-8")
    seen: list[httpx.Request] = []
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T_GITEA"),
        env=TicketEnv(_tickets(env_file="tickets.env"), repo_root=tmp_path),
        transport=_capturing_transport(seen),
    )
    provider.get_ticket("7")
    target.write_text("T_GITEA=second\n", encoding="utf-8")
    provider.get_ticket("7")
    assert [r.headers["Authorization"] for r in seen] == ["token first", "token second"]


def test_process_environment_set_after_construction_is_picked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plain `token_env` path is lazy too — `os.environ` is held live, not
    copied, so the default (no env source configured) benefits from the same fix."""
    monkeypatch.delenv("T_GITEA", raising=False)
    provider = TicketProviderRegistry(_tickets(), env=None).get("gitea")
    assert provider.configured is False
    monkeypatch.setenv("T_GITEA", TOKEN)
    assert provider.configured is True


# ─── resolution order ───────────────────────────────────────────────────────


def test_the_configured_source_wins_over_the_ambient_environment(tmp_path: Path) -> None:
    """Order step 1 beats step 2: an operator pointing Grove at a file is saying
    "read it from here", and a value baked into the daemon's env at exec must not
    shadow it — that stale value is precisely what cannot be updated."""
    (tmp_path / "tickets.env").write_text("T_GITEA=from-file\n", encoding="utf-8")
    env = TicketEnv(
        _tickets(env_file="tickets.env"), repo_root=tmp_path, base={"T_GITEA": "from-process"}
    )
    assert env["T_GITEA"] == "from-file"


def test_the_ambient_environment_answers_what_the_source_does_not(tmp_path: Path) -> None:
    """Fallback, not replacement: one file holding the Gitea token leaves a
    GitHub token exported in the shell perfectly usable."""
    (tmp_path / "tickets.env").write_text("T_GITEA=from-file\n", encoding="utf-8")
    env = TicketEnv(
        _tickets(env_file="tickets.env"), repo_root=tmp_path, base={"T_GITHUB": "from-process"}
    )
    assert env["T_GITHUB"] == "from-process"
    assert env.get("T_MISSING") is None


def test_no_source_configured_reads_only_the_process_environment(tmp_path: Path) -> None:
    """The default path costs no file read and no subprocess."""
    env = TicketEnv(_tickets(), repo_root=tmp_path, base={"T_GITEA": "x"})
    assert env["T_GITEA"] == "x"


def test_env_command_stdout_is_parsed_as_dotenv(tmp_path: Path) -> None:
    """The materialize-nothing path — a command that prints dotenv to stdout,
    run for real rather than faked."""
    script = f"import sys; sys.stdout.write('T_GITEA={TOKEN}\\n')"
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
    )
    assert env["T_GITEA"] == TOKEN


# ─── failure shapes: a render path degrades, a request raises ───────────────


def test_a_missing_env_file_reads_as_unconfigured_rather_than_raising(tmp_path: Path) -> None:
    """`configured` feeds the picker's gray-out flag and the daemon's
    skip-unconfigured aggregation, so one broken tracker must not fail the whole
    request. The reason is logged; the request path below still raises."""
    provider = TicketProviderRegistry(
        _tickets(env_file="absent.env"), repo_root=tmp_path, env=None
    ).get("gitea")
    assert provider.configured is False


def test_a_broken_source_raises_the_tickets_error_on_a_real_call(tmp_path: Path) -> None:
    """Narrowed at the package boundary: the resolver's own `EnvSourceError`
    never escapes as itself, and no unauthenticated request is sent."""
    seen: list[httpx.Request] = []
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T_GITEA"),
        env=TicketEnv(_tickets(env_file="absent.env"), repo_root=tmp_path),
        transport=_capturing_transport(seen),
    )
    with pytest.raises(TicketProviderError, match="could not be resolved"):
        provider.get_ticket("7")
    assert seen == []


def test_an_absent_token_names_both_ways_to_supply_one(tmp_path: Path) -> None:
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T_GITEA"),
        env=TicketEnv(_tickets(), repo_root=tmp_path, base={}),
    )
    with pytest.raises(TicketProviderError) as excinfo:
        provider.get_ticket("7")
    message = str(excinfo.value)
    assert "T_GITEA" in message
    assert "env_file" in message


# ─── the secret never leaks ─────────────────────────────────────────────────


def test_repr_carries_neither_values_nor_the_base_mapping(tmp_path: Path) -> None:
    """A default dataclass/mapping repr would render the whole process
    environment through any incidental f-string or traceback frame dump."""
    (tmp_path / "tickets.env").write_text(f"T_GITEA={TOKEN}\n", encoding="utf-8")
    env = TicketEnv(
        _tickets(env_file="tickets.env"), repo_root=tmp_path, base={"OTHER": "also-secret"}
    )
    assert TOKEN not in repr(env)
    assert "also-secret" not in repr(env)


def test_auth_header_schemes_are_provider_shape(tmp_path: Path) -> None:
    """Each tracker spells the same credential differently — composed per request
    now, so the scheme lives with the provider rather than in a frozen header."""
    del tmp_path
    gitea = GiteaProvider(GiteaTicketConfig(), env={})
    github = GitHubProvider(GitHubTicketConfig(), env={})
    linear = LinearProvider(LinearTicketConfig(), env={})
    assert gitea.auth_headers("t") == {"Authorization": "token t"}
    assert github.auth_headers("t") == {"Authorization": "Bearer t"}
    assert linear.auth_headers("t") == {"Authorization": "t"}
