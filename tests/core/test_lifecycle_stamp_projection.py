"""A lifecycle stamp reaches the activity projection, so a paused remote can hibernate (#1070).

Written from the incident: on 2026-10-22 a remote workspace was frozen (its
record stamped ``frozen_at``) and never hibernated, because the idle watcher
reads the stamp off the PROJECTION's row and that row still said unfrozen. A
paused remote produces no transcript, tmux or spool edge, so a lifecycle event
is the only thing that can carry the stamp into the projection.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from loguru import logger

from grove.core.activity import ActivityService, DashboardDelta, RefreshDomain
from grove.core.activity_runtime import ActivityRuntime, WorkspaceInvalidated
from grove.core.admission import AdmissionLimits
from grove.core.config import AgentSpec, CloudRunJobRuntime, DaemonConfig, GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.native_owners import WorktreeRead
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.core.test_runtimes_contract import FakeDriver


class Worker:
    """The daemon's steer client as the manager sees it: every worktree command succeeds,
    so the push-before-freeze lands exactly as it did live."""

    def run_in_worktree(
        self, state: WorkspaceState, argv: Sequence[str], timeout: float
    ) -> WorktreeRead:
        return WorktreeRead(0, "")


def _cfg() -> GroveConfig:
    return GroveConfig(
        agents=[AgentSpec(name="claude", command="claude", kind="claude_code", native=True)],
        daemon=DaemonConfig(mailbox_url="https://grove.example.test"),
        runtimes={
            "gcp": CloudRunJobRuntime(project="p", region="r", job="j", image="grove-runtime:1")
        },
    )


@pytest.fixture
def repo(tmp_repo: Path, tmp_state_dir: Path) -> Path:
    del tmp_state_dir
    subprocess.run(
        ["git", "remote", "add", "origin", "https://git.example.test/acme/app.git"],
        cwd=tmp_repo,
        check=True,
    )
    return tmp_repo


async def _settle(service: ActivityService, workspace_id: str, *, frozen: bool) -> object:
    """The projection's row once its frozen stamp matches ``frozen``, or the last one read."""
    row = None
    for _ in range(100):
        snapshot = service.snapshot()
        row = next((w for w in snapshot.iter_workspaces() if w.state.id == workspace_id), None)
        if row is not None and (row.state.frozen_at is not None) == frozen:
            return row
        await asyncio.sleep(0.02)
    return row


@pytest.mark.asyncio
async def test_a_freeze_through_the_manager_reaches_the_projection_row(repo: Path) -> None:
    driver = FakeDriver()
    store = JsonWorkspaceStore(path=repo.parent / "state.json")
    registry = RepoRegistry(cfg=_cfg(), store=store, native_steer=Worker())  # type: ignore[arg-type]
    manager = registry.get(repo)
    manager._runtimes.remote._factory = lambda name, block: driver
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    service = ActivityService(registry=registry)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=8, max_bytes=65536))
    await runtime.start()
    await runtime.wait_ready()
    try:
        before = await _settle(service, state.id, frozen=False)
        assert before is not None and before.state.frozen_at is None  # type: ignore[attr-defined]

        # The idle watcher's own verb, on the registry's own manager.
        await asyncio.to_thread(manager.freeze, state.id)
        assert store.get(state.id).frozen_at is not None

        after = await _settle(service, state.id, frozen=True)
        assert after is not None
        assert after.state.frozen_at is not None, (  # type: ignore[attr-defined]
            "the store is frozen but the projection's row is not"
        )
    finally:
        await runtime.close()


class PausedWorker(Worker):
    """A worker that answers until its workload is paused, then never answers a read.

    That is what a frozen remote's worker did live: each read waited out its
    whole bound on the projection's one thread.
    """

    def __init__(self) -> None:
        self.paused: set[str] = set()
        self.release = threading.Event()

    def run_in_worktree(
        self, state: WorkspaceState, argv: Sequence[str], timeout: float
    ) -> WorktreeRead:
        if state.id in self.paused:
            # Bounded so a regression fails the test instead of hanging the suite.
            self.release.wait(10)
            return WorktreeRead(124, "timed out")
        return WorktreeRead(0, "")


