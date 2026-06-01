"""Pydantic Views are wire-shape mirrors of engine dataclasses.

These tests pin the wire contract: every field surfaced on the engine's
public dataclasses round-trips via JSON without loss, and excluded fields
(`init_log_path`, `init_env`) stay off the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.contracts.views import (
    AttachInstructionView,
    CommitSummaryView,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.tmux import AttachInstruction
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    InitStatus,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)


def _fake_state() -> WorkspaceState:
    return WorkspaceState(
        id="ws-abc12345",
        title="Add login flow",
        repo_root="/repos/myproj",
        branch="feat/add-login-20260507",
        base_branch="main",
        worktree_path="/repos/myproj/.grove/worktrees/ws-abc12345",
        tmux_session="grove-add-login-abc12345",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 7, 12, 5, tzinfo=UTC),
        init_status=InitStatus.OK,
        init_duration_ms=4321,
        init_log_path="/home/kk/.grove/logs/ws-abc12345-init.log",
        init_env={"PATH": "/usr/bin"},
    )


def test_workspace_state_view_round_trip() -> None:
    state = _fake_state()
    view = WorkspaceStateView.from_state(state)
    blob = view.model_dump_json()
    reloaded = WorkspaceStateView.model_validate_json(blob)
    assert reloaded == view


def test_workspace_state_view_excludes_internal_fields() -> None:
    view = WorkspaceStateView.from_state(_fake_state())
    payload = view.model_dump()
    assert "init_log_path" not in payload
    assert "init_env" not in payload


def test_workspace_state_view_preserves_branch_provenance_default() -> None:
    state = _fake_state()
    view = WorkspaceStateView.from_state(state)
    assert view.branch_provenance == BranchProvenance.GROVE_CREATED


def test_workspace_state_view_description_default_none() -> None:
    state = _fake_state()
    assert state.description is None
    view = WorkspaceStateView.from_state(state)
    assert view.description is None


def test_workspace_state_view_round_trips_description() -> None:
    state = _fake_state()
    state.description = "see ticket #1234"
    view = WorkspaceStateView.from_state(state)
    reloaded = WorkspaceStateView.model_validate_json(view.model_dump_json())
    assert reloaded.description == "see ticket #1234"


def test_commit_summary_view_round_trip() -> None:
    cs = CommitSummary(
        sha="abc12345",
        subject="feat: add thing",
        committed_at=datetime(2026, 5, 7, 11, 0, tzinfo=UTC),
    )
    view = CommitSummaryView.from_summary(cs)
    reloaded = CommitSummaryView.model_validate_json(view.model_dump_json())
    assert reloaded == view


def test_workspace_peek_view_round_trip() -> None:
    peek = WorkspacePeek(
        state=_fake_state(),
        base_ahead=2,
        base_behind=0,
        diff_added=10,
        diff_removed=3,
        dirty_files=1,
        recent_commits=(
            CommitSummary(
                sha="deadbeef",
                subject="initial",
                committed_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ),
        agent_snapshot="$ ls\nfoo bar\n",
        snapshot_taken_at=datetime(2026, 5, 7, 12, 5, tzinfo=UTC),
    )
    view = WorkspacePeekView.from_peek(peek)
    reloaded = WorkspacePeekView.model_validate_json(view.model_dump_json())
    assert reloaded == view
    assert reloaded.state.id == "ws-abc12345"
    assert len(reloaded.recent_commits) == 1


def test_attach_instruction_view_round_trip() -> None:
    ai = AttachInstruction(tmux_session="grove-add-login-abc12345", inside_outer_tmux=False)
    view = AttachInstructionView.from_instruction(ai)
    reloaded = AttachInstructionView.model_validate_json(view.model_dump_json())
    assert reloaded == view
