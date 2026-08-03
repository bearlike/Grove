"""Pydantic Views are wire-shape mirrors of engine dataclasses.

These tests pin the wire contract: every field surfaced on the engine's
public dataclasses round-trips via JSON without loss, and excluded fields
(`init_log_path`, `init_env`) stay off the wire.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.contracts.tickets import TicketRef
from grove.core.contracts.views import (
    ATTACH_INSTRUCTION_ADAPTER,
    CommitSummaryView,
    ProvisionProgressView,
    WorkspacePeekView,
    WorkspaceStateView,
    attach_instruction_view,
)
from grove.core.tmux import ContainerAttach, HostAttach
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    InitStatus,
    Placement,
    ProvisionProgress,
    ProvisionStatus,
    Runtime,
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
        init_log_path="/home/dev/.grove/logs/ws-abc12345-init.log",
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
    # `init_env` is DERIVED on the state since #275 (it was a stored field with
    # no producer and no consumer), and it stays off the wire either way — the
    # four GROVE_* values are already carried by the fields it derives from.
    assert "init_env" not in payload


def test_workspace_state_view_preserves_branch_provenance_default() -> None:
    state = _fake_state()
    view = WorkspaceStateView.from_state(state)
    assert view.branch_provenance == BranchProvenance.GROVE_CREATED


def test_workspace_state_view_placement_defaults_worktree() -> None:
    view = WorkspaceStateView.from_state(_fake_state())
    assert view.placement is Placement.WORKTREE


def test_workspace_state_view_carries_root_placement() -> None:
    state = _fake_state()
    state.placement = Placement.ROOT
    view = WorkspaceStateView.from_state(state)
    assert view.placement is Placement.ROOT
    reloaded = WorkspaceStateView.model_validate_json(view.model_dump_json())
    assert reloaded.placement is Placement.ROOT


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
    view = attach_instruction_view(
        HostAttach(tmux_session="grove-add-login-abc12345", inside_outer_tmux=False)
    )
    reloaded = ATTACH_INSTRUCTION_ADAPTER.validate_json(view.model_dump_json())
    assert reloaded == view
    assert view.kind == "host"


def test_container_attach_view_round_trips_by_discriminator() -> None:
    """A wire client tells the arms apart by ``kind`` alone, with no host fields."""
    argv = ("devcontainer", "exec", "--workspace-folder", "/w", "--", "tmux", "new-session")
    view = attach_instruction_view(ContainerAttach(argv=argv))
    assert view.kind == "container"
    reloaded = ATTACH_INSTRUCTION_ADAPTER.validate_json(view.model_dump_json())
    assert reloaded == view
    assert reloaded.attach_argv() == list(argv)


# ─── ticket_refs on the wire (#7) ────────────────────────────────────────────


def test_workspace_state_view_ticket_refs_default_empty() -> None:
    view = WorkspaceStateView.from_state(_fake_state())
    assert view.ticket_refs == []


def test_workspace_state_view_carries_ticket_refs() -> None:
    state = _fake_state()
    state.ticket_refs = [
        TicketRef(provider="linear", id="ENG-123", title="Add login", ambiguous=False),
        TicketRef(provider="gitea", id="5", ambiguous=True),
    ]
    view = WorkspaceStateView.from_state(state)
    assert [(r.provider, r.id, r.ambiguous) for r in view.ticket_refs] == [
        ("linear", "ENG-123", False),
        ("gitea", "5", True),
    ]
    reloaded = WorkspaceStateView.model_validate_json(view.model_dump_json())
    assert reloaded.ticket_refs == view.ticket_refs


def test_workspace_state_view_carries_the_provisioning_facts() -> None:
    """The three provisioning fields cross together, because a client reading
    one without the others cannot tell an in-flight build from a finished one."""
    state = _fake_state()
    state.runtime = Runtime.CONTAINER
    state.provision_status = ProvisionStatus.PROVISIONING
    state.provision_started_at = "2026-05-07T12:00:00+00:00"

    view = WorkspaceStateView.from_state(state)

    assert view.provision_status is ProvisionStatus.PROVISIONING
    assert view.provision_started_at == datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    assert view.provision_duration_ms is None
    reloaded = WorkspaceStateView.model_validate_json(view.model_dump_json())
    assert reloaded == view


def test_workspace_state_view_excludes_the_provision_log_path() -> None:
    """A host path, exactly like ``init_log_path`` — clients read the tail
    through ``GET /workspaces/{id}/provision`` instead."""
    state = _fake_state()
    state.provision_log_path = "/home/dev/.local/state/grove/logs/ws-abc12345-provision.log"

    assert "provision_log_path" not in WorkspaceStateView.from_state(state).model_dump()


def test_provision_progress_view_round_trip() -> None:
    progress = ProvisionProgress(
        elapsed_ms=49_000,
        headline="grove: applying egress allowlist",
        lines=("[+] Building 0.4s", "grove: applying egress allowlist"),
    )

    view = ProvisionProgressView.from_progress(progress)

    assert view.elapsed_ms == 49_000
    assert view.headline == "grove: applying egress allowlist"
    assert view.lines == list(progress.lines)
    assert ProvisionProgressView.model_validate_json(view.model_dump_json()) == view
