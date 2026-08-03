"""``grove shell`` — an interactive shell inside a container.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam, the
same discipline as ``test_code_command.py``. The exec seam is patched (as
``test_attach_resolves_then_execs_tmux`` patches tmux's), so nothing here ever
spawns a ``devcontainer`` — the suite's offline promise covers this door too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import build
from grove.core.container_runtime import ContainerRuntimeState
from grove.tui.cli import app
from tests.conftest import FAKE_REMOTE_FOLDER, FakeTmux


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
    del tmp_state_dir, fake_tmux
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str) -> str:
    result = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert result.exit_code == 0, result.output
    manager = build()
    (state,) = [s for s in manager.list() if s.title == title]
    return state.id


def _containerize(monkeypatch: pytest.MonkeyPatch, *, tmux_command: str) -> None:
    """Stand in for a provisioned container at the CLI's own read seam.

    The create above runs host-side (the suite is offline by fixture), so the
    container identity is injected where the verb reads it rather than by
    driving a provision the offline guarantee forbids.
    """
    container = ContainerRuntimeState(
        container_id="c" * 64,
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels={"grove.workspace": "ws1"},
        provisioned=True,
        tmux_command=tmux_command,
    )
    monkeypatch.setattr("grove.tui.cli_shell._container_target", lambda state: container)


def _exec_seam(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "grove.tui.cli_shell.os.execvp",
        lambda file, args: captured.update(file=file, args=args),
    )
    return captured


def test_shell_refuses_a_host_workspace_and_points_at_attach(
    runner: CliRunner, project: Path
) -> None:
    """A host workspace has no namespace to cross — the ONLY case where
    `grove attach` is the whole answer."""
    del project
    workspace_id = _create(runner, "host only")

    result = runner.invoke(app, ["shell", workspace_id])

    assert result.exit_code == 1
    assert "no container" in result.output
    assert "grove attach" in result.output


def test_shell_execs_into_the_containers_own_persistent_tmux_session(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del project
    workspace_id = _create(runner, "containerized")
    _containerize(monkeypatch, tmux_command="/grove/tmux/bin/amd64/tmux")
    captured = _exec_seam(monkeypatch)

    result = runner.invoke(app, ["shell", workspace_id[:8]])

    assert result.exit_code == 0, result.output
    assert captured["file"] == "devcontainer"
    argv = captured["args"]
    assert isinstance(argv, list)
    assert argv[:2] == ["devcontainer", "exec"]
    # The default config keeps the TERM-fallback retry on, so the entry is the
    # wrapper script — asserted through it rather than around it, because that
    # is what a real user's `grove shell` actually runs.
    entry = argv[argv.index("--") + 1 :]
    assert entry[:2] == ["sh", "-c"]
    assert "new-session -A -s shell" in entry[2]


def test_shell_without_an_in_container_tmux_still_opens_a_shell(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degraded, not refused: no persistence, but a shell all the same."""
    del project
    workspace_id = _create(runner, "no tmux")
    _containerize(monkeypatch, tmux_command="")
    captured = _exec_seam(monkeypatch)

    result = runner.invoke(app, ["shell", workspace_id])

    assert result.exit_code == 0, result.output
    argv = captured["args"]
    assert isinstance(argv, list)
    entry = argv[argv.index("--") + 1 :]
    assert entry[:2] == ["sh", "-c"]
    assert "new-session" not in entry
