"""Manual session remap (#120): pin an existing agent session as a workspace's
primary, mirroring the attach_ticket pattern.

Exercises ``WorkspaceManager.remap_session`` against the FakeTmux seam + real
git: prefix resolution through the project's SessionExplorer, idempotency, the
``updated`` event, the no-birth-gate trust class, and the unknown/ambiguous-ref
error mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux  # applied via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _write_transcript(
    claude_home: Path, worktree: Path, session_id: str, *, cwd: Path | None = None
) -> Path:
    """Materialize a claude transcript for ``session_id`` in ``worktree``'s cwd so
    SessionExplorer discovers it."""
    where = cwd or worktree
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(where)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text(f'{{"type":"user","cwd":"{where}"}}\n', encoding="utf-8")
    return path


def test_remap_pins_a_discovered_session_by_full_id(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # A claude workspace so the pinned claude session's kind matches (#F4).
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="host"))
    hand_started = "cafef00d-1111-2222-3333-444455556666"
    _write_transcript(cfg_home, Path(state.worktree_path), hand_started)

    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)
    updated = manager.remap_session(state.id, hand_started)

    assert updated.agent_session_id == hand_started
    assert manager.store.get(state.id).agent_session_id == hand_started
    assert any(
        e.kind == "updated" and e.detail.get("session_remapped") == hand_started for e in events
    )


def test_remap_resolves_a_unique_prefix(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="host"))
    full = "abcd1234-5555-6666-7777-888899990000"
    _write_transcript(cfg_home, Path(state.worktree_path), full)

    updated = manager.remap_session(state.id, "abcd1234")
    assert updated.agent_session_id == full


def test_remap_is_idempotent_when_already_pinned(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="host"))
    sid = "11112222-3333-4444-5555-666677778888"
    _write_transcript(cfg_home, Path(state.worktree_path), sid)
    first = manager.remap_session(state.id, sid)

    events: list[WorkspaceEvent] = []
    manager.subscribe(events.append)
    again = manager.remap_session(state.id, sid)

    assert again.agent_session_id == sid
    assert again.updated_at == first.updated_at  # no-op: no re-persist, no bump
    assert not events  # idempotent: no event on re-pin


def test_remap_unknown_ref_raises_agent_session_not_found(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="host"))
    with pytest.raises(AgentSessionNotFound, match="no session matches"):
        manager.remap_session(state.id, "does-not-exist")


def test_remap_rejects_wrong_kind_session(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#F4: pinning a claude_code session onto a generic (shell) workspace is
    rejected — the workspace's adapter could never read it, so a 200 would leave
    a permanent dead pointer. Names both kinds so the operator sees the mismatch."""
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = manager.create(CreateWorkspaceRequest(agent_name="shell", title="host"))
    claude_session = "beadfeed-1111-2222-3333-444455556666"
    _write_transcript(cfg_home, Path(state.worktree_path), claude_session)

    with pytest.raises(AgentSessionNotFound, match="cannot read a claude_code transcript"):
        manager.remap_session(state.id, claude_session)
