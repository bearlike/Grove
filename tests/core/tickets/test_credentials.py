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

import shlex
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import sleep

import httpx
import pytest

from grove.core.config import (
    GiteaTicketConfig,
    GitHubTicketConfig,
    LinearTicketConfig,
    TicketsConfig,
)
from grove.core.env_source import EnvSource
from grove.core.errors import EnvSourceError, TicketProviderError
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
    """The init-script case remains automatic: a missing file signature changes
    when the lifecycle writer creates it, and the next source consumption reloads."""
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


def test_a_rotated_file_value_is_seen_without_rebuilding_anything(tmp_path: Path) -> None:
    """File signatures invalidate at consumption, so an atomic file rotation
    replaces the owned snapshot without rebuilding a long-lived registry."""
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


# ─── snapshots: one resolution, explicit command refresh ───────────────────


def test_command_source_is_resolved_once_for_ordinary_mapping_reads(tmp_path: Path) -> None:
    """Capability/mapping reads are source-free once the first snapshot exists."""
    counter = tmp_path / "calls"
    script = (
        "from pathlib import Path; "
        f"path = Path({str(counter)!r}); "
        "path.write_text(str(int(path.read_text() or '0') + 1) if path.exists() else '1'); "
        "print('T_GITEA=from-command')"
    )
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
    )

    assert env["T_GITEA"] == "from-command"
    assert env["T_GITEA"] == "from-command"
    assert len(env) == 1
    assert list(env) == ["T_GITEA"]
    assert counter.read_text() == "1"
    assert env.generation == 1


def test_provider_capabilities_reuse_the_command_snapshot(tmp_path: Path) -> None:
    """`configured` and each capability remain I/O-free after initial resolution."""
    counter = tmp_path / "calls"
    script = (
        "from pathlib import Path; "
        f"path = Path({str(counter)!r}); "
        "path.write_text(str(int(path.read_text() or '0') + 1) if path.exists() else '1'); "
        "print('T_GITEA=from-command')"
    )
    provider = GiteaProvider(
        GiteaTicketConfig(enabled=True, owner="o", repo="r", token_env="T_GITEA"),
        env=TicketEnv(
            _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
        ),
    )

    assert provider.configured is True
    assert provider.can_comment is True
    assert provider.can_edit_body is True
    assert provider.can_assign is True
    assert counter.read_text() == "1"


def test_concurrent_initial_reads_share_one_command_resolution(tmp_path: Path) -> None:
    """A burst cannot fork one subprocess per provider/capability consumer."""
    started = tmp_path / "started"
    release = tmp_path / "release"
    counter = tmp_path / "calls"
    script = (
        "from pathlib import Path\n"
        "import time\n"
        f"started = Path({str(started)!r})\n"
        f"release = Path({str(release)!r})\n"
        f"calls = Path({str(counter)!r})\n"
        "calls.write_text(str(int(calls.read_text() or '0') + 1) if calls.exists() else '1')\n"
        "started.touch()\n"
        "while not release.exists():\n"
        "    time.sleep(0.001)\n"
        "print('T_GITEA=shared')\n"
    )
    env = TicketEnv(
        _tickets(env_command=f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"),
        repo_root=tmp_path,
        base={},
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lambda: env["T_GITEA"]) for _ in range(8)]
        for _ in range(1_000):
            if started.exists():
                break
            sleep(0.001)
        else:
            pytest.fail("command did not start")
        release.touch()
        assert [future.result() for future in futures] == ["shared"] * 8

    assert counter.read_text() == "1"
    assert env.generation == 1


