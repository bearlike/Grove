"""Native catalog watches keep event-owned indexes fresh without a poll."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grove.core.admission import AdmissionLimits
from grove.core.file_events import FileEvent, FileEventBatch, FileEventKind
from grove.core.workspace import TranscriptContext, WorkspaceState, WorkspaceStatus
from grove.daemon._catalog_sources import _CatalogSources


@dataclass
class _Index:
    """Path-local memo double; calls stand in for bounded metadata reads."""

    updates: list[Path]
    recoveries: int = 0
    bootstraps: int = 0
    bootstrap_thread: int | None = None
    # (repo root, worktree, diagrams) as the gallery's own census reports it.
    # Stated here rather than patched onto `GitRepo`, because the watch-root
    # discovery now consumes the gallery's census instead of re-enumerating
    # the worktrees itself — so this IS the boundary these tests stub.
    scopes: list[tuple[Path, Path, list[Path]]] = field(default_factory=list)
    censuses: int = 0

    def rows(self) -> tuple[object, ...]:
        self.bootstraps += 1
        self.bootstrap_thread = threading.get_ident()
        return ()

    def items(self) -> tuple[object, ...]:
        self.bootstraps += 1
        self.bootstrap_thread = threading.get_ident()
        return ()

    def census(self) -> list[tuple[Path, Path, list[Path]]]:
        self.censuses += 1
        return self.scopes

    def update_path(self, path: Path) -> bool:
        self.updates.append(path)
        return True

    def reconcile(self) -> tuple[object, ...]:
        self.recoveries += 1
        return ()


class _Registry:
    def __init__(self, state: WorkspaceState) -> None:
        self._state = state

    def workspace_states(self) -> list[WorkspaceState]:
        return [self._state]

    def known_roots(self) -> list[Path]:
        return [Path(self._state.repo_root)]


class _Source:
    """A deterministic native source boundary; no real watcher is needed here."""

    created: ClassVar[list[_Source]] = []
    fail_at: ClassVar[int | None] = None

    def __init__(
        self, roots: object, callback: Callable[[FileEventBatch], None], **kwargs: Any
    ) -> None:
        self.roots = tuple(roots)
        self.callback = callback
        self.kwargs = kwargs
        self.closed = False
        self.index = len(self.created)
        self.created.append(self)

    async def start(self) -> None:
        if self.fail_at == self.index:
            raise RuntimeError("watcher could not start")

    async def wait_ready(self, timeout: float = 2.0) -> None:
        del timeout

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_sources() -> None:
    _Source.created.clear()
    _Source.fail_at = None


def _state(tmp_path: Path) -> WorkspaceState:
    root = tmp_path / "repo"
    worktree = root / ".worktrees" / "catalog"
    profile = tmp_path / "claude-profile"
    (profile / "projects").mkdir(parents=True)
    worktree.mkdir(parents=True)
    now = datetime(2026, 9, 15, tzinfo=UTC)
    return WorkspaceState(
        id="workspace",
        title="Catalog sources",
        repo_root=str(root),
        branch="catalog-sources",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session="catalog-sources",
        agent_name="claude",
        agent_kind="claude_code",
        agent_session_id="session-id",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        transcript_context=TranscriptContext(
            config_dir=str(profile), agent_cwd="/container/workspace"
        ),
    )


def _owner(
    tmp_path: Path,
    on_changed: Callable[[], None],
    *,
    limits: AdmissionLimits | None = None,
) -> tuple[_CatalogSources, _Index, _Index]:
    state = _state(tmp_path)
    catalog = _Index([])
    gallery = _Index([])
    # The one worktree `_state` materializes, holding no diagrams — the shape
    # the removed `GitRepo` patches used to produce.
    gallery.scopes = [(Path(state.repo_root), Path(state.repo_root) / ".worktrees" / "catalog", [])]
    return (
        _CatalogSources(
            catalog,  # type: ignore[arg-type]
            gallery,  # type: ignore[arg-type]
            _Registry(state),  # type: ignore[arg-type]
            on_changed,
            limits=limits,
            source_factory=_Source,  # type: ignore[arg-type]
        ),
        catalog,
        gallery,
    )


@pytest.mark.asyncio
async def test_start_bootstraps_both_indexes_off_loop_then_updates_paths_in_catalog_order(
    tmp_path: Path,
) -> None:
    changed: list[str] = []
    owner, catalog, gallery = _owner(tmp_path, lambda: changed.append("changed"))
    loop_thread = threading.get_ident()

    await owner.start()
    transcript_source = next(source for source in _Source.created if source.kwargs["recursive"])
    changed_path = tmp_path / "claude-profile" / "projects" / "new-session" / "turn.jsonl"
    transcript_source.callback(
        FileEventBatch((FileEvent(FileEventKind.ADDED, changed_path.resolve()),))
    )
    await _wait_for(lambda: len(changed) == 1)

    assert catalog.bootstraps == 1
    assert gallery.bootstraps == 1
    assert catalog.bootstrap_thread != loop_thread
    assert gallery.bootstrap_thread != loop_thread
    assert catalog.updates == [changed_path.resolve()]
    assert gallery.updates == [changed_path.resolve()]
    assert changed == ["changed"]

    await owner.close()


@pytest.mark.asyncio
async def test_recursive_transcript_watch_covers_new_sessions_rotations_and_deletes(
    tmp_path: Path,
) -> None:
    owner, catalog, gallery = _owner(tmp_path, lambda: None)
    await owner.start()
    transcript_source = next(source for source in _Source.created if source.kwargs["recursive"])
    profile_root = tmp_path / "claude-profile" / "projects"
    added = profile_root / "external" / "new.jsonl"
    rotated = profile_root / "external" / "new.jsonl.1"
    removed = profile_root / "external" / "new.jsonl"

    transcript_source.callback(
        FileEventBatch(
            (
                FileEvent(FileEventKind.ADDED, added.resolve()),
                FileEvent(FileEventKind.MODIFIED, rotated.resolve()),
                FileEvent(FileEventKind.DELETED, removed.resolve()),
            )
        )
    )
    await _wait_for(lambda: len(catalog.updates) == 2)

    assert transcript_source.kwargs["recursive"] is True
    # The inbox coalesces the added/deleted path to its final state; the
    # rotation is a distinct path and must remain independently observable.
    assert catalog.updates == [removed.resolve(), rotated.resolve()]
    assert gallery.updates == catalog.updates

    await owner.close()


@pytest.mark.asyncio
async def test_gallery_scopes_are_nonrecursive_at_workspace_roots(tmp_path: Path) -> None:
    owner, _catalog, _gallery = _owner(tmp_path, lambda: None)

    await owner.start()

    worktree = tmp_path / "repo" / ".worktrees" / "catalog"
    gallery_sources = [
        source
        for source in _Source.created
        if worktree.resolve() in source.roots
        or any(root.is_relative_to(worktree.resolve()) for root in source.roots)
    ]
    assert gallery_sources
    assert all(source.kwargs["recursive"] is False for source in gallery_sources)

    await owner.close()


@pytest.mark.asyncio
async def test_overflow_invalidates_both_indexes_once_and_notifies_once(tmp_path: Path) -> None:
    changed: list[str] = []
    owner, catalog, gallery = _owner(
        tmp_path,
        lambda: changed.append("changed"),
        limits=AdmissionLimits(max_items=1, max_bytes=1),
    )
    await owner.start()
    transcript_source = next(source for source in _Source.created if source.kwargs["recursive"])
    root = tmp_path / "claude-profile" / "projects"
    transcript_source.callback(
        FileEventBatch(
            tuple(
                FileEvent(FileEventKind.MODIFIED, (root / f"session-{index}.jsonl").resolve())
                for index in range(3)
            )
        )
    )
    await _wait_for(lambda: changed.count("changed") == 1)

    # One startup handoff reconciliation plus one overflow recovery.
    assert catalog.recoveries == 2
    assert gallery.recoveries == 2
    assert changed.count("changed") == 1

    await owner.close()


@pytest.mark.asyncio
async def test_start_failure_closes_already_started_sources_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    owner, _catalog, _gallery = _owner(tmp_path, lambda: None)
    _Source.fail_at = 1

    with pytest.raises(RuntimeError, match="watcher could not start"):
        await owner.start()

    assert _Source.created[0].closed is True
    await owner.close()
    await owner.close()
    assert owner._closed is True


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)
