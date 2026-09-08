"""Maintain scoped runtime liveness from Docker and tmux event streams.

The lifecycle owns which persisted container ids and host tmux-session names
matter.  This module owns only their current, event-maintained answer: it
bootstraps from the lifecycle's one-time witness, then changes that answer from
long-lived command streams.  It never discovers workspaces, inspects Docker, or
asks tmux whether a session exists on a render path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from loguru import logger

from grove.core.container_runtime import ContainerState


@dataclass(frozen=True, slots=True)
class RuntimeLivenessScope:
    """The persisted runtime identities whose liveness this owner may retain."""

    container_ids: frozenset[str] = frozenset()
    host_tmux_sessions: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RuntimeLivenessChange:
    """One cached liveness answer that changed because a maintained source said so."""

    kind: Literal["container", "host_tmux"]
    identity: str
    alive: bool | None


class RuntimeEventReader(Protocol):
    """One bounded, maintained line source owned by :class:`RuntimeEvents`."""

    @property
    def returncode(self) -> int | None: ...

    def lines(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class RuntimeEventTransport(Protocol):
    """The two command streams this liveness owner needs, and no generic bus."""

    async def docker_events(self, *, docker_bin: str) -> RuntimeEventReader: ...

    async def tmux_control(self, *, session: str) -> RuntimeEventReader: ...


class _SubprocessEventReader:
    """Bound one command's line retention and reap it when its owner stops."""

    _CLOSE_WAIT_SECONDS = 0.5

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._closed = False

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def lines(self) -> AsyncIterator[str]:
        stdout = self._process.stdout
        assert stdout is not None
        while not self._closed:
            try:
                line = await stdout.readline()
            except ValueError:
                # StreamReader's limit prevents one malformed event from being
                # retained unboundedly.  Ending this source produces the honest
                # unknown state until the lifecycle explicitly reconnects it.
                logger.warning("runtime events: source emitted a frame above the line limit")
                return
            if not line:
                return
            yield line.decode(errors="replace").rstrip("\r\n")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.returncode is None:
            self._process.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._process.wait(), timeout=self._CLOSE_WAIT_SECONDS)
            if self._process.returncode is None:
                self._process.kill()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._process.wait(), timeout=self._CLOSE_WAIT_SECONDS)


class AsyncioRuntimeEventTransport:
    """Run the documented Docker and tmux command-line event protocols.

    Docker's CLI event stream is intentionally used instead of an API client:
    the configured executable is already Grove's supported Docker boundary.
    Tmux control mode is attached read-only to a lifecycle-provided session, so
    the reader cannot create a server or session while observing it.
    """

    _MAX_FRAME_BYTES = 16 * 1024

    async def docker_events(self, *, docker_bin: str) -> RuntimeEventReader:
        process = await asyncio.create_subprocess_exec(
            docker_bin,
            "events",
            "--filter",
            "type=container",
            "--format",
            "{{json .}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=self._MAX_FRAME_BYTES,
        )
        return _SubprocessEventReader(process)

    async def tmux_control(self, *, session: str) -> RuntimeEventReader:
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "-C",
            "attach-session",
            "-r",
            "-t",
            session,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=self._MAX_FRAME_BYTES,
        )
        return _SubprocessEventReader(process)


