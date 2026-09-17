"""Activity filesystem sources route bounded paths to persisted workspaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grove.core import paths
from grove.core.activity import RefreshDomain
from grove.core.activity_runtime import WorkspaceInvalidated
from grove.core.activity_sources import ActivitySources
from grove.core.agents.transcript_scope import config_dir_override
from grove.core.file_events import FileEvent, FileEventBatch, FileEventKind
from grove.core.workspace import TranscriptContext, WorkspaceState, WorkspaceStatus


class _Store:
    def __init__(self, path: Path, states: list[WorkspaceState]) -> None:
        self.path = path
        self.states = states
        self.invalidations = 0
        self.load_calls = 0

    def load_all(self) -> list[WorkspaceState]:
        self.load_calls += 1
        return list(self.states)

    def invalidate(self) -> None:
        self.invalidations += 1


class _Runtime:
    def __init__(self) -> None:
        self.invalidations: list[WorkspaceInvalidated] = []
        self.hooks: list[str] = []
        self.recoveries = 0

    def invalidate(self, event: WorkspaceInvalidated) -> None:
        self.invalidations.append(event)

    def hook(self, session_id: str) -> None:
        self.hooks.append(session_id)

    def request_recovery(self) -> None:
        self.recoveries += 1

    async def reconcile(self) -> None:
        self.recoveries += 1


class _Source:
    created: ClassVar[list[_Source]] = []

    def __init__(
        self, roots: object, callback: Callable[[FileEventBatch], None], **kwargs: Any
    ) -> None:
        self.roots = tuple(roots)
        self.callback = callback
        self.kwargs = kwargs
        self.closed = False
        self.created.append(self)

    async def start(self) -> None:
        return None

    async def wait_ready(self, timeout: float = 2.0) -> None:
        del timeout

    async def aclose(self) -> None:
        self.closed = True


class _Adapter:
    def __init__(self, paths: list[Path], expected_config_dir: str | None = None) -> None:
        self.paths = paths
        self.expected_config_dir = expected_config_dir
        self.locates: list[tuple[Path, str]] = []
        self.discoveries: list[Path] = []

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        assert config_dir_override("CLAUDE_CONFIG_DIR") == self.expected_config_dir
        self.locates.append((cwd, session_id))
        return self.paths

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        self.discoveries.append(cwd)
        return []


def _state(tmp_path: Path, **changes: object) -> WorkspaceState:
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    now = datetime(2026, 9, 15, tzinfo=UTC)
    base = WorkspaceState(
        id="workspace",
        title="Activity sources",
        repo_root=str(tmp_path / "repo"),
        branch="activity",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session="activity",
        agent_name="claude",
        agent_kind="claude_code",
        agent_session_id="session-id",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    return replace(base, **changes)


@pytest.fixture(autouse=True)
def _reset_sources() -> None:
    _Source.created.clear()


@pytest.mark.asyncio
async def test_phase_write_invalidates_only_its_workspace_without_rebuilding_watches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    created = len(_Source.created)
    loads = store.load_calls
    phase = tmp_path / "worktree" / ".grove" / "phase" / "workspace.json"
    sources._control_events(FileEventBatch((FileEvent(FileEventKind.MODIFIED, phase),)))

    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.PHASE
        )
    ]
    assert len(_Source.created) == created
    assert store.load_calls == loads

    await sources.close()


@pytest.mark.asyncio
async def test_metadata_state_write_refreshes_only_changed_workspace_without_restarting_groups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    created = len(_Source.created)
    store.states = [
        replace(state, title="Renamed", updated_at=datetime(2026, 9, 15, 1, tzinfo=UTC))
    ]
    sources._control_events(
        FileEventBatch((FileEvent(FileEventKind.MODIFIED, store.path.resolve()),))
    )
    await _wait_for(lambda: len(runtime.invalidations) == 1)

    assert runtime.invalidations == [
        WorkspaceInvalidated(state.repo_root, state.id, reason="lifecycle")
    ]
    assert len(_Source.created) == created

    await sources.close()


@pytest.mark.asyncio
async def test_state_delete_updates_the_source_groups_and_removes_only_deleted_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    initial = tuple(_Source.created)
    store.states = []
    sources._control_events(
        FileEventBatch((FileEvent(FileEventKind.MODIFIED, store.path.resolve()),))
    )
    await _wait_for(lambda: len(runtime.invalidations) == 1)

    assert runtime.invalidations == [
        WorkspaceInvalidated(state.repo_root, state.id, reason="lifecycle", deleted=True)
    ]
    assert any(source.closed for source in initial)

    await sources.close()


@pytest.mark.asyncio
async def test_bootstrap_scopes_persisted_context_and_routes_exact_transcript_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript = tmp_path / "profile" / "sessions" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.touch()
    context = TranscriptContext(config_dir=str(tmp_path / "profile"), agent_cwd="/container/work")
    state = _state(tmp_path, transcript_context=context)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    adapter = _Adapter([transcript], expected_config_dir=context.config_dir)
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    monkeypatch.setattr("grove.core.activity_sources.get_adapter", lambda _: adapter)
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    sources._transcript_events(
        FileEventBatch((FileEvent(FileEventKind.MODIFIED, transcript.resolve()),))
    )

    assert adapter.locates == [
        (Path("/container/work"), "session-id"),
        (Path(state.worktree_path), "session-id"),
    ]
    assert adapter.discoveries == [Path("/container/work"), Path(state.worktree_path)]
    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.TRANSCRIPT
        )
    ]

    await sources.close()


@pytest.mark.asyncio
async def test_agent_exit_control_file_reblends_without_content_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    sources._control_events(
        FileEventBatch(
            (FileEvent(FileEventKind.MODIFIED, paths.agent_exit_path(state.id).resolve()),)
        )
    )

    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.RUNTIME
        )
    ]
    await sources.close()


@pytest.mark.asyncio
async def test_new_sidechain_file_routes_through_its_known_session_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = tmp_path / "profile" / "project" / "session-id.jsonl"
    main.parent.mkdir(parents=True)
    main.touch()
    state = _state(tmp_path)
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    adapter = _Adapter([main])
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    monkeypatch.setattr("grove.core.activity_sources.get_adapter", lambda _: adapter)
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    sidechain = main.parent / "session-id" / "subagents" / "agent-new.jsonl"
    sidechain.parent.mkdir(parents=True)
    sidechain.touch()
    sources._transcript_events(FileEventBatch((FileEvent(FileEventKind.ADDED, sidechain),)))

    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.TRANSCRIPT
        )
    ]
    await sources.close()


@pytest.mark.asyncio
async def test_new_sidechain_file_never_routes_to_an_unrelated_session_sharing_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under a shared project folder (ROOT placement, or several workspaces at
    one cwd), a brand-new sidechain file must be routed by its OWN session's
    subdirectory, never by the shared parent folder — or a fresh sub-agent
    spawned in ONE workspace's session invalidates every workspace whose main
    transcript happens to live in the same folder."""
    shared_folder = tmp_path / "profile" / "project"
    shared_folder.mkdir(parents=True)
    owned = shared_folder / "owned-session.jsonl"
    owned.touch()
    stranger = shared_folder / "stranger-session.jsonl"
    stranger.touch()
    state = _state(tmp_path, id="owner", agent_session_id="owned-session")
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    adapter = _Adapter([owned])
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    monkeypatch.setattr("grove.core.activity_sources.get_adapter", lambda _: adapter)
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    # A sidechain file under the STRANGER session's own subdirectory — this
    # workspace never discovered "stranger-session", so it must never be
    # notified about it, even though both files share `shared_folder`.
    strangers_sidechain = shared_folder / "stranger-session" / "subagents" / "agent-new.jsonl"
    strangers_sidechain.parent.mkdir(parents=True)
    strangers_sidechain.touch()
    sources._transcript_events(
        FileEventBatch((FileEvent(FileEventKind.ADDED, strangers_sidechain),))
    )

    assert runtime.invalidations == []
    await sources.close()


