"""Operation-count regressions for the maintained activity projection."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, ClassVar

import pytest

from grove.core.activity import ActivityService, DashboardDelta
from grove.core.activity_runtime import ActivityRuntime, WorkspaceInvalidated
from grove.core.activity_sources import ActivitySources
from grove.core.admission import Admission, AdmissionLimits
from grove.core.config import GroveConfig
from grove.core.file_events import FileEventRecovery, FileEventRecoveryReason
from grove.core.git import GitRepo
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def _state(root: Path, workspace_id: str) -> WorkspaceState:
    now = datetime.now(UTC)
    worktree = root / workspace_id
    worktree.mkdir(parents=True, exist_ok=True)
    return WorkspaceState(
        id=workspace_id,
        title=workspace_id,
        repo_root=str(root),
        branch="main",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session=workspace_id,
        agent_name="shell",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
    )


def _service(tmp_path: Path, count: int) -> tuple[ActivityService, JsonWorkspaceStore, str]:
    root = tmp_path / "repo"
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    for index in range(count):
        store.save(_state(root, f"workspace-{index}"))
    registry = RepoRegistry(cfg=GroveConfig(), store=store)
    return ActivityService(registry=registry), store, str(root)


def _git_counter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    block: Event | None = None,
    release: Event | None = None,
) -> dict[str, int]:
    calls = dict.fromkeys(
        ("current_branch", "ahead_behind", "diff_stats", "dirty_file_count", "recent_commits"), 0
    )

    def current_branch(repo: GitRepo) -> str:
        calls["current_branch"] += 1
        if block is not None and release is not None and repo._root.name == "workspace-0":
            block.set()
            assert release.wait(2)
        return "main"

    def counted(name: str, result: object) -> Callable[..., object]:
        def call(*_args: object, **_kwargs: object) -> object:
            calls[name] += 1
            return result

        return call

    monkeypatch.setattr(GitRepo, "current_branch", current_branch)
    monkeypatch.setattr(GitRepo, "ahead_behind", counted("ahead_behind", (0, 0)))
    monkeypatch.setattr(GitRepo, "diff_stats", counted("diff_stats", (0, 0)))
    monkeypatch.setattr(GitRepo, "dirty_file_count", counted("dirty_file_count", 0))
    monkeypatch.setattr(GitRepo, "recent_commits", counted("recent_commits", ()))
    return calls


@pytest.mark.parametrize("fleet_size", [1, 24])
def test_projection_reads_are_free_after_one_bootstrap_and_refresh_is_keyed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fleet_size: int
) -> None:
    """Read volume cannot multiply git work; one invalidation cannot scan peers."""
    service, _store, root = _service(tmp_path, fleet_size)
    calls = _git_counter(monkeypatch)

    service.bootstrap()
    calls.update(dict.fromkeys(calls, 0))
    for _ in range(100):
        assert service.snapshot().total_workspaces == fleet_size
        service.bootstrap()
    assert calls == dict.fromkeys(calls, 0)

    service.refresh_workspace(root, "workspace-0")
    assert calls == dict.fromkeys(calls, 1)


@pytest.mark.asyncio
async def test_burst_retains_one_trailing_keyed_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An in-flight refresh plus a burst costs at most one trailing git read."""
    service, _store, root = _service(tmp_path, 2)
    entered, release = Event(), Event()
    release.set()  # Bootstrap is not the blocked refresh under test.
    calls = _git_counter(monkeypatch, block=entered, release=release)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=2, max_bytes=4096))
    await runtime.start()
    await runtime.wait_ready()
    # `start` BOOTSTRAPS, which reads workspace-0 — so it already tripped
    # `entered` and consumed `release`. Clearing the counters without clearing
    # the events left `_wait_for(entered.is_set)` satisfied by the BOOTSTRAP's
    # flag, so the burst below was offered before the refresh under test had
    # blocked and coalesced into the one in-flight read rather than leaving a
    # trailing one. The test then measured 1 call and asserted 2, and the
    # failure read as a timeout rather than as the stale flag it was.
    entered.clear()
    release.clear()
    calls.update(dict.fromkeys(calls, 0))
    try:
        assert runtime.hook("missing") is Admission.ACCEPTED  # unmapped hooks do no I/O
        assert calls == dict.fromkeys(calls, 0)
        assert (
            runtime.invalidate(WorkspaceInvalidated(root, "workspace-0", "filesystem"))
            is Admission.ACCEPTED
        )
        await _wait_for(entered.is_set)
        outcomes = [
            runtime.invalidate(WorkspaceInvalidated(root, "workspace-0", "filesystem"))
            for _ in range(100)
        ]
        assert set(outcomes) <= {Admission.ACCEPTED, Admission.COALESCED}
        release.set()
        # The first git call proves the trailing refresh STARTED, not that its
        # other reads or publication completed. Wait on released reservations.
        await _wait_for(lambda: runtime._inbox.stats().items == 0)
        assert calls["current_branch"] == 2
        assert all(calls[name] == 2 for name in calls)
    finally:
        release.set()
        await runtime.close()


class _ReadySource:
    created: ClassVar[list[_ReadySource]] = []

    def __init__(self, roots: object, callback: Callable[..., object], **kwargs: Any) -> None:
        self.roots, self.callback, self.kwargs = tuple(roots), callback, kwargs
        self.started = self.ready = self.closed = False
        self.created.append(self)

    async def start(self) -> None:
        self.started = True

    async def wait_ready(self, timeout: float = 2.0) -> None:
        del timeout
        self.ready = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_source_readiness_recovers_once_and_runtime_close_unsubscribes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A source readiness edge and one recovery preserve the single projection owner."""
    service, store, _root = _service(tmp_path, 1)
    prepares = 0
    prepare = service.prepare_reconcile

    def counted_prepare() -> object:
        nonlocal prepares
        prepares += 1
        return prepare()

    monkeypatch.setattr(service, "prepare_reconcile", counted_prepare)
    monkeypatch.setattr("grove.core.activity_sources.FileEventSource", _ReadySource)
    monkeypatch.setattr(GitRepo, "list_files", lambda *_: ())
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=2, max_bytes=4096))
    sources = ActivitySources(runtime, store)
    await runtime.start()
    # The projection bootstrap runs off the readiness path, so this test — which
    # COUNTS reconciles — has to let it land before the source edges add theirs.
    await runtime.wait_ready()
    await sources.start()
    try:
        assert _ReadySource.created
        assert all(source.started and source.ready for source in _ReadySource.created)
        _ReadySource.created[0].kwargs["on_recovery"](
            FileEventRecovery(FileEventRecoveryReason.WATCHER_FAILURE, 0)
        )
        await _wait_for(lambda: prepares == 3)
        assert prepares == 3  # runtime bootstrap, ready sources, one recovery
    finally:
        await sources.close()
        await runtime.close()
    assert runtime.hook("any") is Admission.NOT_READY


def test_failed_consumer_isolated_and_unsubscribe_is_idempotent(tmp_path: Path) -> None:
    service, _store, root = _service(tmp_path, 1)
    received: list[DashboardDelta] = []
    service.subscribe(lambda _delta: (_ for _ in ()).throw(RuntimeError("consumer failed")))
    unsubscribe = service.subscribe(received.append)

    service.source_changed(root, "workspace-0")
    unsubscribe()
    unsubscribe()
    service.source_changed(root, "workspace-0")

    assert [delta.kind for delta in received] == ["workspace_source_changed"]
    service.close()


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)
