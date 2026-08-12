"""``grove show``'s phase line — the third status axis on the CLI's own
inspector.

A surface-parity gap, not a new capability: `grove phase` already read and wrote
the axis, but `show` — the verb that prints a workspace's lifecycle status AND
its live agent state — omitted the one status an orchestrator explicitly SETS,
so the surface that reports a workspace's status did not report the status you
had just given it.

Two layers, mirroring ``test_show_todo_section.py``: the renderer is unit-tested
against a hand-built ``PhaseReport``, then a CliRunner run confirms a workspace
whose agent never reported degrades to the honest "(none reported)" note rather
than a blank that would read as "scoping".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import WorkspacePeek, WorkspaceState
from grove.core.phase import PhaseReport
from grove.core.workspace import BranchProvenance, CommitSummary, Placement, WorkspaceStatus
from grove.tui.cli import app
from grove.tui.cli_workspace import WorkspaceInspection
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


def _peek(tmp_path: Path) -> WorkspacePeek:
    now = datetime.now(UTC)
    return WorkspacePeek(
        state=WorkspaceState(
            id="abc123",
            title="render me",
            repo_root=str(tmp_path),
            branch="grove/render-me",
            base_branch="main",
            worktree_path=str(tmp_path / "wt"),
            tmux_session="grove-abc123",
            agent_name="claude",
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
            branch_provenance=BranchProvenance.GROVE_CREATED,
            placement=Placement.WORKTREE,
        ),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(CommitSummary(sha="deadbee0", subject="init", committed_at=now),),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )


def test_emit_renders_the_position_but_not_the_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 1-based position rides along because the axis is ORDERED — "3 of 6"
    is information a bare word does not carry. The NOTE deliberately does not:
    the identity block is aligned one-line facts, and `grove phase` is the
    read-deeply surface that prints it (the same split the TUI row card makes
    against the peek rail)."""
    report = PhaseReport(
        phase="implementing",
        note="wiring the CLI verb",
        updated_at=datetime.now(UTC),
    )
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), phase=report)
    inspection.emit()
    out = capsys.readouterr().out

    assert "implementing" in out
    assert "(3/6)" in out
    assert "wiring the CLI verb" not in out


def test_emit_renders_the_blocked_flag_beside_the_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`show`'s phase line reuses `_phase_summary`, so a blocked claim reads
    exactly like `grove phase`'s own render — the flag rides beside the
    phase, never replacing it."""
    report = PhaseReport(
        phase="implementing",
        note="wiring the CLI verb",
        updated_at=datetime.now(UTC),
        blocked=True,
    )
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), phase=report)
    inspection.emit()
    out = capsys.readouterr().out

    assert "implementing" in out
    assert "(blocked)" in out


def test_emit_with_no_phase_says_none_reported_not_blank(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Has not reported" and "is scoping" are different facts (``PhaseFile.read``
    draws the same line); a blank value would collapse them into the second."""
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), phase=None)
    inspection.emit()
    out = capsys.readouterr().out
    assert "phase:" in out
    assert "(none reported)" in out


def test_emit_phase_line_sits_in_the_identity_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Grouped with the lifecycle ``status`` it is an axis of, and ahead of the
    ``agent``/``todo`` sections — one fact about the workspace, not a section."""
    report = PhaseReport(phase="verifying", note=None, updated_at=datetime.now(UTC))
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), phase=report)
    inspection.emit()
    out = capsys.readouterr().out

    assert "verifying" in out
    assert "(4/6)" in out
    lines = out.splitlines()
    status_at = next(i for i, line in enumerate(lines) if line.startswith("  status:"))
    phase_at = next(i for i, line in enumerate(lines) if line.startswith("  phase:"))
    agent_at = next(i for i, line in enumerate(lines) if line.strip() == "agent")
    assert status_at < phase_at < agent_at


def test_show_on_a_workspace_that_never_reported_degrades_cleanly(
    runner: CliRunner, project: Path
) -> None:
    """End to end: a fresh workspace has no ``.grove/phase.json`` at all, and
    the inspector must render the absence rather than fail the command — the
    same best-effort contract every other section of `show` holds."""
    del project
    created = runner.invoke(app, ["create", "phase me", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    ws_id = next(
        line.split("created ", 1)[1].strip()
        for line in created.output.splitlines()
        if "created " in line
    )

    result = runner.invoke(app, ["show", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "phase:" in result.output
    assert "(none reported)" in result.output


def test_show_reports_a_phase_the_cli_just_set(runner: CliRunner, project: Path) -> None:
    """The parity claim itself: set the phase through the write surface an
    orchestrator uses, then read it back from the inspector."""
    del project
    created = runner.invoke(app, ["create", "phase me", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    ws_id = next(
        line.split("created ", 1)[1].strip()
        for line in created.output.splitlines()
        if "created " in line
    )

    written = runner.invoke(app, ["phase", ws_id[:8], "delivering"])
    assert written.exit_code == 0, written.output

    result = runner.invoke(app, ["show", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "delivering" in result.output
    assert "(none reported)" not in result.output
