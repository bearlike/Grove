"""Public-share capability: token policy, persisted state, and host-wide lookup."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound, WorkspaceStateError
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import ShareToken, WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=GroveConfig(),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )


def _create(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="shared work"))


def _state(
    workspace_id: str,
    repo_root: Path,
    *,
    updated_at: datetime,
    share_token: str | None = None,
) -> WorkspaceState:
    return WorkspaceState(
        id=workspace_id,
        title=workspace_id,
        repo_root=str(repo_root.resolve()),
        branch=f"feature/{workspace_id}",
        base_branch="main",
        worktree_path=str(repo_root / ".grove" / workspace_id),
        tmux_session=f"grove-{workspace_id}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,
        created_at=updated_at,
        updated_at=updated_at,
        share_token=share_token,
    )


@pytest.mark.parametrize(
    ("enabled", "current", "expected"),
    [
        (True, "already-shared", "already-shared"),
        (False, "already-shared", None),
    ],
)
def test_share_token_resolve_reuses_or_revokes_existing_token(
    enabled: bool, current: str, expected: str | None
) -> None:
    assert ShareToken.resolve(enabled=enabled, current=current) == expected


def test_share_token_resolve_mints_for_a_private_workspace() -> None:
    token = ShareToken.resolve(enabled=True, current=None)
    assert token is not None
    assert ShareToken.matches(token, token)


@pytest.mark.parametrize(
    ("candidate", "stored", "expected"),
    [
        ("token", "token", True),
        ("token", None, False),
        ("", "token", False),
        ("wrong", "token", False),
    ],
)
def test_share_token_matches_only_an_exact_nonempty_pair(
    candidate: str, stored: str | None, expected: bool
) -> None:
    assert ShareToken.matches(candidate, stored) is expected


def test_share_update_persists_through_a_fresh_store(manager: WorkspaceManager) -> None:
    state = _create(manager)

    shared = manager.update(state.id, share=True)

    assert shared.share_token is not None
    assert shared.share_expires_at is None
    reloaded = JsonWorkspaceStore(path=manager.store.path).get(state.id)
    assert reloaded.share_token == shared.share_token
    assert reloaded.share_expires_at is None


def test_reenabling_share_is_a_no_op_and_does_not_bump_timestamp(
    manager: WorkspaceManager,
) -> None:
    state = _create(manager)
    shared = manager.update(state.id, share=True)

    again = manager.update(state.id, share=True)

    assert again.share_token == shared.share_token
    assert again.updated_at == shared.updated_at


def test_unsharing_clears_the_token_and_resharing_mints_a_new_one(
    manager: WorkspaceManager,
) -> None:
    state = _create(manager)
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    unshared = manager.update(state.id, share=False)
    reshared = manager.update(state.id, share=True)

    assert unshared.share_token is None
    assert reshared.share_token is not None
    assert reshared.share_token != shared.share_token


def test_minting_a_link_pins_the_workspace_session_it_will_show(
    manager: WorkspaceManager,
) -> None:
    """The pin is captured with the token, not derived when the link is read."""
    state = _create(manager)
    assert state.agent_session_id is not None

    shared = manager.update(state.id, share=True)

    assert shared.share_session_id == state.agent_session_id


def test_revoking_a_link_clears_its_pinned_session(manager: WorkspaceManager) -> None:
    """A pin without a link is a claim about nothing."""
    state = _create(manager)
    manager.update(state.id, share=True)

    unshared = manager.update(state.id, share=False)

    assert unshared.share_token is None
    assert unshared.share_session_id is None


def test_reenabling_share_does_not_move_a_circulated_links_transcript(
    manager: WorkspaceManager,
) -> None:
    """Enabling is idempotent for the pin as well as for the token.

    Clicking "share" on an already-shared workspace must not quietly change
    what a URL somebody is already holding renders — so a re-share stays a
    total no-op even after the workspace's own session has moved on.
    """
    state = _create(manager)
    shared = manager.update(state.id, share=True)
    remapped_id = "99999999-9999-4999-8999-999999999999"
    manager.store.save(_replace_session(manager, state.id, remapped_id))

    again = manager.update(state.id, share=True)

    assert again.share_session_id == shared.share_session_id
    assert again.share_session_id != remapped_id


def test_pinning_an_unknown_session_is_refused_before_anything_is_written(
    manager: WorkspaceManager,
) -> None:
    """A bad ref refuses the whole update rather than half-applying it."""
    state = _create(manager)
    shared = manager.update(state.id, share=True)

    with pytest.raises(AgentSessionNotFound):
        manager.update(state.id, share=True, share_session_id="no-such-session")

    after = manager.store.get(state.id)
    assert after.share_session_id == shared.share_session_id
    assert after.share_token == shared.share_token


def test_pinning_requires_an_enabled_share(manager: WorkspaceManager) -> None:
    state = _create(manager)

    with pytest.raises(WorkspaceStateError):
        manager.update(state.id, share=False, share_session_id="anything")


def test_share_event_reports_the_pin_change_without_naming_the_session(
    manager: WorkspaceManager,
) -> None:
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    shared = manager.update(state.id, share=True)

    event = next(event for event in events if event.kind == "updated")
    assert event.detail["share_session_changed"] == "true"
    assert shared.share_session_id is not None
    assert all(shared.share_session_id not in value for value in event.detail.values())


def test_pinned_session_persists_through_a_fresh_store(
    manager: WorkspaceManager, tmp_path: Path
) -> None:
    state = _create(manager)
    shared = manager.update(state.id, share=True)

    reloaded = JsonWorkspaceStore(path=tmp_path / "state.json").get(state.id)

    assert reloaded.share_session_id == shared.share_session_id


def test_legacy_record_without_a_pinned_session_reads_as_unpinned(tmp_path: Path) -> None:
    """A link issued before pinning existed is un-frozen, never broken.

    ``None`` is the honest state for it, and the reader's documented fallback
    is the workspace's own primary session — so the record must load rather
    than acquire an invented pin.
    """
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {
                    "legacy": {
                        "id": "legacy",
                        "title": "legacy workspace",
                        "repo_root": str(tmp_path / "repo"),
                        "branch": "feature/legacy",
                        "base_branch": "main",
                        "worktree_path": str(tmp_path / "tree"),
                        "tmux_session": "grove-legacy",
                        "agent_name": "claude",
                        "status": "paused",
                        "created_at": "2026-08-15T00:00:00+00:00",
                        "updated_at": "2026-08-15T00:00:00+00:00",
                        "share_token": "a-token-issued-before-pinning-existed",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    legacy = store.get("legacy")
    assert legacy.share_token is not None
    assert legacy.share_session_id is None


def _replace_session(
    manager: WorkspaceManager, workspace_id: str, session_id: str
) -> WorkspaceState:
    """The workspace's own session moves on — a respawn, a remap, a rotation."""
    return dataclasses.replace(manager.store.get(workspace_id), agent_session_id=session_id)