def test_joined_refresh_propagates_its_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh waiter receives the same denied generation, not false success."""
    env = TicketEnv(_tickets(env_command="unused"), repo_root=tmp_path, base={})
    started = Event()
    release = Event()

    def fail_after_release(cls: type[EnvSource], *args: object, **kwargs: object) -> EnvSource:
        del cls, args, kwargs
        started.set()
        assert release.wait(timeout=1)
        raise EnvSourceError("source failed")

    monkeypatch.setattr(EnvSource, "resolve", classmethod(fail_after_release))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(env.refresh)
        assert started.wait(timeout=1)
        joined = pool.submit(env.refresh)
        release.set()
        with pytest.raises(TicketProviderError, match="could not be resolved"):
            first.result()
        with pytest.raises(TicketProviderError, match="could not be resolved"):
            joined.result()


def test_unexpected_resolution_error_releases_waiters_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An untrusted resolver exception becomes a generic denied snapshot."""
    env = TicketEnv(_tickets(env_command="unused"), repo_root=tmp_path, base={})
    started = Event()
    release = Event()
    secret = "resolver-secret"

    def explode_after_release(cls: type[EnvSource], *args: object, **kwargs: object) -> EnvSource:
        del cls, args, kwargs
        started.set()
        assert release.wait(timeout=1)
        raise RuntimeError(secret)

    monkeypatch.setattr(EnvSource, "resolve", classmethod(explode_after_release))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: env["T_GITEA"])
        assert started.wait(timeout=1)
        second = pool.submit(lambda: env["T_GITEA"])
        release.set()
        for future in (first, second):
            with pytest.raises(TicketProviderError) as excinfo:
                future.result()
            assert secret not in str(excinfo.value)
            assert "could not be resolved" in str(excinfo.value)

    assert env.generation == 1


def test_close_forgets_snapshot_and_refuses_later_reads(tmp_path: Path) -> None:
    """Registry shutdown cannot leave a token reachable through its environment."""
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c \"print('T_GITEA=secret')\""),
        repo_root=tmp_path,
        base={},
    )
    assert env["T_GITEA"] == "secret"
    env.close()

    assert "secret" not in repr(env)
    with pytest.raises(TicketProviderError, match="closed"):
        env["T_GITEA"]
    with pytest.raises(TicketProviderError, match="closed"):
        env.refresh()


def test_command_rotation_requires_explicit_refresh(tmp_path: Path) -> None:
    """Commands have no implicit change feed; refresh replaces exactly once."""
    token = tmp_path / "token"
    token.write_text("first")
    counter = tmp_path / "calls"
    script = (
        "from pathlib import Path; "
        f"token = Path({str(token)!r}); calls = Path({str(counter)!r}); "
        "calls.write_text(str(int(calls.read_text() or '0') + 1) if calls.exists() else '1'); "
        "print('T_GITEA=' + token.read_text())"
    )
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
    )

    assert env["T_GITEA"] == "first"
    token.write_text("second")
    assert env["T_GITEA"] == "first"
    assert env.refresh() == 2
    assert env["T_GITEA"] == "second"
    assert counter.read_text() == "2"


def test_invalidate_defers_command_rotation_to_the_next_read(tmp_path: Path) -> None:
    """A rotation owner may separate its event edge from the consuming request."""
    token = tmp_path / "token"
    token.write_text("first")
    script = (
        "from pathlib import Path; "
        f"token = Path({str(token)!r}); "
        "print('T_GITEA=' + token.read_text())"
    )
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
    )

    assert env["T_GITEA"] == "first"
    token.write_text("second")
    env.invalidate()
    assert env.generation == 1
    assert env["T_GITEA"] == "second"
    assert env.generation == 2


def test_failed_refresh_revokes_the_prior_command_snapshot(tmp_path: Path) -> None:
    """A failed refresh must not silently keep authorizing with an old token."""
    mode = tmp_path / "mode"
    mode.write_text("ready")
    script = (
        "from pathlib import Path; import sys; "
        f"mode = Path({str(mode)!r}); "
        "sys.exit(1) if mode.read_text() == 'broken' else print('T_GITEA=first')"
    )
    env = TicketEnv(
        _tickets(env_command=f"{sys.executable} -c {script!r}"), repo_root=tmp_path, base={}
    )

    assert env["T_GITEA"] == "first"
    mode.write_text("broken")
    with pytest.raises(TicketProviderError, match="could not be resolved"):
        env.refresh()
    with pytest.raises(TicketProviderError, match="could not be resolved"):
        env["T_GITEA"]
    assert env.generation == 2


def test_source_instances_never_share_a_repository_snapshot(tmp_path: Path) -> None:
    """Two repository contexts with identical config resolve only their own file."""
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "tickets.env").write_text("T_GITEA=one")
    (second_root / "tickets.env").write_text("T_GITEA=two")
    cfg = _tickets(env_file="tickets.env")

    first = TicketEnv(cfg, repo_root=first_root, base={})
    second = TicketEnv(cfg, repo_root=second_root, base={})
    assert first["T_GITEA"] == "one"
    assert second["T_GITEA"] == "two"
    assert first.generation == second.generation == 1


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
