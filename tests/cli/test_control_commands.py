"""``grove control`` — triggering a session's controls from a shell.

The group is a thin shell over ``session_controls`` / ``invoke_control`` /
``switch_model``, so these tests pin the SHELL's contract against a real tmp
repo and the FakeTmux seam: which workspace a bare call addresses, that the
trigger reaches the session through the one steer path, and that a refusal
exits non-zero having typed nothing. The engine's per-runtime dispatch is
owned by ``tests/core/test_steering.py`` and the native suites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.phase import PhaseFile
from grove.tui.cli import app
from tests.conftest import FakeTmux


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str) -> str:
    created = runner.invoke(app, ["create", title, "--agent", "claude", "--no-attach"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


def test_run_delivers_the_slash_command_to_the_named_workspace(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    del project
    ws_id = _create(runner, "compact me")

    result = runner.invoke(app, ["control", "run", "compact", "-w", ws_id[:8]])

    assert result.exit_code == 0, result.output
    assert any(text == "/compact" for _, text in fake_tmux.sent_texts)


def test_model_switches_through_the_same_steer_path(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    del project
    ws_id = _create(runner, "switch me")

    result = runner.invoke(app, ["control", "model", "sonnet", "-w", ws_id[:8]])

    assert result.exit_code == 0, result.output
    assert any(text == "/model sonnet" for _, text in fake_tmux.sent_texts)


def test_a_bare_call_addresses_the_callers_OWN_session_not_the_cwd(
    runner: CliRunner,
    project: Path,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent triggering its own compaction names no workspace. The cwd (the
    repo root) is inside neither worktree, so only ``GROVE_PHASE_FILE`` can say
    which session is asking, and it alone must receive the command."""
    stranger = _create(runner, "stranger")
    mine = _create(runner, "mine")
    del stranger
    monkeypatch.setenv(PhaseFile.PATH_ENV, str(PhaseFile.path_for(project, mine)))
    sent_before = len(fake_tmux.sent_texts)

    result = runner.invoke(app, ["control", "run", "compact"])

    assert result.exit_code == 0, result.output
    assert mine in result.output
    assert [text for _, text in fake_tmux.sent_texts[sent_before:]] == ["/compact"]


def test_a_grove_command_on_a_terminal_session_is_refused_and_types_nothing(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``grove:`` command needs per-step completion, which only a native
    session reports; typing its steps into a pane would race them. The refusal
    must surface as an error exit, never as a partial delivery."""
    (project / ".grove").mkdir(exist_ok=True)
    (project / ".grove" / "config.json").write_text(
        json.dumps({"macros": {"compact-fast": {"steps": ["/model flash", "/compact"]}}})
    )
    ws_id = _create(runner, "terminal")
    sent_before = len(fake_tmux.sent_texts)

    result = runner.invoke(app, ["control", "run", "grove:compact-fast", "-w", ws_id[:8]])

    assert result.exit_code == 1
    assert "native Claude Code" in result.output
    assert fake_tmux.sent_texts[sent_before:] == []


def test_an_unknown_grove_command_names_the_configured_ones(
    runner: CliRunner, project: Path
) -> None:
    del project
    ws_id = _create(runner, "unknown")

    result = runner.invoke(app, ["control", "run", "grove:nope", "-w", ws_id[:8]])

    assert result.exit_code == 1
    assert "no Grove command /grove:nope" in result.output


def test_ls_json_is_the_daemon_controls_view(runner: CliRunner, project: Path) -> None:
    """The JSON is ``SessionControlsView``, the shape ``GET /controls`` serves,
    so a script written against either reads the other."""
    del project
    ws_id = _create(runner, "listing")

    result = runner.invoke(app, ["control", "ls", "-w", ws_id[:8], "--json"])

    assert result.exit_code == 0, result.output
    view = json.loads(result.output)
    assert {"commands", "skills", "models", "current_model"} <= view.keys()