@pytest.mark.asyncio
async def test_git_metadata_event_invalidates_worktree_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    git_dir = tmp_path / "repo" / ".git"
    (git_dir / "refs").mkdir(parents=True)
    (git_dir / "HEAD").touch()
    (git_dir / "index").touch()
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: ())
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.common_dir", lambda *_: git_dir)
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    sources._git_events(FileEventBatch((FileEvent(FileEventKind.MODIFIED, git_dir / "HEAD"),)))

    git_source = next(
        source for source in _Source.created if (git_dir / "refs").resolve() in source.roots
    )
    assert git_source.kwargs["include_ignored"] is True
    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.WORKTREE
        )
    ]
    await sources.close()


@pytest.mark.asyncio
async def test_worktree_watches_tracked_parents_nonrecursively(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _state(tmp_path)
    nested = Path(state.worktree_path) / "src" / "app.py"
    nested.parent.mkdir()
    nested.touch()
    store = _Store(tmp_path / "state.json", [state])
    runtime = _Runtime()
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _Source)
    monkeypatch.setattr(
        "grove.core.activity_sources.GitRepo.list_files", lambda *_: ("src/app.py",)
    )
    sources = ActivitySources(runtime, store)  # type: ignore[arg-type]

    await sources.start()
    sources._worktree_events(FileEventBatch((FileEvent(FileEventKind.MODIFIED, nested.resolve()),)))

    worktree_source = next(
        source for source in _Source.created if nested.parent.resolve() in source.roots
    )
    assert worktree_source.kwargs["recursive"] is False
    assert runtime.invalidations == [
        WorkspaceInvalidated(
            state.repo_root, state.id, reason="filesystem", domains=RefreshDomain.WORKTREE
        )
    ]

    await sources.close()


async def _wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await __import__("asyncio").sleep(0.01)
    raise AssertionError("background activity source task did not settle")
