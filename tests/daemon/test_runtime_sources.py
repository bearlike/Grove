"""Runtime liveness source ownership stays lifecycle-scoped and loop-safe."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core.activity_runtime import WorkspaceInvalidated
from grove.core.container_runtime import ContainerRuntimeState, ContainerState
from grove.core.manager import WorkspaceEvent
from grove.core.runtime_events import RuntimeLivenessChange, RuntimeLivenessScope
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus
from grove.daemon._runtime_sources import RuntimeSources

CONTAINER_A = "a" * 64
CONTAINER_B = "b" * 64
CONTAINER_C = "c" * 64


class _Store:
    def __init__(self, states: list[WorkspaceState]) -> None:
        self.states = {state.id: state for state in states}
        self.get_calls: list[str] = []
        self.load_thread: int | None = None
        self.invalidations = 0

    def invalidate(self) -> None:
        self.invalidations += 1

    def load_all(self) -> list[WorkspaceState]:
        self.load_thread = threading.get_ident()
        return list(self.states.values())

    def get(self, workspace_id: str) -> WorkspaceState:
        self.get_calls.append(workspace_id)
        return self.states[workspace_id]


class _Runtime:
    def __init__(self) -> None:
        self.invalidations: list[WorkspaceInvalidated] = []
        self.recoveries = 0

    def invalidate(self, event: WorkspaceInvalidated) -> None:
        self.invalidations.append(event)

    def request_recovery(self) -> None:
        self.recoveries += 1


class _Events:
    def __init__(self) -> None:
        self.bootstraps: list[tuple[RuntimeLivenessScope, dict[str, bool | None]]] = []
        self.replacements: list[RuntimeLivenessScope] = []
        self.container_values: dict[str, bool | None] = {}
        self.closed = 0

    async def bootstrap(
        self,
        scope: RuntimeLivenessScope,
        *,
        containers: dict[str, bool | None],
        host_tmux_sessions: dict[str, bool | None],
        host_tmux_activity: dict[str, datetime | None],
    ) -> None:
        del host_tmux_sessions, host_tmux_activity
        self.bootstraps.append((scope, dict(containers)))
        self.container_values = dict(containers)

    async def replace_scope(
        self,
        scope: RuntimeLivenessScope,
        *,
        containers: dict[str, bool | None],
        host_tmux_sessions: dict[str, bool | None],
        host_tmux_activity: dict[str, datetime | None],
    ) -> None:
        del containers, host_tmux_sessions, host_tmux_activity
        self.replacements.append(scope)

    def container_liveness(self, container_id: str) -> bool | None:
        return self.container_values.get(container_id)

    def host_tmux_activity(self, _session: str) -> datetime | None:
        return None

    async def aclose(self) -> None:
        self.closed += 1


class _Manager:
    def __init__(self) -> None:
        self.container: Callable[[str], ContainerState | None] | None = None
        self.callbacks: list[Callable[[WorkspaceEvent], None]] = []

    def bind_runtime_liveness(self, *, container: Any, **_kwargs: Any) -> None:
        self.container = container

    def subscribe(self, callback: Callable[[WorkspaceEvent], None]) -> Callable[[], None]:
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def emit(self, event: WorkspaceEvent) -> None:
        for callback in tuple(self.callbacks):
            callback(event)


class _Registry:
    def __init__(self, manager: _Manager) -> None:
        self.manager = manager
        self.callbacks: list[Callable[[Path, _Manager], None]] = []

    def subscribe_managers(self, callback: Callable[[Path, _Manager], None]) -> Callable[[], None]:
        self.callbacks.append(callback)
        callback(Path("/repo"), self.manager)
        return lambda: self.callbacks.remove(callback)

    def get(self, _root: Path) -> _Manager:
        return self.manager


def _state(workspace_id: str, container_id: str, *, repo_root: str = "/repo") -> WorkspaceState:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    return WorkspaceState(
        id=workspace_id,
        title=workspace_id,
        repo_root=repo_root,
        branch=workspace_id,
        base_branch="main",
        worktree_path=f"/worktrees/{workspace_id}",
        tmux_session=workspace_id,
        agent_name="claude",
        agent_kind="claude_code",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        runtime=Runtime.CONTAINER,
        container=ContainerRuntimeState(container_id=container_id, provisioned=True),
    )


def _owner(
    monkeypatch: pytest.MonkeyPatch, states: list[WorkspaceState]
) -> tuple[RuntimeSources, _Store, _Runtime, _Events, _Manager, list[tuple[str, int]]]:
    probes: list[tuple[str, int]] = []

    class _Docker:
        def __init__(self, *, docker_bin: str) -> None:
            del docker_bin

        def state_of(self, container: ContainerRuntimeState) -> ContainerState:
            probes.append((container.container_id, threading.get_ident()))
            return ContainerState.UNPROVISIONED

    monkeypatch.setattr("grove.daemon._runtime_sources.ContainerLiveness", _Docker)
    monkeypatch.setattr("grove.daemon._runtime_sources.tmux.has_session", lambda _session: True)
    monkeypatch.setattr(
        "grove.daemon._runtime_sources.tmux.pane_activity_seconds_ago", lambda _session: None
    )
    store = _Store(states)
    runtime = _Runtime()
    events = _Events()
    manager = _Manager()
    owner = RuntimeSources(
        registry=_Registry(manager),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        activity_runtime=runtime,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
    )
    return owner, store, runtime, events, manager, probes


@pytest.mark.asyncio
async def test_start_bootstraps_persisted_witnesses_off_loop() -> None:
    state = _state("a", CONTAINER_A)
    monkeypatch = pytest.MonkeyPatch()
    owner, store, _runtime, events, _manager, probes = _owner(monkeypatch, [state])

    try:
        await owner.start()

        assert store.load_thread != threading.get_ident()
        assert probes == [(CONTAINER_A, store.load_thread)]
        assert events.bootstraps == [
            (RuntimeLivenessScope(container_ids=frozenset({CONTAINER_A})), {CONTAINER_A: True})
        ]
    finally:
        await owner.close()
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_worker_thread_lifecycle_edge_refreshes_only_its_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _state("a", CONTAINER_A)
    second = _state("b", CONTAINER_B)
    owner, store, _runtime, events, manager, probes = _owner(monkeypatch, [first, second])
    await owner.start()
    probes.clear()

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, manager.emit, WorkspaceEvent("updated", first.id)
        )
        await _wait_for(lambda: events.replacements)

        assert store.get_calls == [first.id]
        assert probes == [(CONTAINER_A, probes[0][1])]
        assert probes[0][1] != threading.get_ident()
        assert events.replacements == [
            RuntimeLivenessScope(container_ids=frozenset({CONTAINER_A, CONTAINER_B}))
        ]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_killed_edge_removes_routes_without_store_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state("a", CONTAINER_A)
    owner, store, _runtime, events, manager, _probe_threads = _owner(monkeypatch, [state])
    await owner.start()

    try:
        manager.emit(WorkspaceEvent("killed", state.id))
        await _wait_for(lambda: events.replacements)

        assert store.get_calls == []
        assert events.replacements == [RuntimeLivenessScope()]
        assert owner._states == {}
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_unknown_container_change_preserves_witness_but_dead_marks_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state("a", CONTAINER_A)
    owner, _store, _runtime, _events, manager, _probe_threads = _owner(monkeypatch, [state])
    await owner.start()

    try:
        assert manager.container is not None
        assert manager.container(CONTAINER_A) is ContainerState.UNPROVISIONED

        owner._changed(RuntimeLivenessChange("container", CONTAINER_A, None))
        assert manager.container(CONTAINER_A) is ContainerState.UNPROVISIONED

        owner._changed(RuntimeLivenessChange("container", CONTAINER_A, False))
        assert manager.container(CONTAINER_A) is ContainerState.ABSENT
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_runtime_change_invalidates_every_workspace_on_shared_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _state("a", CONTAINER_A, repo_root="/first")
    second = _state("b", CONTAINER_A, repo_root="/second")
    foreign = _state("c", CONTAINER_C, repo_root="/third")
    owner, _store, runtime, _events, _manager, _probe_threads = _owner(
        monkeypatch, [first, second, foreign]
    )
    await owner.start()

    try:
        owner._changed(RuntimeLivenessChange("container", CONTAINER_A, False))

        assert set(runtime.invalidations) == {
            WorkspaceInvalidated("/first", "a", reason="runtime"),
            WorkspaceInvalidated("/second", "b", reason="runtime"),
        }
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_after_started_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    owner, _store, _runtime, events, _manager, _probe_threads = _owner(
        monkeypatch, [_state("a", CONTAINER_A)]
    )
    await owner.start()
    task = owner._task

    await owner.close()
    await owner.close()

    assert task is not None
    assert task.cancelled()
    assert owner._task is None
    assert events.closed == 1


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)
