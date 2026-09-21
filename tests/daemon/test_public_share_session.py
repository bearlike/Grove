"""Public-share transcript reads stay bound when ROOT placement shares a discovery cwd."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=daemon_test_config(),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _write_transcript(
    claude_home: Path,
    sid: str,
    cwd: Path,
    *,
    mtime: int,
    prompt: str,
    born_at: datetime | None = None,
) -> Path:
    """``born_at`` is the session's birth (first-record timestamp) — distinct
    from ``mtime``, the file's last-touched time. Most callers don't care and
    take the fixed default; a test exercising the created_at adoption gate
    (`WorkspaceState.adopts_session`) passes one relative to the workspace's
    own ``created_at``."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    born = born_at.isoformat().replace("+00:00", "Z") if born_at else "2026-06-09T08:00:00.000Z"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"{born}",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def _root_workspace(manager: WorkspaceManager, title: str) -> WorkspaceState:
    # ROOT placement deliberately gives every workspace this repo's same transcript cwd.
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title=title, branch_plan=RootBranch())
    )
    assert state.agent_session_id is not None
    return state


def _born_after(*states: WorkspaceState) -> datetime:
    return max(state.created_at for state in states) + timedelta(seconds=1)


def _client(manager: WorkspaceManager) -> TestClient:
    return TestClient(build_app(cfg=daemon_test_config(), store=manager.store))


