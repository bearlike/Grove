"""``grove artifacts attach/list/detach`` against a real repo and the FakeTmux seam.

The one thing the CLI adds over the engine is WHERE a relative path is
relative to: the caller's cwd, not the workspace checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.tui.cli import app
from tests.cli.test_tickets_commands import _create, _worktree_of
from tests.conftest import FakeTmux


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux
) -> Path:
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def test_attach_list_and_detach_resolve_a_path_from_the_callers_cwd(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    ws_id = _create(runner)
    docs = _worktree_of(runner, ws_id) / "docs"
    docs.mkdir()
    (docs / "plan.md").write_text("plan", encoding="utf-8")
    monkeypatch.chdir(docs)

    attached = runner.invoke(
        app, ["artifacts", "attach", "plan.md", "--title", "The plan", "-w", ws_id]
    )
    assert attached.exit_code == 0, attached.output
    assert "The plan" in attached.output and "(file)" in attached.output

    linked = runner.invoke(app, ["artifacts", "attach", "https://example.com/doc", "-w", ws_id])
    assert linked.exit_code == 0, linked.output

    listed = runner.invoke(app, ["artifacts", "list", "-w", ws_id])
    assert "(file)" in listed.output and "(link)" in listed.output

    detached = runner.invoke(app, ["artifacts", "detach", "plan.md", "-w", ws_id])
    assert detached.exit_code == 0, detached.output
    assert "(file)" not in detached.output and "(link)" in detached.output


def test_a_file_outside_the_allowed_directories_exits_nonzero(project: Path) -> None:
    del project
    runner = CliRunner()
    ws_id = _create(runner)
    refused = runner.invoke(app, ["artifacts", "attach", "/etc/hosts", "-w", ws_id])
    assert refused.exit_code != 0
    assert "outside the allowed directories" in refused.output
