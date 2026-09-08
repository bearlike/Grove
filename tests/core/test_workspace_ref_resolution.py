"""Host-wide workspace-ref resolution: `WorkspaceRef` + `RepoRegistry.resolve_workspace`.

A workspace id is unique across the host and the record names its own repo, so
naming one is enough to reach it. These pin that a caller standing OUTSIDE any
git repo still resolves — the precondition `grove attach <id>` used to inherit
from binding its manager to the cwd's repo instead of to the record's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.errors import GroveError
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceRef, WorkspaceState, WorkspaceStatus


def _state(workspace_id: str, repo_root: Path) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=f"title-{workspace_id}",
        repo_root=str(repo_root.resolve()),
        branch=f"grove/{workspace_id}",
        base_branch="main",
        worktree_path=str(repo_root / ".worktrees" / workspace_id),
        tmux_session=f"grove-{workspace_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


# ─── WorkspaceRef (pure) ─────────────────────────────────────────────────────


def test_exact_id_wins_over_a_longer_id_it_prefixes(tmp_path: Path) -> None:
    """A full id must never be ambiguous against a longer one that starts with
    it — otherwise pasting a complete id from `grove ls` could be refused."""
    states = [_state("abc", tmp_path), _state("abcdef", tmp_path)]
    assert WorkspaceRef.resolve(states, "abc").id == "abc"


def test_unique_prefix_resolves(tmp_path: Path) -> None:
    states = [_state("abc123", tmp_path), _state("zzz999", tmp_path)]
    assert WorkspaceRef.resolve(states, "abc").id == "abc123"


def test_no_match_names_the_ref(tmp_path: Path) -> None:
    with pytest.raises(GroveError, match="no workspace matches 'nope'"):
        WorkspaceRef.resolve([_state("abc", tmp_path)], "nope")


def test_ambiguous_prefix_lists_the_candidates(tmp_path: Path) -> None:
    """The message has to leave the user a ref to extend; a bare refusal only
    moves the problem."""
    states = [_state("abc111", tmp_path), _state("abc222", tmp_path)]
    with pytest.raises(GroveError) as excinfo:
        WorkspaceRef.resolve(states, "abc")
    message = str(excinfo.value)
    assert "ambiguous" in message
    assert "abc111" in message
    assert "abc222" in message


# ─── RepoRegistry.resolve_workspace (store-backed) ───────────────────────────


def test_resolves_from_outside_any_repo_and_binds_to_the_records_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: standing somewhere that is not a git repo at all, a
    workspace id still resolves, and the Manager comes back bound to the repo
    the RECORD names rather than to wherever the caller happened to be."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()

    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("deadbeef", repo))
    registry = RepoRegistry(cfg=GroveConfig(), store=store)

    monkeypatch.chdir(elsewhere)
    manager, state = registry.resolve_workspace("dead")

    assert state.id == "deadbeef"
    assert manager.repo_root == repo.resolve()


def test_ambiguity_is_judged_across_repos(tmp_path: Path) -> None:
    """Host-wide scope means a prefix unique inside one repo can now collide
    with another repo's — which is the honest answer for a ref that no longer
    carries a repo to disambiguate it."""
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for repo in (repo_a, repo_b):
        (repo / ".git").mkdir(parents=True)

    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("ab1", repo_a))
    store.save(_state("ab2", repo_b))
    registry = RepoRegistry(cfg=GroveConfig(), store=store)

    with pytest.raises(GroveError, match="ambiguous"):
        registry.resolve_workspace("ab")


def test_each_repo_resolves_its_own_config_cascade(tmp_path: Path) -> None:
    """A workspace reached from outside its repo must still get that repo's
    config — otherwise a project-scoped agent or init script silently stops
    applying the moment you address the workspace by id from elsewhere."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(_state("cafe", repo))

    seen: list[Path] = []

    def loader(root: Path) -> GroveConfig:
        seen.append(root)
        return GroveConfig()

    registry = RepoRegistry(cfg=GroveConfig(), store=store, config_loader=loader)
    registry.resolve_workspace("cafe")

    assert seen == [repo.resolve()]
