"""Maintained runtime liveness reads no recurring inspect or session discovery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from grove.core.runtime_events import (
    RuntimeEventReader,
    RuntimeEvents,
    RuntimeLivenessChange,
    RuntimeLivenessScope,
)

CONTAINER = "c" * 64
SESSION = "grove-demo"


class _Reader:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def lines(self) -> AsyncIterator[str]:
        while (frame := await self.frames.get()) is not None:
            yield frame

    async def aclose(self) -> None:
        self.closed = True

    def exit(self, *, returncode: int = 0) -> None:
        self._returncode = returncode
        self.frames.put_nowait(None)


class _OverflowReader(_Reader):
    async def lines(self) -> AsyncIterator[str]:
        raise ValueError("Separator is not found, and chunk exceed the limit")
        yield ""  # pragma: no cover - makes this an async generator


class _Transport:
    def __init__(self) -> None:
        self.docker = _Reader()
        self.tmux: dict[str, _Reader] = {}
        self.docker_starts: list[str] = []
        self.tmux_starts: list[str] = []

    async def docker_events(self, *, docker_bin: str) -> RuntimeEventReader:
        self.docker_starts.append(docker_bin)
        return self.docker

    async def tmux_control(self, *, session: str) -> RuntimeEventReader:
        self.tmux_starts.append(session)
        return self.tmux.setdefault(session, _Reader())


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_bootstrap_uses_lifecycle_witness_then_events_change_only_scoped_identity() -> None:
    transport = _Transport()
    changes: list[RuntimeLivenessChange] = []
    events = RuntimeEvents(transport=transport, on_change=changes.append)
    scope = RuntimeLivenessScope(frozenset({CONTAINER}), frozenset({SESSION}))

    await events.bootstrap(scope, containers={CONTAINER: True}, host_tmux_sessions={SESSION: True})
    await _settle()
    transport.docker.frames.put_nowait('{"id":"' + CONTAINER + '","Action":"die"}')
    transport.docker.frames.put_nowait('{"id":"foreign","Action":"start"}')
    transport.tmux[SESSION].frames.put_nowait("%exit")
    await _settle()

    assert transport.docker_starts == ["docker"]
    assert transport.tmux_starts == [SESSION]
    assert events.container_liveness(CONTAINER) is False
    assert events.host_tmux_liveness(SESSION) is False
    assert changes == [
        RuntimeLivenessChange("container", CONTAINER, False),
        RuntimeLivenessChange("host_tmux", SESSION, False),
    ]
    await events.aclose()


async def test_tmux_output_refreshes_activity_without_a_liveness_change() -> None:
    transport = _Transport()
    changes: list[RuntimeLivenessChange] = []
    activity: list[tuple[str, object]] = []
    events = RuntimeEvents(
        transport=transport,
        on_change=changes.append,
        on_host_tmux_activity=lambda session, observed_at: activity.append((session, observed_at)),
    )
    await events.bootstrap(
        RuntimeLivenessScope(host_tmux_sessions=frozenset({SESSION})),
        host_tmux_sessions={SESSION: True},
    )
    await _settle()

    transport.tmux[SESSION].frames.put_nowait("%output %1 first")
    transport.tmux[SESSION].frames.put_nowait("%output %1 second")
    await _settle()

    assert changes == []
    assert [session for session, _observed_at in activity] == [SESSION, SESSION]
    assert events.host_tmux_activity(SESSION) == activity[-1][1]
    await events.aclose()


async def test_reader_exit_becomes_unknown_and_requires_explicit_reconnect() -> None:
    transport = _Transport()
    changes: list[RuntimeLivenessChange] = []
    events = RuntimeEvents(transport=transport, on_change=changes.append)
    await events.bootstrap(
        RuntimeLivenessScope(frozenset({CONTAINER})), containers={CONTAINER: True}
    )
    await _settle()
    transport.docker.exit(returncode=1)
    await _settle()

    assert events.container_liveness(CONTAINER) is None
    assert transport.docker_starts == ["docker"]
    assert changes == [RuntimeLivenessChange("container", CONTAINER, None)]

    transport.docker = _Reader()
    await events.reconnect()
    await _settle()

    assert transport.docker_starts == ["docker", "docker"]
    await events.aclose()


async def test_oversized_source_frame_fences_the_previous_answer_as_unknown() -> None:
    transport = _Transport()
    transport.docker = _OverflowReader()
    events = RuntimeEvents(transport=transport)

    await events.bootstrap(
        RuntimeLivenessScope(frozenset({CONTAINER})), containers={CONTAINER: True}
    )
    await _settle()

    assert events.container_liveness(CONTAINER) is None
    assert transport.docker.closed
    await events.aclose()


async def test_scope_replacement_stops_removed_tmux_reader_without_discovery() -> None:
    transport = _Transport()
    events = RuntimeEvents(transport=transport)
    await events.bootstrap(
        RuntimeLivenessScope(host_tmux_sessions=frozenset({SESSION})),
        host_tmux_sessions={SESSION: True},
    )
    await _settle()

    await events.replace_scope(RuntimeLivenessScope())
    await _settle()

    assert transport.tmux[SESSION].closed
    assert events.host_tmux_liveness(SESSION) is None
    assert transport.tmux_starts == [SESSION]
    await events.aclose()


async def test_tmux_transport_exit_clears_activity_to_restore_probe_fallback() -> None:
    transport = _Transport()
    events = RuntimeEvents(transport=transport)
    await events.bootstrap(
        RuntimeLivenessScope(host_tmux_sessions=frozenset({SESSION})),
        host_tmux_sessions={SESSION: True},
    )
    await _settle()
    transport.tmux[SESSION].frames.put_nowait("%output %1 active")
    await _settle()

    assert events.host_tmux_activity(SESSION) is not None

    transport.tmux[SESSION].exit(returncode=0)
    await _settle()

    assert events.host_tmux_liveness(SESSION) is None
    assert events.host_tmux_activity(SESSION) is None
    await events.aclose()


async def test_reconnect_after_tmux_transport_exit_does_not_retain_dead_reader() -> None:
    transport = _Transport()
    events = RuntimeEvents(transport=transport)
    await events.bootstrap(
        RuntimeLivenessScope(host_tmux_sessions=frozenset({SESSION})),
        host_tmux_sessions={SESSION: True},
    )
    await _settle()
    transport.tmux[SESSION].exit(returncode=0)
    await _settle()

    assert events.host_tmux_liveness(SESSION) is None
    assert transport.tmux_starts == [SESSION]

    transport.tmux[SESSION] = _Reader()
    await events.reconnect()
    await _settle()

    assert transport.tmux_starts == [SESSION, SESSION]
    await events.aclose()


async def test_empty_scope_never_launches_a_source() -> None:
    transport = _Transport()
    events = RuntimeEvents(transport=transport)

    await events.bootstrap(RuntimeLivenessScope())
    await _settle()

    assert transport.docker_starts == []
    assert transport.tmux_starts == []
    await events.aclose()


async def test_bootstrap_is_one_shot_and_reconnect_requires_it() -> None:
    events = RuntimeEvents(transport=_Transport())
    with pytest.raises(RuntimeError, match="bootstrap"):
        await events.reconnect()
    await events.bootstrap(RuntimeLivenessScope())
    with pytest.raises(RuntimeError, match="already bootstrapped"):
        await events.bootstrap(RuntimeLivenessScope())
    await events.aclose()