def test_public_overview_and_turns_name_the_same_session(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    state = _root_workspace(manager, "shared root")
    assert state.agent_session_id is not None
    _write_transcript(
        claude_home,
        state.agent_session_id,
        state.agent_cwd,
        mtime=1_000,
        prompt="my shared transcript",
        born_at=_born_after(state),
    )
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        overview = client.get(f"/public/{shared.share_token}")
        turns = client.get(f"/public/{shared.share_token}/turns")

    assert overview.status_code == 200, overview.text
    assert turns.status_code == 200, turns.text
    assert overview.json()["session_id"] == turns.json()["session"]["session_id"]


def test_a_shared_link_serves_its_own_workspaces_transcript_not_a_newer_neighbours(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    first = _root_workspace(manager, "first root")
    neighbour = _root_workspace(manager, "newer neighbour")
    assert first.agent_session_id is not None
    assert neighbour.agent_session_id is not None
    born_at = _born_after(first, neighbour)
    _write_transcript(
        claude_home,
        first.agent_session_id,
        first.agent_cwd,
        mtime=1_000,
        prompt="first workspace transcript",
        born_at=born_at,
    )
    _write_transcript(
        claude_home,
        neighbour.agent_session_id,
        neighbour.agent_cwd,
        mtime=9_000,
        prompt="neighbour transcript",
        born_at=born_at,
    )
    shared = manager.update(first.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        overview = client.get(f"/public/{shared.share_token}")
        turns = client.get(f"/public/{shared.share_token}/turns")

    assert overview.status_code == 200, overview.text
    assert turns.status_code == 200, turns.text
    assert overview.json()["session_id"] == first.agent_session_id
    assert turns.json()["session"]["session_id"] == first.agent_session_id
    assert turns.json()["session"]["session_id"] != neighbour.agent_session_id


def test_an_unshared_workspaces_transcript_never_reaches_a_public_reader(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    first = _root_workspace(manager, "shared root")
    neighbour = _root_workspace(manager, "private neighbour")
    assert first.agent_session_id is not None
    assert neighbour.agent_session_id is not None
    born_at = _born_after(first, neighbour)
    _write_transcript(
        claude_home,
        first.agent_session_id,
        first.agent_cwd,
        mtime=1_000,
        prompt="shared transcript",
        born_at=born_at,
    )
    _write_transcript(
        claude_home,
        neighbour.agent_session_id,
        neighbour.agent_cwd,
        mtime=9_000,
        prompt="private transcript",
        born_at=born_at,
    )
    shared = manager.update(first.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        overview = client.get(f"/public/{shared.share_token}")
        turns = client.get(f"/public/{shared.share_token}/turns")

    assert overview.status_code == 200, overview.text
    assert turns.status_code == 200, turns.text
    assert overview.json()["session_id"] != neighbour.agent_session_id
    assert neighbour.agent_session_id not in turns.text


def test_the_pinned_session_survives_a_newer_transcript_appearing_later(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    state = _root_workspace(manager, "shared root")
    assert state.agent_session_id is not None
    _write_transcript(
        claude_home,
        state.agent_session_id,
        state.agent_cwd,
        mtime=1_000,
        prompt="pinned transcript",
        born_at=_born_after(state),
    )
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    newer_session_id = "33333333-3333-4333-8333-333333333333"
    _write_transcript(
        claude_home,
        newer_session_id,
        state.agent_cwd,
        mtime=9_000,
        prompt="newly discovered transcript",
        born_at=_born_after(state),
    )

    with _client(manager) as client:
        turns = client.get(f"/public/{shared.share_token}/turns")

    assert turns.status_code == 200, turns.text
    assert turns.json()["session"]["session_id"] == state.agent_session_id


def test_overview_reports_whether_the_session_is_pinned(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    pinned = _root_workspace(manager, "pinned root")
    legacy = _root_workspace(manager, "legacy root")
    assert pinned.agent_session_id is not None
    assert legacy.agent_session_id is not None
    born_at = _born_after(pinned, legacy)
    _write_transcript(
        claude_home,
        pinned.agent_session_id,
        pinned.agent_cwd,
        mtime=1_000,
        prompt="pinned transcript",
        born_at=born_at,
    )
    _write_transcript(
        claude_home,
        legacy.agent_session_id,
        legacy.agent_cwd,
        mtime=2_000,
        prompt="legacy transcript",
        born_at=born_at,
    )
    shared_pinned = manager.update(pinned.id, share=True)
    shared_legacy = manager.update(legacy.id, share=True)
    assert shared_pinned.share_token is not None
    assert shared_legacy.share_token is not None
    legacy_link = replace(shared_legacy, share_session_id=None)
    manager.store.save(legacy_link)

    with _client(manager) as client:
        pinned_overview = client.get(f"/public/{shared_pinned.share_token}")
        legacy_overview = client.get(f"/public/{shared_legacy.share_token}")
        legacy_turns = client.get(f"/public/{shared_legacy.share_token}/turns")

    assert pinned_overview.status_code == 200, pinned_overview.text
    assert legacy_overview.status_code == 200, legacy_overview.text
    assert legacy_turns.status_code == 200, legacy_turns.text
    assert pinned_overview.json()["session_pinned"] is True
    assert legacy_overview.json()["session_pinned"] is False
    assert legacy_overview.json()["session_id"] == legacy.agent_session_id
    assert legacy_turns.json()["session"]["session_id"] == legacy_overview.json()["session_id"]


def _write_tool_transcript(
    claude_home: Path, sid: str, cwd: Path, *, born_at: datetime, prefix: str = "tc"
) -> None:
    """Two turns, each issuing one settled tool call with a real body — enough
    for the public read to withhold one and the public drill-in to serve it.

    ``prefix`` distinguishes one transcript's call ids from another's, which is
    what lets a test ask for a NEIGHBOUR's id through a shared token."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    born = born_at.isoformat().replace("+00:00", "Z")
    lines = ['{"type":"mode","mode":"normal"}']
    for i in range(2):
        lines.append(
            f'{{"type":"user","uuid":"h{i}","timestamp":"{born}","isSidechain":false,'
            f'"cwd":"{cwd}","gitBranch":"main",'
            f'"message":{{"role":"user","content":"turn-{i}"}}}}'
        )
        lines.append(
            f'{{"type":"assistant","uuid":"a{i}","timestamp":"{born}","isSidechain":false,'
            f'"message":{{"id":"m{i}","role":"assistant","stop_reason":"tool_use",'
            f'"content":[{{"type":"tool_use","id":"{prefix}{i}","name":"Bash",'
            f'"input":{{"command":"pytest -q"}}}}]}}}}'
        )
        lines.append(
            f'{{"type":"user","uuid":"r{i}","timestamp":"{born}","isSidechain":false,'
            f'"message":{{"role":"user","content":[{{"type":"tool_result",'
            f'"tool_use_id":"{prefix}{i}","content":"2 passed"}}]}}}}'
        )
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_public_drill_in_serves_a_withheld_body_for_the_tokens_own_session(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """The public read withholds settled bodies exactly as the authenticated one
    does, and the sibling drill-in serves them WITHOUT any session coordinate —
    the token names the workspace, the daemon picks the session."""
    state = _root_workspace(manager, "shared root")
    assert state.agent_session_id is not None
    _write_tool_transcript(
        claude_home, state.agent_session_id, state.agent_cwd, born_at=_born_after(state)
    )
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        turns = client.get(f"/public/{shared.share_token}/turns")
        drill = client.get(f"/public/{shared.share_token}/tools/tc0")

    assert turns.status_code == 200, turns.text
    tools = [e["tool"] for t in turns.json()["turns"] for e in t["entries"] if e.get("tool")]
    assert [t["body"] for t in tools] == ["available", "inline"]
    assert tools[0]["result"] is None
    assert drill.status_code == 200, drill.text
    assert drill.json()["result"] == "2 passed"
    assert drill.json()["body"] == "inline"


def test_a_public_drill_in_for_an_unknown_id_is_the_flat_share_404(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """Every public failure is one code: a caller must not learn whether an id
    exists on some other workspace's transcript."""
    state = _root_workspace(manager, "shared root")
    assert state.agent_session_id is not None
    _write_tool_transcript(
        claude_home, state.agent_session_id, state.agent_cwd, born_at=_born_after(state)
    )
    shared = manager.update(state.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        response = client.get(f"/public/{shared.share_token}/tools/not-a-call")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "share_not_found"


def test_an_unshared_workspaces_tool_body_never_reaches_a_public_reader(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """The drill-in is bound to the token's OWN session by the same resolver
    ``turns`` uses, so a neighbour's call id is unreachable through this link
    even though both transcripts sit in one shared ROOT cwd."""
    first = _root_workspace(manager, "shared root")
    neighbour = _root_workspace(manager, "private neighbour")
    assert first.agent_session_id is not None
    assert neighbour.agent_session_id is not None
    born_at = _born_after(first, neighbour)
    _write_tool_transcript(claude_home, first.agent_session_id, first.agent_cwd, born_at=born_at)
    _write_tool_transcript(
        claude_home,
        neighbour.agent_session_id,
        neighbour.agent_cwd,
        born_at=born_at,
        prefix="nb",
    )
    shared = manager.update(first.id, share=True)
    assert shared.share_token is not None

    with _client(manager) as client:
        own = client.get(f"/public/{shared.share_token}/tools/tc0")
        stranger = client.get(f"/public/{shared.share_token}/tools/nb0")

    # `nb0` is a REAL call, in the same ROOT-placement cwd, reachable only
    # through the neighbour's own (unshared) session — so a 404 here is the
    # binding doing its job rather than the id simply not existing.
    assert own.status_code == 200, own.text
    assert stranger.status_code == 404
    assert stranger.json()["detail"]["error"] == "share_not_found"


def test_the_public_reader_selects_its_session_by_id_not_by_recency(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    first = _root_workspace(manager, "shared root")
    neighbour = _root_workspace(manager, "newer neighbour")
    assert first.agent_session_id is not None
    assert neighbour.agent_session_id is not None
    born_at = _born_after(first, neighbour)
    _write_transcript(
        claude_home,
        first.agent_session_id,
        first.agent_cwd,
        mtime=1_000,
        prompt="shared transcript",
        born_at=born_at,
    )
    _write_transcript(
        claude_home,
        neighbour.agent_session_id,
        neighbour.agent_cwd,
        mtime=9_000,
        prompt="newer neighbour transcript",
        born_at=born_at,
    )
    shared = manager.update(first.id, share=True)
    assert shared.share_token is not None

    newest = SessionExplorer(manager).for_workspace(first.id)[0]
    with _client(manager) as client:
        turns = client.get(f"/public/{shared.share_token}/turns")

    assert turns.status_code == 200, turns.text
    assert newest.summary.session_id == neighbour.agent_session_id
    assert newest.summary.session_id != turns.json()["session"]["session_id"]
    assert turns.json()["session"]["session_id"] == first.agent_session_id
