"""grove.cli composes TUI / daemon subcommands via try/except."""

from __future__ import annotations

import subprocess


def test_grove_help_lists_daemon_subcommand() -> None:
    result = subprocess.run(
        ["uv", "run", "grove", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "daemon" in result.stdout.lower(), result.stdout


def test_grove_daemon_serve_help() -> None:
    result = subprocess.run(
        ["uv", "run", "grove", "daemon", "serve", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "host" in result.stdout.lower()
    assert "port" in result.stdout.lower()
