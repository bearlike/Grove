"""``grove code`` — open a containerized workspace in VS Code.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam,
same discipline as ``test_workspace_commands.py``. The happy path is
exercised against a duck-typed stand-in for ``WorkspaceState.container``
rather than driving a real containerized create flow, and the error path
pins the actual default behavior: a normal (host) workspace has no
container and `grove code` must refuse it cleanly instead of launching
anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import build
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
    del tmp_state_dir, fake_tmux
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str) -> str:
    result = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert result.exit_code == 0, result.output
    manager = build()
    (state,) = [s for s in manager.list() if s.title == title]
    return state.id


def test_code_refuses_host_workspace(runner: CliRunner, project: Path) -> None:
    workspace_id = _create(runner, "host only")

    result = runner.invoke(app, ["code", workspace_id])

    assert result.exit_code == 1
    assert "no container" in result.output


def test_code_launches_vscode_for_a_containerized_workspace(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = _create(runner, "containerized")

    class _FakeContainer:
        container_id = "grove-abc123"
        remote_workspace_folder = "/workspace"

    # Monkeypatches the read seam itself (`grove.tui.cli_code._container_target`)
    # so this test exercises the launch path without depending on the exact
    # shape of `WorkspaceState.container`.
    monkeypatch.setattr("grove.tui.cli_code._container_target", lambda state: _FakeContainer())
    # Patch the whole consuming class, not `subprocess.Popen` — `subprocess`
    # is one shared module process-wide, and `grove.core.git` (which `build()`
    # calls on every command) shells out through the SAME `subprocess.run`;
    # patching the attribute globally breaks it mid-test (tests/CLAUDE.md).
    calls: list[str] = []

    class _FakeAttach:
        def __init__(self, target: object, **kwargs: object) -> None:
            del kwargs
            self.uri = f"vscode-remote://attached-container+fake{target.container_id}"

        async def start(self) -> None:
            calls.append(self.uri)

    monkeypatch.setattr("grove.tui.cli_code.VsCodeAttach", _FakeAttach)

    result = runner.invoke(app, ["code", workspace_id])

    assert result.exit_code == 0, result.output
    assert "opened" in result.output
    assert len(calls) == 1
    assert "attached-container+" in calls[0]
