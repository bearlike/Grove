"""Cross-worktree session aggregation (`SessionExplorer`).

Real tmp git repo + real worktrees (via the manager's create with FakeTmux),
sandboxed Claude config dir with hand-written realistic transcripts. Pins the
aggregation invariants: every worktree scanned, workspace + provenance
annotation, filters, and unique-prefix resolution.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

ROOT_SID = "11111111-1111-4111-8111-111111111111"
TREE_SID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _write_transcript(claude_home: Path, sid: str, cwd: Path, *, mtime: int, prompt: str) -> Path:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def test_lists_sessions_across_root_and_worktrees(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="widget work"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    # The Grove-minted session in the worktree, and a hand-started one at the root.
    _write_transcript(
        claude_home, state.agent_session_id, worktree, mtime=2_000, prompt="fix the widget"
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="root question")

    listings = SessionExplorer(manager).list()

    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id, ROOT_SID]
    minted, hand = listings
    assert minted.provenance == "grove_launched"
    assert minted.workspace_id == state.id
    assert minted.workspace_title == "widget work"
    assert hand.provenance == "fs_discovered"
    assert hand.workspace_id is None  # repo root has no ROOT-placement workspace


def test_paused_workspace_sessions_survive_worktree_removal(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """Transcripts outlive worktrees: a paused workspace's dir is gone from
    `git worktree list`, but its persisted path still gets scanned."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pausable"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    _write_transcript(
        claude_home, state.agent_session_id, worktree, mtime=1_500, prompt="paused work"
    )
    manager.pause(state.id)
    assert not worktree.exists()

    listings = SessionExplorer(manager).list()
    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id]
    assert listings[0].workspace_title == "pausable"


def test_filters_and_limit(manager: WorkspaceManager, claude_home: Path, tmp_repo: Path) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="filterable"))
    assert state.agent_session_id is not None
    _write_transcript(
        claude_home,
        state.agent_session_id,
        Path(state.worktree_path),
        mtime=2_000,
        prompt="in the worktree",
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="at the root")
    explorer = SessionExplorer(manager)

    assert len(explorer.list(agent="claude_code")) == 2
    assert explorer.list(agent="codex") == []
    by_title = explorer.list(workspace="filter")
    assert [ls.summary.session_id for ls in by_title] == [state.agent_session_id]
    assert len(explorer.list(limit=1)) == 1


def test_resolve_prefix_and_ambiguity(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="one")
    _write_transcript(claude_home, TREE_SID, tmp_repo, mtime=2_000, prompt="two")
    explorer = SessionExplorer(manager)

    assert explorer.resolve("1111").summary.session_id == ROOT_SID
    with pytest.raises(GroveError, match="ambiguous"):
        explorer.resolve("")  # empty prefix matches both
    with pytest.raises(GroveError, match="no session"):
        explorer.resolve("dead-beef")


def test_turns_read_through_the_adapter(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="hello there")
    turns = SessionExplorer(manager).turns("1111", last=5)
    assert len(turns) == 1
    assert turns[0].user_text == "hello there"


def test_for_workspace_scopes_to_one_directory(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`for_workspace` is the bounded per-request scan: only the workspace's own
    cwd, newest-first, provenance by minted-id equality — a root-level session
    must not leak in."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="scoped"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    _write_transcript(claude_home, state.agent_session_id, worktree, mtime=2_000, prompt="mine")
    _write_transcript(claude_home, TREE_SID, worktree, mtime=3_000, prompt="hand-started here")
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=4_000, prompt="root noise")

    listings = SessionExplorer(manager).for_workspace(state.id)

    assert [ls.summary.session_id for ls in listings] == [TREE_SID, state.agent_session_id]
    by_id = {ls.summary.session_id: ls for ls in listings}
    assert by_id[state.agent_session_id].provenance == "grove_launched"
    assert by_id[TREE_SID].provenance == "fs_discovered"
    assert all(ls.workspace_id == state.id for ls in listings)


def test_for_workspace_unknown_id_raises(manager: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceNotFound):
        SessionExplorer(manager).for_workspace("deadbeef")


def test_turns_for_matches_turns(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`turns(ref)` is `turns_for(resolve(ref))` — the split must not drift."""
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="hello there")
    explorer = SessionExplorer(manager)
    listing = explorer.resolve("1111")
    assert explorer.turns_for(listing, last=5) == explorer.turns("1111", last=5)
