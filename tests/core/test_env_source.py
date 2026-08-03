"""The env source: literal dotenv parsing, host resolution, secrets file.

Driven through `ContainerConfig` because that is the section with the
`secrets_file` handoff; the resolver itself serves any `EnvSourceConfig` (the
`tickets` one is exercised in `tests/core/tickets/test_credentials.py`).

Parsing is pure and asserted directly. Resolution is the side-effect edge, so it
is driven for real — a real file on disk, a real `sys.executable` subprocess —
because "the configured command actually runs and its STDOUT becomes the
environment" is the whole feature; a faked `subprocess.run` would pin nothing.

The recurring assertion across the failure cases is a NEGATIVE one: no secret
value appears in any raised message, and none appears in a repr. STDOUT is the
value channel, so it is the channel that must never be quoted back.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from grove.core import paths
from grove.core.config import ContainerConfig
from grove.core.env_source import EnvSource
from grove.core.errors import EnvSourceError

#: Distinctive enough that finding it anywhere is unambiguous, and shaped like
#: the thing `${VAR}` expansion would silently eat.
SECRET = "s3cr3t-$NOT_EXPANDED-${ALSO_NOT}"


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the secrets path off the real state dir (tests/CLAUDE.md).

    Patched at the Grove function, never via an env var — `platformdirs`
    resolves the state dir without consulting the environment on Windows.
    """
    base = tmp_path / "grove-state" / "container-secrets"
    monkeypatch.setattr(
        "grove.core.paths.container_secrets_path",
        lambda workspace_id: base / f"{workspace_id}.json",
    )
    return base


def cfg(**kwargs: str) -> ContainerConfig:
    """A container config carrying only the env knobs under test."""
    return ContainerConfig(**kwargs)


# ─── parsing ────────────────────────────────────────────────────────────────


def test_parse_basic_pairs_blank_lines_and_full_line_comments() -> None:
    parsed = EnvSource.parse(
        "\n".join(
            [
                "# a comment",
                "",
                "   ",
                "FOO=bar",
                "  # indented comment",
                "  BAZ = qux  ",
            ]
        )
    )
    assert parsed == {"FOO": "bar", "BAZ": "qux"}


def test_parse_strips_export_prefix_with_any_whitespace() -> None:
    assert EnvSource.parse("export FOO=bar") == {"FOO": "bar"}
    assert EnvSource.parse("export\tFOO=bar") == {"FOO": "bar"}
    assert EnvSource.parse("export   FOO=bar") == {"FOO": "bar"}
    # Not the keyword — a key that merely starts with it survives intact.
    assert EnvSource.parse("exported=1") == {"exported": "1"}


def test_parse_splits_on_the_first_equals_only() -> None:
    assert EnvSource.parse("DSN=postgres://u:p@h/db?a=1&b=2") == {
        "DSN": "postgres://u:p@h/db?a=1&b=2"
    }
    assert EnvSource.parse("B64=aGVsbG8=") == {"B64": "aGVsbG8="}


def test_parse_never_expands_dollar_references() -> None:
    """The reason this parser is hand-written rather than `python-dotenv`."""
    parsed = EnvSource.parse(
        "\n".join(["HOME_REF=${HOME}", "BARE=$USER", f"TOKEN={SECRET}", 'Q="${HOME}"'])
    )
    assert parsed == {
        "HOME_REF": "${HOME}",
        "BARE": "$USER",
        "TOKEN": SECRET,
        "Q": "${HOME}",
    }


def test_parse_single_quotes_are_literal() -> None:
    parsed = EnvSource.parse("\n".join([r"A='a\nb'", "B='  padded  '", "C='has # hash'"]))
    assert parsed == {"A": r"a\nb", "B": "  padded  ", "C": "has # hash"}


def test_parse_double_quotes_process_exactly_five_escapes() -> None:
    parsed = EnvSource.parse(
        "\n".join(
            [
                r'NL="a\nb"',
                r'TAB="a\tb"',
                r'CR="a\rb"',
                r'BS="a\\b"',
                r'DQ="a\"b"',
                r'UNKNOWN="a\db"',
                r'TRAILING="a\\"',
            ]
        )
    )
    assert parsed == {
        "NL": "a\nb",
        "TAB": "a\tb",
        "CR": "a\rb",
        "BS": "a\\b",
        "DQ": 'a"b',
        # Not in the table: the backslash stays, rather than silently vanishing
        # out of somebody's password.
        "UNKNOWN": r"a\db",
        "TRAILING": "a\\",
    }