async def _refreshed(service: ActivityService, workspace_id: str, after: datetime) -> bool:
    for _ in range(100):
        row = next(
            (w for w in service.snapshot().iter_workspaces() if w.state.id == workspace_id), None
        )
        if row is not None and row.observed_at > after:
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.mark.asyncio
async def test_a_frozen_remote_does_not_stall_every_other_row(repo: Path) -> None:
    """The #1070 incident: a frozen remote's refresh read through its paused
    worker and held the projection's only thread, so no row on the host
    refreshed for 11 m 39 s. Its row must take the freeze without a worker
    read, and another workspace's row must still refresh."""
    driver = FakeDriver()
    worker = PausedWorker()
    store = JsonWorkspaceStore(path=repo.parent / "state.json")
    registry = RepoRegistry(cfg=_cfg(), store=store, native_steer=worker)  # type: ignore[arg-type]
    manager = registry.get(repo)
    manager._runtimes.remote._factory = lambda name, block: driver
    frozen = manager.create(
        CreateWorkspaceRequest(title="frozen", agent_name="claude", runtime=Runtime.REMOTE)
    )
    other = manager.create(
        CreateWorkspaceRequest(title="other", agent_name="claude", runtime=Runtime.REMOTE)
    )
    service = ActivityService(registry=registry)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=8, max_bytes=65536))
    await runtime.start()
    await runtime.wait_ready()
    try:
        assert await _settle(service, frozen.id, frozen=False) is not None
        # The push before the pause still answers; every read after it does not.
        await asyncio.to_thread(manager.freeze, frozen.id)
        worker.paused.add(frozen.id)

        row = await _settle(service, frozen.id, frozen=True)
        assert row is not None
        assert row.state.frozen_at is not None, (  # type: ignore[attr-defined]
            "the frozen remote's refresh is blocked on its paused worker"
        )

        before = datetime.now(UTC)
        runtime.invalidate(WorkspaceInvalidated(str(repo), other.id, "lifecycle"))
        assert await _refreshed(service, other.id, before), (
            "another workspace's row did not refresh behind the frozen remote"
        )

        # A worktree or phase edge on the frozen row keeps its retained facts too.
        partial = await asyncio.wait_for(
            asyncio.to_thread(
                service.prepare_workspace_refresh,
                str(repo),
                frozen.id,
                domains=RefreshDomain.WORKTREE | RefreshDomain.PHASE,
            ),
            timeout=5,
        )
        assert partial is not None and partial.branch == row.branch  # type: ignore[attr-defined]
    finally:
        worker.release.set()
        await runtime.close()


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    """Every WARNING loguru emits, which is what the daemon's journal keeps."""
    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(m.record["message"]), level="WARNING")
    yield seen
    logger.remove(sink)


def _changed(workspace_id: str, event: str) -> DashboardDelta:
    return DashboardDelta(
        kind="workspace_changed",
        seq=1,
        workspace_id=workspace_id,
        repo_root="/repo",
        detail={"event": event},
    )


@pytest.mark.asyncio
async def test_a_lifecycle_edge_the_inbox_refuses_is_logged_not_dropped(
    tmp_path: Path, warnings: list[str]
) -> None:
    """The live run lost a `frozen` stamp with no trace anywhere. A refused
    lifecycle edge must say so at a level the journal keeps."""
    service = ActivityService(
        registry=RepoRegistry(cfg=GroveConfig(), store=JsonWorkspaceStore(path=tmp_path / "s.json"))
    )
    # One slot, and the worker never runs, so the second edge cannot be admitted.
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=1, max_bytes=65536))
    runtime._inbox.bind()
    runtime._on_delta(_changed("a" * 32, "frozen"))
    runtime._on_delta(_changed("b" * 32, "frozen"))
    assert any("lifecycle edge frozen for " + "b" * 32 in w and "full" in w for w in warnings), (
        warnings
    )
    assert not any("a" * 32 in w for w in warnings)


@pytest.mark.asyncio
async def test_a_refreshed_row_discarded_outside_shutdown_is_logged(
    tmp_path: Path, warnings: list[str]
) -> None:
    """A computed row thrown away while the runtime is not ready loses whatever
    stamp it carried; that must be visible. A shutdown discard stays quiet."""
    service = ActivityService(
        registry=RepoRegistry(cfg=GroveConfig(), store=JsonWorkspaceStore(path=tmp_path / "s.json"))
    )
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=4, max_bytes=65536))
    event = WorkspaceInvalidated("/repo", "c" * 32, "lifecycle", domains=RefreshDomain.FULL)
    runtime._ready = False
    await runtime._refresh(event)
    assert any("refreshed row for " + "c" * 32 + " discarded" in w for w in warnings), warnings

    warnings.clear()
    real_compute = runtime._compute

    async def closing_compute(fn: object) -> object:
        runtime._generation += 1  # close() moved the generation mid-read
        return await real_compute(fn)  # type: ignore[arg-type]

    runtime._compute = closing_compute  # type: ignore[method-assign]
    await runtime._refresh(event)
    assert not any("discarded" in w for w in warnings)
