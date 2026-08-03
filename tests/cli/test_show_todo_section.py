"""``grove show``'s todo section — surfaces `WorkspaceManager.latest_todo` as
a compact checklist with counts on the existing read-only inspector.

Two layers, matching ``test_show_command.py``'s split: the renderer
(:meth:`WorkspaceInspection._emit_todo`, via ``emit()``) is unit-tested
directly against a hand-built ``TodoList`` — no CliRunner, no manager; the
CliRunner end-to-end test confirms a freshly created workspace (no session
artifacts on disk, since FakeTmux never runs a real agent) degrades to the
honest "no todo list" note rather than erroring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import WorkspacePeek, WorkspaceState
from grove.core.agents import TodoItem, TodoList
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


def test_show_with_no_agent_session_artifacts_reports_no_todo_list(
    runner: CliRunner, project: Path
) -> None:
    """A freshly created workspace has a minted session id but no transcript
    on disk yet (FakeTmux never runs a real `claude`) — `latest_todo`
    degrades to `None` and `show` renders the honest empty note, not a crash."""
    del project
    created = runner.invoke(app, ["create", "todo me", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    ws_id = next(
        line.split("created ", 1)[1].strip()
        for line in created.output.splitlines()
        if "created " in line
    )

    result = runner.invoke(app, ["show", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "todo" in result.output
    assert "(no todo list)" in result.output


# ─── WorkspaceInspection._emit_todo (pure, hand-built peek + todo) ─────────


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


def test_emit_with_no_todo_renders_absence_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), todo=None)
    inspection.emit()
    out = capsys.readouterr().out
    assert "todo" in out
    assert "(no todo list)" in out


def test_emit_with_todo_renders_counts_and_checklist_glyphs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    todo = TodoList(
        items=(
            TodoItem(content="write the CLI verb", status="completed"),
            TodoItem(
                content="wire the todo section",
                status="in_progress",
                active_form="wiring the todo section",
            ),
            TodoItem(content="run the gate", status="pending"),
        )
    )
    inspection = WorkspaceInspection(peek=_peek(tmp_path), primary=None, turns=(), todo=todo)
    inspection.emit()
    out = capsys.readouterr().out

    assert "1/3 done" in out
    assert "✓ write the CLI verb" in out
    # In-progress items prefer the present-tense `active_form` over `content`.
    assert "▸ wiring the todo section" in out
    assert "☐ run the gate" in out


def test_emit_with_empty_items_tuple_renders_absence_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty-but-present TodoList (e.g. a tool call that cleared the list)
    degrades the same way as `None` — no todo card, no crash on `0/0 done`."""
    inspection = WorkspaceInspection(
        peek=_peek(tmp_path), primary=None, turns=(), todo=TodoList(items=())
    )
    inspection.emit()
    out = capsys.readouterr().out
    assert "(no todo list)" in out