def test_parse_quotes_inside_values_and_mixed_quoting() -> None:
    parsed = EnvSource.parse(
        "\n".join(
            [
                """A='he said "hi"'""",
                'B="it\'s fine"',
                "C=unquoted'apostrophe",
                'D="unterminated',
            ]
        )
    )
    assert parsed == {
        "A": 'he said "hi"',
        "B": "it's fine",
        "C": "unquoted'apostrophe",
        # Only a MATCHING pair unquotes; a lone quote is part of the value.
        "D": '"unterminated',
    }


def test_parse_inline_comments_only_in_unquoted_values() -> None:
    parsed = EnvSource.parse(
        "\n".join(
            [
                "A=value # trailing comment",
                "B=value#nospace",
                "C=#hash-first",
                'D="value # kept"',
                "E='value # kept'",
            ]
        )
    )
    assert parsed == {
        "A": "value",
        "B": "value#nospace",
        "C": "#hash-first",
        "D": "value # kept",
        "E": "value # kept",
    }


def test_parse_handles_crlf_and_a_leading_bom() -> None:
    parsed = EnvSource.parse("﻿FOO=bar\r\nBAZ=qux\r\n")
    assert parsed == {"FOO": "bar", "BAZ": "qux"}


def test_parse_ignores_unparseable_lines_without_raising() -> None:
    parsed = EnvSource.parse(
        "\n".join(["not an assignment", "=novalue", "   =  ", "GOOD=yes", "Warning: something"])
    )
    assert parsed == {"GOOD": "yes"}


def test_parse_last_assignment_wins() -> None:
    assert EnvSource.parse("K=first\nK=second\nK=third") == {"K": "third"}


# ─── resolve: neither knob, both knobs ──────────────────────────────────────


def test_resolve_without_either_knob_is_empty(tmp_path: Path) -> None:
    assert EnvSource.resolve(cfg(), repo_root=tmp_path).values == {}


def test_resolve_rejects_both_knobs_set(tmp_path: Path) -> None:
    """The defensive twin of the config validator, so `model_construct` bypasses it.

    `ContainerConfig` refuses the pair at load, so the only way to reach this
    arm is a model built without validation — which is exactly the case the
    check exists for (the `tmux.run_init_script` precedent).
    """
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    unvalidated = ContainerConfig.model_construct(env_file=".env", env_command="true")
    with pytest.raises(EnvSourceError, match="mutually exclusive"):
        EnvSource.resolve(unvalidated, repo_root=tmp_path)


# ─── resolve: env_file ──────────────────────────────────────────────────────


def test_resolve_env_file_relative_to_repo_root(tmp_path: Path) -> None:
    (tmp_path / ".env.grove").write_text(f"TOKEN={SECRET}\n", encoding="utf-8")
    env = EnvSource.resolve(cfg(env_file=".env.grove"), repo_root=tmp_path)
    assert env.values == {"TOKEN": SECRET}