class RuntimeEvents:
    """A shared, explicitly-recovered liveness cache for known runtime identities.

    ``bootstrap`` is deliberately one-shot.  Its values are the lifecycle's
    initial witness, not a discovery mechanism.  On a source exit the affected
    cache changes to ``None`` (cannot tell), and the owner remains stopped until
    its lifecycle calls :meth:`reconnect`; there is no hidden retry loop.
    """

    _CONTAINER_LIVE_ACTIONS = frozenset({"start", "restart", "unpause"})
    _CONTAINER_DEAD_ACTIONS = frozenset({"die", "destroy", "kill", "pause", "stop"})

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        transport: RuntimeEventTransport | None = None,
        on_change: Callable[[RuntimeLivenessChange], None] | None = None,
    ) -> None:
        self._docker_bin = docker_bin
        self._transport = transport or AsyncioRuntimeEventTransport()
        self._on_change = on_change
        self._scope = RuntimeLivenessScope()
        self._container_liveness: dict[str, bool | None] = {}
        self._host_tmux_liveness: dict[str, bool | None] = {}
        self._host_tmux_activity: dict[str, datetime | None] = {}
        self._container_task: asyncio.Task[None] | None = None
        self._tmux_tasks: dict[str, asyncio.Task[None]] = {}
        self._bootstrapped = False
        self._closed = False

    async def bootstrap(
        self,
        scope: RuntimeLivenessScope,
        *,
        containers: Mapping[str, bool | None] | None = None,
        host_tmux_sessions: Mapping[str, bool | None] | None = None,
        host_tmux_activity: Mapping[str, datetime | None] | None = None,
    ) -> None:
        """Seed the cache once from lifecycle evidence and arm maintained readers."""
        if self._bootstrapped:
            raise RuntimeError("runtime events already bootstrapped")
        self._bootstrapped = True
        await self._replace_scope(
            scope,
            containers={} if containers is None else containers,
            host_tmux_sessions={} if host_tmux_sessions is None else host_tmux_sessions,
            host_tmux_activity={} if host_tmux_activity is None else host_tmux_activity,
        )

    async def replace_scope(
        self,
        scope: RuntimeLivenessScope,
        *,
        containers: Mapping[str, bool | None] | None = None,
        host_tmux_sessions: Mapping[str, bool | None] | None = None,
        host_tmux_activity: Mapping[str, datetime | None] | None = None,
    ) -> None:
        """Apply a lifecycle edge without scanning for identities outside its scope."""
        if not self._bootstrapped:
            raise RuntimeError("runtime events must bootstrap before replacing scope")
        await self._replace_scope(
            scope,
            containers={} if containers is None else containers,
            host_tmux_sessions={} if host_tmux_sessions is None else host_tmux_sessions,
            host_tmux_activity={} if host_tmux_activity is None else host_tmux_activity,
        )

    async def reconnect(self) -> None:
        """Explicitly recover maintained readers after an exit or transport repair."""
        self._require_bootstrapped()
        await self._stop_readers()
        for identity in tuple(self._container_liveness):
            self._set_container(identity, None)
        for identity in tuple(self._host_tmux_liveness):
            self._set_host_tmux(identity, None)
        self._start_readers()

    def container_liveness(self, container_id: str) -> bool | None:
        """Return this scoped container's cached state; ``None`` means unknown."""
        return self._container_liveness.get(container_id)

    def container_liveness_state(self, container_id: str) -> ContainerState | None:
        """Adapt the event cache to the manager's container-state seam."""
        alive = self.container_liveness(container_id)
        if alive is None:
            return None
        return ContainerState.RUNNING if alive else ContainerState.ABSENT

    def host_tmux_liveness(self, session: str) -> bool | None:
        """Return this scoped host session's cached state; ``None`` means unknown."""
        return self._host_tmux_liveness.get(session)

    def host_tmux_activity(self, session: str) -> datetime | None:
        """Return the last control-stream frame time, or ``None`` when unknown."""
        return self._host_tmux_activity.get(session)

    async def aclose(self) -> None:
        """Cancel readers and reap their command processes exactly once."""
        if self._closed:
            return
        self._closed = True
        await self._stop_readers()

    async def _replace_scope(
        self,
        scope: RuntimeLivenessScope,
        *,
        containers: Mapping[str, bool | None],
        host_tmux_sessions: Mapping[str, bool | None],
        host_tmux_activity: Mapping[str, datetime | None],
    ) -> None:
        if self._closed:
            raise RuntimeError("runtime events is closed")
        old_scope = self._scope
        self._scope = scope
        removed_tmux = old_scope.host_tmux_sessions - scope.host_tmux_sessions
        removed_tasks: list[asyncio.Task[None]] = []
        for session in removed_tmux:
            task = self._tmux_tasks.pop(session, None)
            if task is not None:
                removed_tasks.append(task)
        for task in removed_tasks:
            task.cancel()
        if removed_tasks:
            await asyncio.gather(*removed_tasks, return_exceptions=True)
        self._container_liveness = {
            identity: containers.get(identity) for identity in scope.container_ids
        }
        self._host_tmux_liveness = {
            identity: host_tmux_sessions.get(identity) for identity in scope.host_tmux_sessions
        }
        self._host_tmux_activity = {
            identity: host_tmux_activity.get(identity) for identity in scope.host_tmux_sessions
        }
        if not scope.container_ids and self._container_task is not None:
            self._container_task.cancel()
            await asyncio.gather(self._container_task, return_exceptions=True)
            self._container_task = None
        self._start_readers()

    def _start_readers(self) -> None:
        if self._closed:
            return
        if self._scope.container_ids and (
            self._container_task is None or self._container_task.done()
        ):
            self._container_task = asyncio.create_task(
                self._consume_docker(), name="grove-runtime-docker-events"
            )
        for session in self._scope.host_tmux_sessions:
            task = self._tmux_tasks.get(session)
            if task is None or task.done():
                self._tmux_tasks[session] = asyncio.create_task(
                    self._consume_tmux(session), name=f"grove-runtime-tmux:{session}"
                )

    async def _consume_docker(self) -> None:
        reader: RuntimeEventReader | None = None
        try:
            reader = await self._transport.docker_events(docker_bin=self._docker_bin)
            async for frame in reader.lines():
                self._apply_docker_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("runtime events: Docker event stream failed: {}", type(exc).__name__)
        finally:
            if reader is not None:
                await reader.aclose()
            if not self._closed:
                for identity in tuple(self._container_liveness):
                    self._set_container(identity, None)

    async def _consume_tmux(self, session: str) -> None:
        reader: RuntimeEventReader | None = None
        exited = False
        try:
            reader = await self._transport.tmux_control(session=session)
            async for frame in reader.lines():
                if frame.startswith("%exit"):
                    exited = True
                    self._set_host_tmux(session, False)
                    return
                self._set_host_tmux(session, True)
                if frame.startswith(("%output", "%extended-output")):
                    self._host_tmux_activity[session] = datetime.now(UTC)
                    self._notify("host_tmux", session, True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "runtime events: tmux control stream for {} failed: {}", session, type(exc).__name__
            )
        finally:
            if reader is not None:
                returncode = reader.returncode
                await reader.aclose()
                if not self._closed and session in self._host_tmux_liveness and not exited:
                    # A tmux command that ran and rejected its target is a real
                    # absence; transport exceptions above remain unknown.
                    self._set_host_tmux(session, False if returncode not in (None, 0) else None)
            elif not self._closed and session in self._host_tmux_liveness:
                self._set_host_tmux(session, None)

    def _apply_docker_frame(self, frame: str) -> None:
        try:
            payload = json.loads(frame)
        except json.JSONDecodeError:
            logger.debug("runtime events: ignored malformed Docker event")
            return
        if not isinstance(payload, dict):
            return
        identity = payload.get("id")
        actor = payload.get("Actor")
        if not isinstance(identity, str) and isinstance(actor, dict):
            candidate = actor.get("ID")
            identity = candidate if isinstance(candidate, str) else ""
        if not isinstance(identity, str) or identity not in self._container_liveness:
            return
        action = payload.get("Action", payload.get("status"))
        if not isinstance(action, str):
            return
        if action in self._CONTAINER_LIVE_ACTIONS:
            self._set_container(identity, True)
        elif action in self._CONTAINER_DEAD_ACTIONS:
            self._set_container(identity, False)

    async def _stop_readers(self) -> None:
        tasks = tuple(
            task for task in (self._container_task, *self._tmux_tasks.values()) if task is not None
        )
        self._container_task = None
        self._tmux_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _set_container(self, identity: str, alive: bool | None) -> None:
        if identity not in self._container_liveness or self._container_liveness[identity] is alive:
            return
        self._container_liveness[identity] = alive
        self._notify("container", identity, alive)

    def _set_host_tmux(self, identity: str, alive: bool | None) -> None:
        if identity not in self._host_tmux_liveness or self._host_tmux_liveness[identity] is alive:
            return
        self._host_tmux_liveness[identity] = alive
        self._notify("host_tmux", identity, alive)

    def _notify(
        self, kind: Literal["container", "host_tmux"], identity: str, alive: bool | None
    ) -> None:
        if self._on_change is not None:
            self._on_change(RuntimeLivenessChange(kind, identity, alive))

    def _require_bootstrapped(self) -> None:
        if not self._bootstrapped:
            raise RuntimeError("runtime events must bootstrap before reconnecting")


__all__ = [
    "AsyncioRuntimeEventTransport",
    "RuntimeEventReader",
    "RuntimeEventTransport",
    "RuntimeEvents",
    "RuntimeLivenessChange",
    "RuntimeLivenessScope",
]
