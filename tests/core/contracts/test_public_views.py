"""Unauthenticated public-view contracts fail closed around host-private state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grove.core.contracts.public import (
    PublicActivityView,
    PublicPeekView,
    PublicWorkspaceStateView,
)
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state() -> WorkspaceState:
    now = datetime(2026, 8, 15, tzinfo=UTC)
    return WorkspaceState(
        id="ws-public",
        title="Shared work",
        repo_root="/private/host/checkouts/project-name",
        branch="feature/public",
        base_branch="main",
        worktree_path="/private/host/checkouts/project-name/.grove/worktrees/ws-public",
        tmux_session="grove-shared-work",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
        share_token="a-capability-that-must-not-echo",
        agent_session_id="a-session-that-must-not-echo",
        init_log_path="/private/logs/init.log",
        provision_log_path="/private/logs/provision.log",
        error_detail="private diagnosis",
    )


def test_public_workspace_state_allowlist_excludes_private_fields() -> None:
    # The public view is an explicit allowlist, so new state fields fail closed;
    # this guard fails loudly if someone widens the unauthenticated payload.
    forbidden = {
        "repo_root",
        "worktree_path",
        "tmux_session",
        "container",
        "share_token",
        "agent_session_id",
        "transcript_context",
        "init_log_path",
        "provision_log_path",
        "error_detail",
    }

    assert not forbidden.intersection(PublicWorkspaceStateView.model_fields)


def test_public_peek_allowlist_excludes_terminal_snapshots() -> None:
    forbidden = {"agent_snapshot", "snapshot_taken_at"}

    assert not forbidden.intersection(PublicPeekView.model_fields)


def test_public_activity_allowlist_excludes_workspace_and_pane_coordinates() -> None:
    forbidden = {"state", "pane_target"}

    assert not forbidden.intersection(PublicActivityView.model_fields)


def test_public_workspace_state_uses_only_the_repo_directory_name_for_project() -> None:
    state = _state()

    view = PublicWorkspaceStateView.from_state(state)

    assert view.project == Path(state.repo_root).name
    assert view.project == "project-name"
    assert state.repo_root not in view.model_dump_json()