def test_resolve_env_file_absolute_path(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "secrets.env"
    target.parent.mkdir()
    target.write_text("A=1\n", encoding="utf-8")
    env = EnvSource.resolve(cfg(env_file=str(target)), repo_root=tmp_path / "repo")
    assert env.values == {"A": "1"}


def test_resolve_env_file_expands_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "grove.env").write_text("FROM_HOME=1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    env = EnvSource.resolve(cfg(env_file="~/grove.env"), repo_root=tmp_path)
    assert env.values == {"FROM_HOME": "1"}


def test_resolve_missing_env_file_raises_naming_the_path(tmp_path: Path) -> None:
    with pytest.raises(EnvSourceError) as excinfo:
        EnvSource.resolve(cfg(env_file="nope.env"), repo_root=tmp_path)
    message = str(excinfo.value)
    assert str(tmp_path / "nope.env") in message
    assert "configured explicitly" in message


def test_resolve_directory_as_env_file_raises(tmp_path: Path) -> None:
    (tmp_path / "envdir").mkdir()
    with pytest.raises(EnvSourceError):
        EnvSource.resolve(cfg(env_file="envdir"), repo_root=tmp_path)


# ─── resolve: env_command (real subprocesses) ───────────────────────────────


def python_command(body: str) -> str:
    """A real one-off command, quoted the way a user would write it in config."""
    return f'"{sys.executable}" -c "{body}"'


def test_resolve_env_command_runs_a_real_subprocess(tmp_path: Path) -> None:
    command = python_command(f"print('TOKEN={SECRET}'); print('OTHER=2')")
    env = EnvSource.resolve(cfg(env_command=command), repo_root=tmp_path)
    assert env.values == {"TOKEN": SECRET, "OTHER": "2"}


def test_resolve_env_command_runs_in_the_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command = python_command("import os; print('CWD=' + os.getcwd())")
    env = EnvSource.resolve(cfg(env_command=command), repo_root=repo)
    assert Path(env.values["CWD"]).resolve() == repo.resolve()


def script_command(tmp_path: Path, body: str) -> str:
    """A real command whose ARGV carries no secret — the secret is in the script.

    Load-bearing for the leak assertions below: the command string is
    deliberately named in every failure message, so a secret embedded in the
    command itself would make "no value reaches the message" vacuous. This is
    also the realistic shape — users point `env_command` at a script.
    """
    script = tmp_path / "dump-secrets.py"
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def test_resolve_env_command_failure_names_the_exit_code_and_stderr(tmp_path: Path) -> None:
    """Naming the command is required; echoing a RESOLVED value is forbidden.

    Two different strings, two different rules. The command is operator-written
    config and identifies which invocation failed, so the message must carry
    it. The secret here arrives the way a real one does — on the command's
    STDOUT — and must not appear in the message even though the command already
    printed it before dying.
    """
    command = script_command(
        tmp_path,
        f"import sys\nprint('TOKEN={SECRET}')\nsys.stderr.write('vault sealed')\nsys.exit(7)\n",
    )
    with pytest.raises(EnvSourceError) as excinfo:
        EnvSource.resolve(cfg(env_command=command), repo_root=tmp_path)
    message = str(excinfo.value)
    assert "dump-secrets.py" in message
    assert "7" in message
    assert "vault sealed" in message
    # STDOUT is the value channel and never reaches a message, not even the
    # partial output of a command that failed halfway through.
    assert SECRET not in message
    assert "TOKEN" not in message


def test_resolve_env_command_timeout_raises_without_stdout(tmp_path: Path) -> None:
    command = script_command(
        tmp_path, f"import time\nprint('TOKEN={SECRET}', flush=True)\ntime.sleep(30)\n"
    )
    with pytest.raises(EnvSourceError) as excinfo:
        EnvSource.resolve(cfg(env_command=command), repo_root=tmp_path, timeout=1.0)
    message = str(excinfo.value)
    assert "timed out" in message
    # `TimeoutExpired` carries the partial STDOUT it already read; neither the
    # message nor the exception chain may surface it.
    assert SECRET not in message
    assert excinfo.value.__cause__ is None


def test_resolve_env_command_missing_binary_raises(tmp_path: Path) -> None:
    with pytest.raises(EnvSourceError, match="could not be run"):
        EnvSource.resolve(cfg(env_command="grove-no-such-binary-256 --dump"), repo_root=tmp_path)


def test_resolve_env_command_unparseable_quoting_raises(tmp_path: Path) -> None:
    with pytest.raises(EnvSourceError, match="not a parseable command"):
        EnvSource.resolve(cfg(env_command="printf 'unbalanced"), repo_root=tmp_path)


# ─── the secrets file ───────────────────────────────────────────────────────


def test_secrets_file_writes_json_pairs_at_0600(secrets_dir: Path) -> None:
    env = EnvSource({"TOKEN": SECRET, "OTHER": "2"})
    with env.secrets_file("ws-1") as path:
        assert path is not None
        assert json.loads(path.read_text(encoding="utf-8")) == {"TOKEN": SECRET, "OTHER": "2"}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        held = path
    assert not held.exists()


def test_secrets_file_lives_outside_the_repo_and_every_worktree(tmp_path: Path) -> None:
    """Unpatched on purpose: the real helper must not resolve into a checkout."""
    repo = tmp_path / "repo"
    path = paths.container_secrets_path("ws-1")
    assert repo not in path.parents
    assert Path.cwd() not in path.parents
    assert ".worktrees" not in path.parts


def test_secrets_file_is_removed_even_when_the_body_raises(secrets_dir: Path) -> None:
    env = EnvSource({"TOKEN": SECRET})
    seen: Path | None = None
    with pytest.raises(RuntimeError), env.secrets_file("ws-2") as path:
        seen = path
        raise RuntimeError("provision blew up")
    assert seen is not None
    assert not seen.exists()


def test_secrets_file_yields_none_for_an_empty_env(secrets_dir: Path) -> None:
    with EnvSource({}).secrets_file("ws-3") as path:
        assert path is None
    assert not (secrets_dir / "ws-3.json").exists()


def test_secrets_file_overwrites_a_stale_world_readable_leftover(secrets_dir: Path) -> None:
    """A crashed run can leave a file behind; `O_CREAT` alone would keep its mode."""
    stale = secrets_dir / "ws-4.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}", encoding="utf-8")
    stale.chmod(0o644)
    with EnvSource({"A": "1"}).secrets_file("ws-4") as path:
        assert path is not None
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ─── the no-leak invariant ──────────────────────────────────────────────────


def test_repr_shows_the_count_and_never_a_value() -> None:
    env = EnvSource({"TOKEN": SECRET, "OTHER": "2"})
    text = repr(env)
    assert text == "EnvSource(2 vars)"
    assert SECRET not in text
    assert SECRET not in f"{env}"
