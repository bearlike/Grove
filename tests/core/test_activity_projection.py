"""Reads share one projection; source events determine the computation budget."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from grove.core.activity import (
    ActivityService,
    DashboardDelta,
    DashboardSnapshot,
    ProjectGroup,
    WorkspaceActivity,
)
from grove.core.activity_runtime import ActivityRuntime
from grove.core.admission import Admission, AdmissionLimits
from grove.core.agents import AgentActivityState
from grove.core.config import GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def row(root: Path, key: str) -> WorkspaceActivity:
    now = datetime.now(UTC)
    state = WorkspaceState(
        id=key,
        title=key,
        repo_root=str(root),
        branch="main",
        base_branch="main",
        worktree_path=str(root),
        tmux_session=key,
        agent_name="shell",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        agent_session_id=f"session-{key}",
    )
    return WorkspaceActivity(
        state=state,
        sessions=(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=now,
    )


class ProjectionSource(ActivityService):
    """Replace external discovery and git I/O, retaining real projection transitions."""

    def __init__(self, root: Path) -> None:
        super().__init__(
            registry=RepoRegistry(
                cfg=GroveConfig(), store=JsonWorkspaceStore(path=root / "state.json")
            )
        )
        self.root = root
        self.source = {key: row(root, key) for key in ("one", "two")}
        store = self._registry._store
        for item in self.source.values():
            store.save(replace(item.state, status=WorkspaceStatus.PAUSED))
        self.bootstraps = 0
        self.reads: list[str] = []
        self.entered: Event | None = None
        self.release: Event | None = None

    def _collect_snapshot(self) -> DashboardSnapshot:
        self.bootstraps += 1
        return DashboardSnapshot(
            projects=(
                ProjectGroup(
                    repo_root=str(self.root),
                    repo_name=self.root.name,
                    cwd=str(self.root),
                    workspaces=tuple(self.source.values()),
                ),
            ),
            generated_at=datetime.now(UTC),
        )

    def _workspace_activity(
        self, mgr: WorkspaceManager, state: WorkspaceState
    ) -> WorkspaceActivity:
        self.reads.append(state.id)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(2)
        return replace(self.source[state.id], dirty_files=len(self.reads))


def test_bootstrap_is_explicit_and_many_reads_do_not_discover(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    # The FIRST read bootstraps rather than reporting an empty fleet, which is
    # indistinguishable from a host that has none. The property under test is
    # unchanged and is the next assertion: it happens ONCE, however many times
    # anyone reads.
    assert len(service.snapshot().projects[0].workspaces) == 2
    for _ in range(100):
        assert len(service.snapshot().projects[0].workspaces) == 2
        service.bootstrap()
    assert service.bootstraps == 1
    assert service.reads == []
    assert service.workspace_keys_for_session("session-one") == ((str(tmp_path), "one"),)


def test_delete_removes_only_its_session_index(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    service.bootstrap()
    service.remove_workspace(str(tmp_path), "one")
    assert service.workspace_keys_for_session("session-one") == ()
    assert service.workspace_keys_for_session("session-two") == ((str(tmp_path), "two"),)
    assert [item.state.id for item in service.snapshot().projects[0].workspaces] == ["two"]


@pytest.mark.asyncio
async def test_hook_is_keyed_and_reaches_two_consumers(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=4, max_bytes=4096))
    first: list[DashboardDelta] = []
    second: list[DashboardDelta] = []
    service.subscribe(first.append)
    service.subscribe(second.append)
    await runtime.start()
    await runtime.wait_ready()
    try:
        assert runtime.hook("session-one") is Admission.ACCEPTED
        async with asyncio.timeout(2):
            while not second:
                await asyncio.sleep(0)
        assert service.reads == ["one"]
        assert first[0].workspace_id == second[0].workspace_id == "one"
        assert service.bootstraps == 1
    finally:
        await runtime.close()
    assert runtime.hook("session-one") is Admission.NOT_READY


@pytest.mark.asyncio
async def test_hook_during_refresh_leaves_one_trailing_update(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    service.entered = Event()
    service.release = Event()
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=2, max_bytes=4096))
    seen: list[DashboardDelta] = []
    service.subscribe(seen.append)
    await runtime.start()
    await runtime.wait_ready()
    try:
        runtime.hook("session-one")
        async with asyncio.timeout(2):
            while not service.entered.is_set():
                await asyncio.sleep(0)
        for _ in range(50):
            assert runtime.hook("session-one") in (Admission.ACCEPTED, Admission.COALESCED)
        service.release.set()
        async with asyncio.timeout(2):
            while len(seen) < 2:
                await asyncio.sleep(0)
        assert service.reads == ["one", "one"]
        assert service.bootstraps == 1
    finally:
        service.release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_recovery_emits_changed_and_removed_rows_and_evicts_sessions(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=2, max_bytes=1))
    seen: list[DashboardDelta] = []
    service.subscribe(seen.append)
    await runtime.start()
    await runtime.wait_ready()
    try:
        service._settled["session-two"] = (AgentActivityState.IDLE, datetime.now(UTC))
        service._spine_cache["session-two"] = (0, object())  # type: ignore[assignment]
        del service.source["two"]
        service.source["one"] = replace(service.source["one"], dirty_files=1)

        runtime.request_recovery()
        async with asyncio.timeout(2):
            while len(seen) < 2:
                await asyncio.sleep(0)

        assert [(delta.kind, delta.workspace_id) for delta in seen] == [
            ("workspace_changed", "two"),
            ("session_activity", "one"),
        ]
        assert [row.state.id for row in service.snapshot().projects[0].workspaces] == ["one"]
        assert "session-two" not in service._settled
        assert "session-two" not in service._spine_cache
    finally:
        await runtime.close()
