"""Bridge lifecycle-owned identities into maintained runtime liveness streams.

``RuntimeEvents`` owns readers and facts; this daemon owner maps their identities
back to workspaces, invalidates the activity projection, and updates only the
workspace whose lifecycle edge changed. Initial Docker/tmux witnesses are
collected off the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from grove.core import tmux
from grove.core.activity import RefreshDomain
from grove.core.activity_runtime import ActivityRuntime, WorkspaceInvalidated
from grove.core.admission import AdmissionLimits, BoundedInbox, InboxClosed
from grove.core.container_runtime import ContainerLiveness, ContainerState
from grove.core.errors import WorkspaceNotFound
from grove.core.manager import WorkspaceEvent, WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.runtime_events import RuntimeEvents, RuntimeLivenessChange, RuntimeLivenessScope
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState


class RuntimeSources:
    """Maintain event-backed liveness only for currently persisted workspaces.

    Lifecycle callbacks run on the worker that completed the verb. They enqueue
    their workspace id through a thread-safe, keyed inbox instead of touching
    loop-owned source state. Taking removes the key, so an edge that arrives
    during its refresh remains as a trailing update rather than being lost.
    """

    def __init__(
        self,
        *,
        registry: RepoRegistry,
        store: JsonWorkspaceStore,
        activity_runtime: ActivityRuntime,
        docker_bin: str = "docker",
        events: RuntimeEvents | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._activity_runtime = activity_runtime
        self._docker = ContainerLiveness(docker_bin=docker_bin)
        self._events = events or RuntimeEvents(
            docker_bin=docker_bin,
            on_change=self._changed,
            on_host_tmux_activity=self._host_tmux_activity_changed,
        )
        self._states: dict[str, WorkspaceState] = {}
        # Events say only alive/dead. Keep bootstrap's richer container verdict
        # (especially UNPROVISIONED) until an event changes that identity.
        self._container_states: dict[str, ContainerState | None] = {}
        self._host_tmux_liveness: dict[str, bool | None] = {}
        self._host_tmux_activity: dict[str, datetime | None] = {}
        self._by_container: dict[str, set[tuple[str, str]]] = {}
        self._by_host_tmux: dict[str, set[tuple[str, str]]] = {}
        self._manager_unsubs: list[Callable[[], None]] = []
        self._inbox = BoundedInbox[WorkspaceEvent](AdmissionLimits())
        self._task: asyncio.Task[None] | None = None
        self._started = False
        self._closing = False
        self._idle_deadlines: dict[str, asyncio.TimerHandle] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._inbox.bind()
        self._manager_unsubs.append(self._registry.subscribe_managers(self._bind_manager))
        try:
            self._store.invalidate()
            states = await asyncio.to_thread(self._store.load_all)
            facts = await asyncio.to_thread(self._bootstrap_facts, states)
            self._apply_states(states, facts)
            await self._events.bootstrap(
                self._scope(),
                containers=self._container_liveness_values(),
                host_tmux_sessions=self._host_tmux_liveness_values(),
                host_tmux_activity=self._host_tmux_activity,
            )
        except BaseException:
            self._inbox.close()
            for unsubscribe in self._manager_unsubs:
                unsubscribe()
            self._manager_unsubs.clear()
            raise
        self._started = True
        self._task = asyncio.create_task(self._run(), name="grove-runtime-source-refresh")

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        for deadline in self._idle_deadlines.values():
            deadline.cancel()
        self._idle_deadlines.clear()
        for unsubscribe in self._manager_unsubs:
            unsubscribe()
        self._manager_unsubs.clear()
        self._inbox.close()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._events.aclose()

    def _bind_manager(self, _root: Path, manager: WorkspaceManager) -> None:
        manager.bind_runtime_liveness(
            container=self._container_state,
            host_tmux=self._host_tmux_liveness.get,
            host_tmux_activity=self._events.host_tmux_activity,
        )
        self._manager_unsubs.append(manager.subscribe(self._lifecycle_changed))

    def _lifecycle_changed(self, event: WorkspaceEvent) -> None:
        """Accept worker-thread lifecycle edges without taking loop ownership."""
        if self._closing:
            return
        self._inbox.offer(event, size_bytes=96, key=event.workspace_id)

    async def _run(self) -> None:
        while True:
            try:
                delivery = await self._inbox.take()
            except InboxClosed:
                return
            try:
                await self._refresh_workspace(delivery.value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("runtime source refresh failed: {}", type(exc).__name__)
                self._activity_runtime.request_recovery()
            finally:
                self._inbox.complete(delivery)

    async def _refresh_workspace(self, event: WorkspaceEvent) -> None:
        """Replace one lifecycle record and preserve every other cached witness."""
        previous = self._states.get(event.workspace_id)
        if event.kind == "killed":
            self._remove_workspace(event.workspace_id)
            await self._replace_scope()
            return
        self._store.invalidate()
        try:
            state = await asyncio.to_thread(self._store.get, event.workspace_id)
        except WorkspaceNotFound:
            self._remove_workspace(event.workspace_id)
            await self._replace_scope()
            return
        previous_liveness = self._host_tmux_liveness.get(state.tmux_session)
        previous_activity = self._host_tmux_activity.get(state.tmux_session)
        fact = await asyncio.to_thread(
            self._state_fact, state, previous, previous_liveness, previous_activity
        )
        self._remove_workspace(event.workspace_id)
        self._states[state.id] = state
        self._apply_fact(state, fact)
        if state.runtime is Runtime.HOST:
            self._schedule_idle_deadline(state.repo_root, state.id)
        await self._replace_scope()

    async def _replace_scope(self) -> None:
        await self._events.replace_scope(
            self._scope(),
            containers=self._container_liveness_values(),
            host_tmux_sessions=self._host_tmux_liveness_values(),
            host_tmux_activity=self._host_tmux_activity,
        )

    def _bootstrap_facts(
        self, states: Iterable[WorkspaceState]
    ) -> dict[str, tuple[ContainerState | None, bool | None, datetime | None]]:
        return {state.id: self._state_fact(state, None, None, None) for state in states}

    def _state_fact(
        self,
        state: WorkspaceState,
        previous: WorkspaceState | None,
        previous_liveness: bool | None,
        previous_activity: datetime | None,
    ) -> tuple[ContainerState | None, bool | None, datetime | None]:
        if state.runtime is Runtime.CONTAINER and state.container is not None:
            return (self._docker.state_of(state.container), None, None)
        if state.runtime is not Runtime.HOST:
            return (None, None, None)
        if previous is not None and previous.tmux_session == state.tmux_session:
            return (None, previous_liveness, previous_activity)
        alive = tmux.has_session(state.tmux_session)
        age = tmux.pane_activity_seconds_ago(state.tmux_session) if alive else None
        activity = datetime.now(UTC) - timedelta(seconds=age) if age is not None else None
        return (None, alive, activity)

    def _apply_states(
        self,
        states: Iterable[WorkspaceState],
        facts: dict[str, tuple[ContainerState | None, bool | None, datetime | None]],
    ) -> None:
        for state in states:
            self._states[state.id] = state
            self._apply_fact(state, facts[state.id])
        self._schedule_idle_deadlines()

    def _apply_fact(
        self,
        state: WorkspaceState,
        fact: tuple[ContainerState | None, bool | None, datetime | None],
    ) -> None:
        container_state, liveness, activity = fact
        key = (state.repo_root, state.id)
        if state.runtime is Runtime.CONTAINER and state.container is not None:
            self._container_states[state.container.container_id] = container_state
            self._by_container.setdefault(state.container.container_id, set()).add(key)
        elif state.runtime is Runtime.HOST:
            self._host_tmux_liveness[state.tmux_session] = liveness
            self._host_tmux_activity[state.tmux_session] = activity
            self._by_host_tmux.setdefault(state.tmux_session, set()).add(key)

    def _remove_workspace(self, workspace_id: str) -> None:
        state = self._states.pop(workspace_id, None)
        if state is None:
            return
        self._cancel_idle_deadline(workspace_id)
        key = (state.repo_root, state.id)
        if state.runtime is Runtime.CONTAINER and state.container is not None:
            self._discard_route(self._by_container, state.container.container_id, key)
            if state.container.container_id not in self._by_container:
                self._container_states.pop(state.container.container_id, None)
        elif state.runtime is Runtime.HOST:
            self._discard_route(self._by_host_tmux, state.tmux_session, key)
            if state.tmux_session not in self._by_host_tmux:
                self._host_tmux_liveness.pop(state.tmux_session, None)
                self._host_tmux_activity.pop(state.tmux_session, None)

    @staticmethod
    def _discard_route(
        routes: dict[str, set[tuple[str, str]]], identity: str, key: tuple[str, str]
    ) -> None:
        keys = routes.get(identity)
        if keys is None:
            return
        keys.discard(key)
        if not keys:
            del routes[identity]

    def _scope(self) -> RuntimeLivenessScope:
        return RuntimeLivenessScope(
            container_ids=frozenset(self._by_container),
            host_tmux_sessions=frozenset(self._by_host_tmux),
        )

    def _container_liveness_values(self) -> dict[str, bool | None]:
        values: dict[str, bool | None] = {}
        for container_id, state in self._container_states.items():
            values[container_id] = (
                state in (ContainerState.RUNNING, ContainerState.UNPROVISIONED)
                if state is not None
                else None
            )
        return values

    def _host_tmux_liveness_values(self) -> dict[str, bool | None]:
        return {session: self._host_tmux_liveness.get(session) for session in self._by_host_tmux}

    def _container_state(self, container_id: str) -> ContainerState | None:
        alive = self._events.container_liveness(container_id)
        if alive is None:
            return None
        if not alive:
            return ContainerState.ABSENT
        return self._container_states.get(container_id, ContainerState.RUNNING)

    def _changed(self, change: RuntimeLivenessChange) -> None:
        """Invalidate shared identities only when their liveness answer changes."""
        if change.kind == "container" and change.alive is not None:
            self._container_states[change.identity] = (
                ContainerState.RUNNING if change.alive else ContainerState.ABSENT
            )
        elif change.kind == "host_tmux":
            self._host_tmux_liveness[change.identity] = change.alive
        identities = (
            self._by_container.get(change.identity, ())
            if change.kind == "container"
            else self._by_host_tmux.get(change.identity, ())
        )
        for repo_root, workspace_id in identities:
            if change.kind == "host_tmux":
                if change.alive is True:
                    self._schedule_idle_deadline(repo_root, workspace_id)
                else:
                    self._host_tmux_activity[change.identity] = None
                    self._cancel_idle_deadline(workspace_id)
            self._invalidate(repo_root, workspace_id)

    def _host_tmux_activity_changed(self, session: str, observed_at: datetime) -> None:
        """Refresh the authoritative activity instant without reprojecting every byte."""
        self._host_tmux_activity[session] = observed_at
        for repo_root, workspace_id in self._by_host_tmux.get(session, ()):
            was_idle = workspace_id not in self._idle_deadlines
            self._schedule_idle_deadline(repo_root, workspace_id)
            if was_idle:
                self._invalidate(repo_root, workspace_id)

    def _schedule_idle_deadlines(self) -> None:
        for session, identities in self._by_host_tmux.items():
            if self._host_tmux_liveness.get(session) is not True:
                continue
            if self._host_tmux_activity.get(session) is None:
                continue
            for repo_root, workspace_id in identities:
                self._schedule_idle_deadline(repo_root, workspace_id)

    def _schedule_idle_deadline(self, repo_root: str, workspace_id: str) -> None:
        state = self._states.get(workspace_id)
        if state is None:
            return
        observed_at = self._host_tmux_activity.get(state.tmux_session)
        if observed_at is None:
            return
        cfg = self._registry.get(Path(repo_root)).config
        deadline_at = observed_at + timedelta(seconds=cfg.tmux.activity_threshold_seconds + 0.01)
        delay = (deadline_at - datetime.now(UTC)).total_seconds()
        if delay <= 0:
            self._cancel_idle_deadline(workspace_id)
            return
        previous = self._idle_deadlines.pop(workspace_id, None)
        if previous is not None:
            previous.cancel()
        self._idle_deadlines[workspace_id] = asyncio.get_running_loop().call_later(
            delay, self._idle_due, repo_root, workspace_id, observed_at
        )

    def _cancel_idle_deadline(self, workspace_id: str) -> None:
        deadline = self._idle_deadlines.pop(workspace_id, None)
        if deadline is not None:
            deadline.cancel()

    def _idle_due(self, repo_root: str, workspace_id: str, observed_at: datetime) -> None:
        state = self._states.get(workspace_id)
        if state is None or self._host_tmux_activity.get(state.tmux_session) != observed_at:
            return
        self._idle_deadlines.pop(workspace_id, None)
        if not self._closing:
            self._invalidate(repo_root, workspace_id)

    def _invalidate(self, repo_root: str, workspace_id: str) -> None:
        self._activity_runtime.invalidate(
            WorkspaceInvalidated(
                repo_root, workspace_id, reason="runtime", domains=RefreshDomain.RUNTIME
            )
        )


__all__ = ["RuntimeSources"]
