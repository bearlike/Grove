"""``grove fleet`` — the scriptable, host-wide read of every workspace's
lifecycle status, agent activity, task phase, and ticket refs.

In-process via CliRunner against real tmp git repos and the FakeTmux seam —
no daemon, no real tmux, no network. ``fleet`` reuses
``ActivityService.snapshot()`` in-process (never HTTP) and serializes it
through the exact same ``DashboardSnapshotView`` the daemon's ``GET
/activity`` route and the MCP ``grove_get_fleet_status`` tool already emit —
these tests pin that shape (round-tripping the CLI's JSON back through the
real Pydantic view) and the host-wide scope (unlike ``grove ls``, which is
repo-scoped).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.contracts.activity import DashboardSnapshotView
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
    """cwd inside a real repo, Grove state sandboxed, tmux/git side effects faked."""
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str = "watch me") -> str:
    """Create a workspace via the CLI and return its id."""
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


def _init_repo(path: Path) -> Path:
    """A second, unrelated real git repo — proves host-wide scope, mirroring
    the identical helper in ``tests/cli/test_sessions_commands.py``."""
    import subprocess  # noqa: PLC0415 — test-local helper, keeps the fixture list short

    path.mkdir(parents=True)
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@grove.local"],
        ["git", "config", "user.name", "Grove Test"],
    ):
        subprocess.run(args, cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def test_fleet_matches_the_dashboard_snapshot_wire_shape(runner: CliRunner, project: Path) -> None:
    """The output round-trips through the exact same ``DashboardSnapshotView``
    the daemon and the MCP tool emit — the "describe a fleet identically"
    claim, pinned structurally rather than by hand-picked fields."""
    ws_id = _create(runner)

    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0, result.output

    view = DashboardSnapshotView.model_validate_json(result.output)
    ids = {w.state.id for group in view.projects for w in group.workspaces}
    assert ws_id in ids


def test_fleet_carries_lifecycle_status_and_phase(runner: CliRunner, project: Path) -> None:
    """The two axes ``grove ls`` cannot show: the lifecycle status ``ls``
    already carries, plus the phase claim only ``grove phase``/``show`` could
    read before this verb existed."""
    ws_id = _create(runner)
    phased = runner.invoke(app, ["phase", ws_id, "implementing", "--note", "wiring fleet"])
    assert phased.exit_code == 0, phased.output

    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0, result.output
    view = DashboardSnapshotView.model_validate_json(result.output)

    row = next(w for group in view.projects for w in group.workspaces if w.state.id == ws_id)
    # RUNNING is the persisted intent; a live fake tmux session with recent
    # activity reconciles to the computed ACTIVE status — same axis `grove ls`
    # already reports, just read through the richer view here.
    assert row.state.status.value == "active"
    assert row.phase is not None
    assert row.phase.phase == "implementing"
    assert row.phase.note == "wiring fleet"
    # ticket_refs rides on the embedded WorkspaceStateView — present (empty),
    # never dropped, so a script can always read the field.
    assert row.state.ticket_refs == []


def test_fleet_is_host_wide_unlike_ls(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, project: Path, tmp_path: Path
) -> None:
    """``grove ls`` only ever lists the cwd's own repo; ``grove fleet`` must see
    every repo the host knows about — the whole point of the new verb over
    just widening ``ls``."""
    repo_a = project
    repo_b = _init_repo(tmp_path / "repo-b")

    id_a = _create(runner, "in repo a")

    monkeypatch.chdir(repo_b)
    id_b = _create(runner, "in repo b")

    # `ls` stays repo-scoped: from repo b it sees only repo b's workspace.
    scoped = runner.invoke(app, ["ls"])
    assert scoped.exit_code == 0, scoped.output
    assert id_b in scoped.output
    assert id_a not in scoped.output

    # `fleet`, from the same cwd, sees both.
    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0, result.output
    view = DashboardSnapshotView.model_validate_json(result.output)
    ids = {w.state.id for group in view.projects for w in group.workspaces}
    assert {id_a, id_b} <= ids

    roots = {group.repo_root for group in view.projects}
    assert str(repo_a.resolve()) in roots
    assert str(repo_b.resolve()) in roots


def test_fleet_totals_reflect_the_fleet(runner: CliRunner, project: Path) -> None:
    """``total_workspaces``/``needs_attention`` are computed by the same
    ``DashboardSnapshot`` properties every other client reads — pin that a
    fresh, non-attention-needing workspace counts toward the first and not
    the second."""
    _create(runner)

    result = runner.invoke(app, ["fleet"])
    assert result.exit_code == 0, result.output
    view = DashboardSnapshotView.model_validate_json(result.output)

    assert view.total_workspaces >= 1
    assert view.needs_attention >= 0
