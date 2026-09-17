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
    RefreshDomain,
    WorkspaceActivity,
)
from grove.core.activity_runtime import ActivityRuntime, WorkspaceInvalidated
from grove.core.admission import Admission, AdmissionLimits
from grove.core.agents import AgentActivityState
from grove.core.config import GroveConfig
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


def test_coalescing_preserves_filesystem_content_invalidation() -> None:
    file_edge = WorkspaceInvalidated(
        "repo", "workspace", "filesystem", domains=RefreshDomain.TRANSCRIPT
    )
    runtime_edge = WorkspaceInvalidated(
        "repo", "workspace", "runtime", domains=RefreshDomain.RUNTIME
    )
    for merged in (file_edge.merged(runtime_edge), runtime_edge.merged(file_edge)):
        assert merged.reason == "filesystem"
        assert merged.domains == RefreshDomain.TRANSCRIPT | RefreshDomain.RUNTIME


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
        self.source = {
            key: replace(item, state=replace(item.state, status=WorkspaceStatus.PAUSED))
            for key, item in self.source.items()
        }
        for item in self.source.values():
            store.save(item.state)
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


@pytest.mark.asyncio
async def test_source_changed_fires_for_every_filesystem_reason_not_only_worktree(
    tmp_path: Path,
) -> None:
    """`useWorkspaceDiff`'s contract is "invalidate per filesystem write", not
    "per WORKTREE-domain refresh" — a PHASE-only or TRANSCRIPT-only filesystem
    hint must still emit `workspace_source_changed`, or the webapp's diff/
    commit invalidation silently stops firing for two of three filesystem
    edges while the activity fingerprint stays green."""
    service = ProjectionSource(tmp_path)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=4, max_bytes=4096))
    seen: list[DashboardDelta] = []
    service.subscribe(seen.append)
    await runtime.start()
    await runtime.wait_ready()
    try:
        runtime.invalidate(
            WorkspaceInvalidated(
                str(tmp_path), "one", reason="filesystem", domains=RefreshDomain.PHASE
            )
        )
        async with asyncio.timeout(2):
            while not any(d.kind == "workspace_source_changed" for d in seen):
                await asyncio.sleep(0)
    finally:
        await runtime.close()
    assert [d.kind for d in seen if d.kind == "workspace_source_changed"] == [
        "workspace_source_changed"
    ]


def test_incremental_domains_reuse_retained_row_parts(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    service.bootstrap()
    calls = {"sessions": 0, "git": 0, "phase": 0}

    def sessions(*_args: object) -> list[object]:
        calls["sessions"] += 1
        return []

    def worktree(*_args: object) -> tuple[int, int, int, int, int, None, tuple[object, ...], str]:
        calls["git"] += 1
        return 0, 0, 0, 0, 0, None, (), "main"

    def phase(*_args: object) -> None:
        calls["phase"] += 1

    service.sessions_for = sessions  # type: ignore[method-assign]
    service._worktree_facts = worktree  # type: ignore[method-assign]
    service._phase = phase  # type: ignore[method-assign]

    assert (
        service.prepare_workspace_refresh(str(tmp_path), "one", domains=RefreshDomain.TRANSCRIPT)
        is not None
    )
    assert calls == {"sessions": 1, "git": 0, "phase": 0}

    assert (
        service.prepare_workspace_refresh(str(tmp_path), "one", domains=RefreshDomain.WORKTREE)
        is not None
    )
    assert calls == {"sessions": 1, "git": 1, "phase": 0}

    assert (
        service.prepare_workspace_refresh(str(tmp_path), "one", domains=RefreshDomain.PHASE)
        is not None
    )
    assert calls == {"sessions": 1, "git": 1, "phase": 1}


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
        # Hook content refreshes the retained row without re-entering the
        # full projection's git/worktree computation.
        assert service.reads == []
        assert first[0].workspace_id == second[0].workspace_id == "one"
        assert service.bootstraps == 1
    finally:
        await runtime.close()
    assert runtime.hook("session-one") is Admission.NOT_READY


@pytest.mark.asyncio
async def test_coalesced_domains_are_unioned_before_refresh(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    runtime = ActivityRuntime(service, limits=AdmissionLimits(max_items=2, max_bytes=4096))
    calls: list[RefreshDomain] = []
    original = service.prepare_workspace_refresh

    def prepare(
        root: str, key: str, *, domains: RefreshDomain = RefreshDomain.FULL
    ) -> WorkspaceActivity | None:
        calls.append(domains)
        return original(root, key, domains=domains)

    service.prepare_workspace_refresh = prepare  # type: ignore[method-assign]
    await runtime.start()
    await runtime.wait_ready()
    try:
        assert (
            runtime.invalidate(
                WorkspaceInvalidated(str(tmp_path), "one", "hook", domains=RefreshDomain.TRANSCRIPT)
            )
            is Admission.ACCEPTED
        )
        assert (
            runtime.invalidate(
                WorkspaceInvalidated(
                    str(tmp_path), "one", "filesystem", domains=RefreshDomain.WORKTREE
                )
            )
            is Admission.COALESCED
        )
        async with asyncio.timeout(2):
            while not calls:
                await asyncio.sleep(0)
        assert calls == [RefreshDomain.TRANSCRIPT | RefreshDomain.WORKTREE]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_hook_during_refresh_leaves_one_trailing_update(tmp_path: Path) -> None:
    service = ProjectionSource(tmp_path)
    service.entered = Event()
    service.release = Event()
    # The refresh is now transcript-only, so block the transcript seam rather
    # than the old full-row override.
    original_sessions = service.sessions_for
    calls = 0

    def blocked_sessions(mgr: WorkspaceManager, state: WorkspaceState) -> list[object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            service.entered.set()
            assert service.release is not None and service.release.wait(2)
        return original_sessions(mgr, state)

    def todo(*_args: object) -> None:
        return None

    def queue(*_args: object) -> None:
        return None

    def fleet(*_args: object) -> None:
        return None

    service.sessions_for = blocked_sessions  # type: ignore[method-assign]
    service._todo_progress = todo  # type: ignore[method-assign]
    service._queue_depth = queue  # type: ignore[method-assign]
    service._fleet_summary = fleet  # type: ignore[method-assign]
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
            while calls < 2:
                await asyncio.sleep(0)
        # The second hint arrived while the first transcript computation owned
        # the key, so it is retained as trailing work even when neither fixture
        # row's fingerprint happens to change.
        assert service.reads == []
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
