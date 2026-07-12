"""Manager-level ticket association: ticket-aware create, branch inference,
attach/detach, and persistence round-trip — against real git + FakeTmux.

No network: create/attach use only the providers' pure branch parse/format, so
the providers are enabled with no token. Enrichment (list/get) is the daemon's
on-demand job, exercised separately over MockTransport.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import ExistingLocalBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketSelector
from grove.core.errors import (
    TicketProviderNotConfigured,
    WorkspaceNotFound,
    WorkspaceStateError,
)
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "grove/"},
            "tmux": {"session_prefix": "test-"},
            "tickets": {
                "gitea": {"enabled": True, "owner": "bearlike", "repo": "Grove"},
                "github": {"enabled": True, "owner": "o", "repo": "r"},
                "linear": {"enabled": True, "team_key": "ENG"},
            },
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _make_branch(repo: Path, name: str) -> None:
    subprocess.run(["git", "branch", name], cwd=repo, check=True, capture_output=True)


def test_create_with_ticket_generates_ticket_aware_branch(manager: WorkspaceManager) -> None:
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="Fix auth callback",
            ticket=TicketSelector(provider="linear", id="ENG-123"),
        )
    )
    assert state.branch == "grove/ENG-123-fix-auth-callback"
    assert [(r.provider, r.id, r.ambiguous) for r in state.ticket_refs] == [
        ("linear", "ENG-123", False)
    ]


def test_create_with_ticket_for_disabled_provider_raises(tmp_repo: Path, tmp_path: Path) -> None:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees")},
            "tickets": {"gitea": {"enabled": True, "owner": "o", "repo": "r"}},
        }
    )
    mgr = WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "s.json")
    )
    with pytest.raises(TicketProviderNotConfigured):
        mgr.create(
            CreateWorkspaceRequest(
                agent_name="claude",
                title="x",
                ticket=TicketSelector(provider="linear", id="ENG-1"),
            )
        )
    # Failed before any side effect: no workspace persisted.
    assert mgr.list() == []


def test_create_auto_without_ticket_has_no_refs(manager: WorkspaceManager) -> None:
    # auto branch is grove/{slug}-{ts}; the slug "fix-bug" carries no numeric
    # leading segment, so nothing is inferred.
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fix bug"))
    assert state.ticket_refs == []


def test_create_from_existing_branch_infers_refs(manager: WorkspaceManager, tmp_repo: Path) -> None:
    _make_branch(tmp_repo, "ENG-77-existing-work")
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="adopt it",
            branch_plan=ExistingLocalBranch(name="ENG-77-existing-work"),
        )
    )
    assert state.branch == "ENG-77-existing-work"
    assert [(r.provider, r.id) for r in state.ticket_refs] == [("linear", "ENG-77")]


def test_create_from_bare_number_branch_marks_ambiguous(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    _make_branch(tmp_repo, "123-fix")
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="adopt bare",
            branch_plan=ExistingLocalBranch(name="123-fix"),
        )
    )
    assert {(r.provider, r.id) for r in state.ticket_refs} == {
        ("gitea", "123"),
        ("github", "123"),
    }
    assert all(r.ambiguous for r in state.ticket_refs)


def test_attach_and_detach_round_trip_persists(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="plain"))
    assert state.ticket_refs == []

    attached = manager.attach_ticket(state.id, TicketSelector(provider="github", id="42"))
    assert [(r.provider, r.id) for r in attached.ticket_refs] == [("github", "42")]
    # persisted: a fresh read sees it
    assert manager.get(state.id).ticket_refs == attached.ticket_refs

    # idempotent re-attach
    again = manager.attach_ticket(state.id, TicketSelector(provider="github", id="42"))
    assert len(again.ticket_refs) == 1

    detached = manager.detach_ticket(state.id, "github", "42")
    assert detached.ticket_refs == []
    # idempotent detach of a missing ref
    assert manager.detach_ticket(state.id, "github", "42").ticket_refs == []


def test_attach_to_unknown_workspace_raises(manager: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceNotFound):
        manager.attach_ticket("nope", TicketSelector(provider="github", id="1"))


def test_attach_refused_on_orphaned(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="orph"))
    # Make the worktree vanish → reconciles to ORPHANED → attach refused.
    shutil.rmtree(state.worktree_path)
    with pytest.raises(WorkspaceStateError):
        manager.attach_ticket(state.id, TicketSelector(provider="github", id="1"))


# ─── find_by_ticket (#195) ───────────────────────────────────────────────────


def test_find_by_ticket_returns_none_when_no_workspace_tracks_it(
    manager: WorkspaceManager,
) -> None:
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="plain"))
    assert manager.find_by_ticket("github", "999") is None


def test_find_by_ticket_returns_the_tracking_workspace(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fix"))
    manager.attach_ticket(state.id, TicketSelector(provider="github", id="42"))
    found = manager.find_by_ticket("github", "42")
    assert found is not None
    assert found.id == state.id


def test_find_by_ticket_ignores_a_different_provider_with_the_same_id(
    manager: WorkspaceManager,
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fix"))
    manager.attach_ticket(state.id, TicketSelector(provider="github", id="42"))
    assert manager.find_by_ticket("linear", "42") is None


def test_find_by_ticket_newest_created_at_wins_on_multiple_matches(
    manager: WorkspaceManager,
) -> None:
    # Two workspaces can end up tracking the same ticket (hand-attached twice,
    # or a fresh workspace opened for a ticket an older one already tracks) —
    # the newest by created_at is the one issue-ops should steer.
    older = manager.create(CreateWorkspaceRequest(agent_name="claude", title="older"))
    newer = manager.create(CreateWorkspaceRequest(agent_name="claude", title="newer"))
    manager.attach_ticket(older.id, TicketSelector(provider="github", id="7"))
    manager.attach_ticket(newer.id, TicketSelector(provider="github", id="7"))
    found = manager.find_by_ticket("github", "7")
    assert found is not None
    assert found.id == newer.id


def test_find_by_ticket_excludes_a_killed_workspace(manager: WorkspaceManager) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="dead"))
    manager.attach_ticket(state.id, TicketSelector(provider="github", id="5"))
    assert manager.find_by_ticket("github", "5") is not None
    # kill() deletes the persisted record outright (no KILLED status), so the
    # scan over list() excludes it with no separate filter.
    manager.kill(state.id, delete_branch=True)
    assert manager.find_by_ticket("github", "5") is None
