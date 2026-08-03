"""``grove phase`` — set/read a workspace's task-phase (``grove.core.phase``).

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux, no real agent. The phase file itself is real disk
I/O (``PhaseFile`` writes into the workspace's worktree), so these tests
exercise the actual ``WorkspaceManager.phase``/``set_phase`` seam end to end,
not a mock.

Covers: the common single-token cwd-inferred form (the agent-in-its-own-
worktree case), the explicit-ref two-token form, the two show spellings
(bare ``grove phase`` and ``grove phase show``), the honest "(none
reported)" default, Click's native choice validation on an invalid phase
word, and the `--note`-without-a-phase guard. ``_emit_phase`` is also
unit-tested directly (no CliRunner), mirroring the ``_emit_runtime_marks``
precedent in ``test_show_command.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core.phase import PHASE_ORDER, PhaseReport
from grove.core.workspace import BranchProvenance, Placement, WorkspaceState, WorkspaceStatus
from grove.tui.cli import app
from grove.tui.cli_workspace import _emit_phase
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


def _create(runner: CliRunner, title: str = "phase me") -> str:
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


def _worktree_of(runner: CliRunner, ws_id: str) -> Path:
    listed = runner.invoke(app, ["ls"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    match = next(r for r in rows if r["id"] == ws_id)
    return Path(match["worktree_path"])


# ─── setting a phase ───────────────────────────────────────────────────────


def test_phase_single_token_sets_cwd_inferred_workspace(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common shape: an agent in its own worktree runs `grove phase <word>`
    with no ref — the workspace is inferred from the cwd."""
    del project
    ws_id = _create(runner)
    worktree = _worktree_of(runner, ws_id)
    monkeypatch.chdir(worktree)

    result = runner.invoke(app, ["phase", "planning"])
    assert result.exit_code == 0, result.output
    assert ws_id in result.output
    assert "phase: planning" in result.output

    # Persisted: a second `grove phase` (show mode) sees it.
    shown = runner.invoke(app, ["phase"])
    assert shown.exit_code == 0, shown.output
    assert "phase: planning" in shown.output


def test_phase_explicit_ref_and_note(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["phase", ws_id[:8], "verifying", "--note", "checking the diff"])
    assert result.exit_code == 0, result.output
    assert "phase: verifying" in result.output
    assert "note:  checking the diff" in result.output


def test_phase_invalid_value_is_a_clean_click_error(runner: CliRunner, project: Path) -> None:
    """Click's own Choice validation on the typed `TaskPhase` argument — the
    vocabulary is derived from PHASE_ORDER, never hand-retyped here."""
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["phase", ws_id[:8], "bogus-phase"])
    assert result.exit_code != 0
    assert "not one of" in result.output
    for phase in PHASE_ORDER:
        assert phase in result.output


def test_phase_note_without_a_phase_is_rejected(runner: CliRunner, project: Path) -> None:
    del project
    _create(runner)

    result = runner.invoke(app, ["phase", "--note", "stray note"])
    assert result.exit_code == 1
    assert "--note only applies when setting a phase" in result.output


# ─── showing a phase ────────────────────────────────────────────────────────


def test_phase_show_defaults_to_none_reported(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    result = runner.invoke(app, ["phase", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert "(none reported)" in result.output


def test_phase_show_alias_matches_bare_command(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del project
    ws_id = _create(runner)
    worktree = _worktree_of(runner, ws_id)
    monkeypatch.chdir(worktree)
    runner.invoke(app, ["phase", "delivering"])

    bare = runner.invoke(app, ["phase"])
    alias = runner.invoke(app, ["phase", "show"])
    assert bare.exit_code == alias.exit_code == 0
    assert "phase: delivering" in bare.output
    assert "phase: delivering" in alias.output


def test_phase_unknown_workspace_is_a_clean_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["phase", "deadbeef"])
    assert result.exit_code == 1
    assert "no workspace matches" in result.output
    assert "Traceback" not in result.output


# ─── _emit_phase renderer (pure, no CliRunner) ─────────────────────────────


def _state(ws_id: str, worktree: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=ws_id,
        title="render me",
        repo_root=str(worktree.parent),
        branch=f"grove/{ws_id}",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        branch_provenance=BranchProvenance.GROVE_CREATED,
        placement=Placement.WORKTREE,
    )


def test_emit_phase_none_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _emit_phase(_state("abc123", tmp_path), None)
    out = capsys.readouterr().out
    assert "abc123  render me" in out
    assert "(none reported)" in out


def test_emit_phase_with_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = PhaseReport(phase="implementing", note="wiring it up", updated_at=datetime.now(UTC))
    _emit_phase(_state("abc123", tmp_path), report)
    out = capsys.readouterr().out
    assert "phase: implementing" in out
    assert "note:  wiring it up" in out
    assert "age:" in out
