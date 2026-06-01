"""Tests for `grove config schema --stdout`.

The docs-build pipeline pipes this command into a hook that renders the
`configure-reference.md` page.  Pinning that contract here keeps the CLI,
the Makefile, and the CI workflow from drifting.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from grove.tui.cli import app


def test_config_schema_stdout_prints_valid_json_schema() -> None:
    """`grove config schema --stdout` writes the schema to stdout."""
    runner = CliRunner()
    result = runner.invoke(app, ["config", "schema", "--stdout"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    # Every top-level config section appears in the dumped schema.
    for section in ("worktree", "agents", "init_script", "tmux", "ui"):
        assert section in result.stdout, f"expected `{section}` to appear in --stdout dump"


def test_config_schema_stdout_does_not_write_to_disk(tmp_path, monkeypatch) -> None:
    """--stdout must not touch the user config dir.

    Sandbox the schema-path resolver so a stray write would land here and
    we'd catch it. Patches ``grove.core.paths.user_schema_path`` directly
    rather than fiddling with env vars: ``platformdirs`` on Windows
    resolves the user config dir via ``SHGetKnownFolderPath`` (ctypes) and
    ignores ``APPDATA`` / ``LOCALAPPDATA`` entirely (env vars are only
    the fallback when ctypes is unavailable). Patching the function gives
    a deterministic seam on every OS — same pattern as ``tmp_state_dir``.
    """
    schema_target = tmp_path / "config.schema.json"
    monkeypatch.setattr("grove.core.paths.user_schema_path", lambda: schema_target)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "schema", "--stdout"])

    assert result.exit_code == 0, result.output
    assert not schema_target.exists(), "--stdout must not write a schema file"


def test_config_schema_default_still_writes_file(tmp_path, monkeypatch) -> None:
    """Without --stdout, the existing write-to-disk behavior is unchanged.

    Regression guard for users + IDEs that rely on `grove config init` /
    `grove config schema` writing the schema next to the user config.
    Same monkeypatch rationale as the --stdout sibling test above —
    platformdirs ignores env vars on Windows, so we sandbox the function.
    """
    schema_target = tmp_path / "config.schema.json"
    monkeypatch.setattr("grove.core.paths.user_schema_path", lambda: schema_target)

    runner = CliRunner()
    result = runner.invoke(app, ["config", "schema"])

    assert result.exit_code == 0, result.output
    assert schema_target.exists(), (
        f"expected schema at {schema_target}; tmp_path contents: {list(tmp_path.rglob('*'))}"
    )
