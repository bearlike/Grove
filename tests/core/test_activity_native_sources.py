"""Native source coverage must survive removal of incidental full refreshes."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core.activity import RefreshDomain
from grove.core.activity_runtime import WorkspaceInvalidated
from grove.core.activity_sources import ActivitySources
from grove.core.workspace import WorkspaceState, WorkspaceStatus


class _Runtime:
    def __init__(self) -> None:
        self.events: list[WorkspaceInvalidated] = []
        self.arrived = asyncio.Event()

    def invalidate(self, event: WorkspaceInvalidated) -> None:
        self.events.append(event)
        self.arrived.set()

    def request_recovery(self) -> None:
        pass


class _Store:
    def __init__(self, path: Path) -> None:
        self.path = path


def _state(root: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id="native-source",
        title="Source",
        repo_root=str(root),
        branch="feature",
        base_branch="main",
        worktree_path=str(root),
        tmux_session="unused",
        agent_name="shell",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
        agent_kind="claude_code",
        agent_session_id="session",
    )


@pytest.mark.asyncio
async def test_new_nested_worker_is_observed_after_watches_are_armed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    main = tmp_path / "projects" / "project" / "session.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text("{}\n")

    class Adapter:
        def locate_transcripts(self, *_args: Any) -> list[Path]:
            return [main]

        def discover_births(self, *_args: Any) -> list[Any]:
            return []

    monkeypatch.setattr("grove.core.activity_sources.get_adapter", lambda _kind: Adapter())
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.list_files", lambda *_: [])
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.common_dir", lambda *_: None)
    monkeypatch.setattr("grove.core.activity_sources.GitRepo.git_dir", lambda *_: None)
    runtime = _Runtime()
    owner = ActivitySources(runtime, _Store(tmp_path / "state.json"))  # type: ignore[arg-type]
    watches = owner._build_sources((_state(root),))
    watch = next(w for w in watches if w._on_event == owner._transcript_events)
    await watch.start()
    try:
        await watch.wait_ready(timeout=3)
        worker = main.parent / "session" / "subagents" / "workflows" / "run" / "agent-one.jsonl"
        worker.parent.mkdir(parents=True)
        worker.write_text("{}\n")
        await asyncio.wait_for(runtime.arrived.wait(), timeout=3)
        assert runtime.events[-1].domains == RefreshDomain.TRANSCRIPT
        assert runtime.events[-1].workspace_id == "native-source"
    finally:
        await watch.aclose()


@pytest.mark.asyncio
async def test_linked_worktree_git_index_and_nested_refs_are_observed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    git("init", "-q", "-b", "main")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-qm",
        "base",
    )
    linked = tmp_path / "linked"
    git("worktree", "add", "-q", "-b", "nested/feature", str(linked))
    runtime = _Runtime()
    owner = ActivitySources(runtime, _Store(tmp_path / "state.json"))  # type: ignore[arg-type]
    roots: set[Path] = set()
    owner._add_git_roots(_state(linked), (str(root), "native-source"), roots)
    from grove.core.file_events import FileEventSource  # noqa: PLC0415

    watch = FileEventSource(roots, owner._git_events, recursive=True, include_ignored=True)
    await watch.start()
    try:
        await watch.wait_ready(timeout=3)
        (linked / "new.txt").write_text("new")
        subprocess.run(["git", "-C", str(linked), "add", "new.txt"], check=True)
        await asyncio.wait_for(runtime.arrived.wait(), timeout=3)
        assert runtime.events[-1].domains == RefreshDomain.WORKTREE
        runtime.events.clear()
        runtime.arrived.clear()
        git("update-ref", "refs/heads/nested/other", "HEAD")
        await asyncio.wait_for(runtime.arrived.wait(), timeout=3)
        assert runtime.events[-1].domains == RefreshDomain.WORKTREE
    finally:
        await watch.aclose()