def test_legacy_record_without_share_token_loads_private(tmp_path: Path) -> None:
    """A pre-sharing state file must default safely to a private workspace."""
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {
                    "legacy": {
                        "id": "legacy",
                        "title": "legacy workspace",
                        "repo_root": str(tmp_path / "repo"),
                        "branch": "feature/legacy",
                        "base_branch": "main",
                        "worktree_path": str(tmp_path / "tree"),
                        "tmux_session": "grove-legacy",
                        "agent_name": "claude",
                        "status": "paused",
                        "created_at": "2026-08-15T00:00:00+00:00",
                        "updated_at": "2026-08-15T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert store.get("legacy").share_token is None


def test_rename_preserves_an_existing_share_token(manager: WorkspaceManager) -> None:
    state = _create(manager)
    shared = manager.update(state.id, share=True)

    renamed = manager.update(state.id, title="renamed work")

    assert renamed.share_token == shared.share_token


def test_share_event_reports_only_the_change_flag(manager: WorkspaceManager) -> None:
    state = _create(manager)
    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)

    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    event = next(event for event in events if event.kind == "updated")
    assert event.detail["share_changed"] == "true"
    assert shared.share_token not in event.detail
    assert all(shared.share_token not in value for value in event.detail.values())


def test_registry_resolves_shared_tokens_and_forgets_revoked_ones(
    manager: WorkspaceManager,
) -> None:
    state = _create(manager)
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None
    registry = RepoRegistry(cfg=manager.config, store=manager.store)

    resolved = registry.resolve_share(shared.share_token)

    assert resolved is not None
    resolved_manager, resolved_state = resolved
    assert resolved_manager.repo_root == manager.repo_root
    assert resolved_state.id == state.id
    assert registry.resolve_share("unknown-token") is None
    assert registry.resolve_share("") is None

    resolved_manager.update(state.id, share=False)
    assert registry.resolve_share(shared.share_token) is None


def test_registry_lists_only_shared_workspaces_newest_first(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    now = datetime.now(UTC)
    store.save(_state("old", repo_a, updated_at=now, share_token="old-token"))
    store.save(
        _state(
            "new",
            repo_a,
            updated_at=now + timedelta(seconds=1),
            share_token="new-token",
        )
    )
    store.save(_state("private", repo_a, updated_at=now + timedelta(seconds=2)))
    store.save(
        _state(
            "other-repo",
            repo_b,
            updated_at=now + timedelta(seconds=3),
            share_token="other-token",
        )
    )
    registry = RepoRegistry(cfg=GroveConfig(), store=store)

    shared = registry.shared_in(repo_a)

    assert [state.id for state in shared] == ["new", "old"]
