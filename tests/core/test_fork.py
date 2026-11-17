"""Forking a conversation: a new workspace launched from a copy of another session.

Pins the launch each provider receives (Claude's `--resume <src> --fork-session`
beside the minted `--session-id`, Codex's `fork <src>`), the refusals that run
before any side effect, and `WorkspaceManager.fork`'s choice of source session.
"""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.native_codex import CodexNativeOwner
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound, ResumeNotSupported
from grove.core.manager import WorkspaceManager
from grove.core.native_worker import NativeWorker, NativeWorkerConfig
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceStatus
from tests.conftest import FakeTmux

pytestmark = pytest.mark.usefixtures("native_roster")

SOURCE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point Claude and Codex at empty config roots under the test's tmpdir."""
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return claude, codex


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            "brief": {"enabled": False},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo, cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


class _Explorer:
    """A session explorer that resolves every ref to one session of ``kind``."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def resolve(self, ref: str) -> object:
        summary = type("Summary", (), {"adapter_kind": self.kind, "session_id": ref})()
        return type("Listing", (), {"summary": summary})()


def _claude_transcript(home: Path, cwd: Path | str, session_id: str) -> None:
    folder = home / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(
        f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8"
    )


def _codex_rollout(home: Path, cwd: Path, session_id: str) -> None:
    folder = home / "sessions" / "2026" / "04" / "28"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"rollout-2026-04-28T13-43-44-{session_id}.jsonl").write_text(
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + session_id + '","cwd":"' + str(cwd) + '"}}\n',
        encoding="utf-8",
    )


def _launch(fake: FakeTmux, session: str) -> list[str]:
    """The last argv Grove appended for ``session``."""
    for name, decoration in reversed(fake.launch_decorations):
        if name == session:
            return decoration
    raise AssertionError(f"no launch recorded for {session}")


def _worker_command(fake: FakeTmux, session: str) -> list[str]:
    """The provider argv a native launch wrote into its worker config."""
    config = json.loads(Path(_launch(fake, session)[1]).read_text(encoding="utf-8"))
    return list(config["command"])


def test_a_fork_needs_a_session_to_copy() -> None:
    with pytest.raises(ValidationError, match="fork_session requires resume_session_id"):
        CreateWorkspaceRequest(fork_session=True)


def test_a_native_claude_fork_mints_its_own_id_and_copies_the_source(
    manager: WorkspaceManager, fake_tmux: FakeTmux, homes: tuple[Path, Path]
) -> None:
    """Native create refuses a plain resume; a fork is a new session, so it launches."""
    _claude_transcript(homes[0], manager.repo_root, SOURCE)
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="fork", resume_session_id=SOURCE, fork_session=True
        )
    )
    assert state.native is True
    assert state.agent_session_id not in (None, SOURCE)
    assert _worker_command(fake_tmux, state.tmux_session)[-5:] == [
        "--session-id",
        state.agent_session_id,
        "--resume",
        SOURCE,
        "--fork-session",
    ]
    config = json.loads(Path(_launch(fake_tmux, state.tmux_session)[1]).read_text())
    # Booted like a fresh session, so the copy is written straight away.
    assert config["resume"] is False


def test_a_terminal_claude_fork_carries_both_ids(
    manager: WorkspaceManager, fake_tmux: FakeTmux, homes: tuple[Path, Path]
) -> None:
    _claude_transcript(homes[0], manager.repo_root, SOURCE)
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude",
            title="fork",
            native=False,
            resume_session_id=SOURCE,
            fork_session=True,
        )
    )
    assert _launch(fake_tmux, state.tmux_session) == [
        "--session-id",
        state.agent_session_id,
        "--resume",
        SOURCE,
        "--fork-session",
    ]


@pytest.mark.parametrize("native", [True, False])
def test_a_codex_fork_rides_the_fork_subcommand(
    manager: WorkspaceManager, fake_tmux: FakeTmux, homes: tuple[Path, Path], native: bool
) -> None:
    """Codex mints the new thread, so Grove pins nothing and discovers it later."""
    _codex_rollout(homes[1], manager.repo_root, SOURCE)
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="codex",
            title="fork",
            native=native,
            resume_session_id=SOURCE,
            fork_session=True,
        )
    )
    assert state.agent_session_id is None
    if native:
        command = _worker_command(fake_tmux, state.tmux_session)
        assert ("fork", SOURCE) in list(pairwise(command))
    else:
        assert _launch(fake_tmux, state.tmux_session) == ["fork", SOURCE]


@pytest.mark.parametrize("native", [True, False])
def test_an_opencode_fork_is_refused_before_any_side_effect(
    manager: WorkspaceManager,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    native: bool,
) -> None:
    """OpenCode's fork keeps the source directory, so Grove does not offer it."""
    # Resolution is not under test: every ref resolves to an opencode session.
    monkeypatch.setattr(manager, "_session_explorer", lambda: _Explorer("opencode"))
    before = set(fake_tmux.sessions)
    with pytest.raises(ResumeNotSupported, match="not implemented for 'opencode'"):
        manager.create(
            CreateWorkspaceRequest(
                agent_name="opencode",
                title="fork",
                native=native,
                resume_session_id=SOURCE,
                fork_session=True,
            )
        )
    assert set(fake_tmux.sessions) == before


def test_the_codex_worker_turns_the_fork_subcommand_into_the_owners_source() -> None:
    """The app server takes no `fork` argv; the owner forks over JSON-RPC instead."""
    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32, provider="codex", command=["codex", "fork", SOURCE, "-m", "x"]
        )
    )
    owner = worker.transport()
    assert isinstance(owner, CodexNativeOwner)
    assert owner.fork_from == SOURCE
    assert owner.command[:3] == ("codex", "-m", "x")


def test_fork_copies_the_source_session_into_its_own_worktree(
    manager: WorkspaceManager, fake_tmux: FakeTmux, homes: tuple[Path, Path]
) -> None:
    source = manager.create(CreateWorkspaceRequest(agent_name="claude", title="origin"))
    assert source.agent_session_id is not None
    _claude_transcript(homes[0], source.agent_cwd, source.agent_session_id)

    forked = manager.fork(source.id)

    assert forked.id != source.id
    assert forked.worktree_path != source.worktree_path
    assert forked.base_branch == source.branch
    assert forked.title == "origin (fork)"
    assert forked.native is True
    assert _worker_command(fake_tmux, forked.tmux_session)[-3:] == [
        "--resume",
        source.agent_session_id,
        "--fork-session",
    ]
    # The source is only read.
    kept = manager.store.get(source.id)
    assert kept.agent_session_id == source.agent_session_id
    assert kept.status is WorkspaceStatus.RUNNING


def test_fork_refuses_a_workspace_with_no_session(manager: WorkspaceManager) -> None:
    source = manager.create(CreateWorkspaceRequest(agent_name="shell", title="plain"))
    with pytest.raises(AgentSessionNotFound, match="no agent session to fork"):
        manager.fork(source.id)


def test_fork_refuses_a_workspace_off_this_host(manager: WorkspaceManager) -> None:
    source = manager.create(CreateWorkspaceRequest(agent_name="claude", title="origin"))
    manager.store.save(replace(source, runtime=Runtime.CONTAINER))
    with pytest.raises(ResumeNotSupported, match="only a host workspace"):
        manager.fork(source.id)
