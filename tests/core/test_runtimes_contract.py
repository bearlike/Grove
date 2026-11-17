"""The serverless driver contract, run against an in-memory fake.

``assert_driver_conformance`` is the check every real driver runs too: it walks
one workspace through the whole lifecycle and reads ``status`` after each verb.
"""

from __future__ import annotations

from itertools import count

import pytest

from grove.core.errors import ContainerError
from grove.core.runtimes import (
    EpisodicDriver,
    HibernatingDriver,
    ProvisionRefused,
    RemoteHandle,
    RuntimeState,
    ServerlessDriver,
    TurnRequest,
    TurnResult,
    WorkspaceSpec,
)

_SPEC = WorkspaceSpec(
    workspace_id="a" * 32,
    image="registry.example.com/grove-worker:1",
    command=["grove-native-worker"],
    env={"GROVE_WORKSPACE_ID": "a" * 32},
)


class FakeDriver:
    """A platform whose thaw is a fresh execution and whose quota is ``quota`` workloads."""

    def __init__(self, *, quota: int = 10) -> None:
        self._quota = quota
        self._ids = count(1)
        self._states: dict[str, RuntimeState] = {}

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        return f"image: {spec.image}\ncommand: {list(spec.command)}\n"

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        del spec
        live = [s for s in self._states.values() if s is not RuntimeState.ABSENT]
        if len(live) >= self._quota:
            raise ProvisionRefused("quota exhausted")
        return self._start()

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        return self._states.get(handle.ref, RuntimeState.ABSENT)

    def stop(self, handle: RemoteHandle) -> None:
        self._states[handle.ref] = RuntimeState.STOPPED

    def rm(self, handle: RemoteHandle) -> None:
        self._states[handle.ref] = RuntimeState.ABSENT

    def freeze(self, handle: RemoteHandle) -> None:
        self._states[handle.ref] = RuntimeState.FROZEN

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        self._states[handle.ref] = RuntimeState.ABSENT
        return self._start()

    def _start(self) -> RemoteHandle:
        handle = RemoteHandle(runtime="fake", ref=f"exec-{next(self._ids)}")
        self._states[handle.ref] = RuntimeState.RUNNING
        return handle


def assert_driver_conformance(driver: ServerlessDriver, spec: WorkspaceSpec = _SPEC) -> None:
    """Walk one workspace through every verb and read it back after each."""
    assert isinstance(driver, ServerlessDriver)
    assert driver.emit_spec(spec).strip()

    handle = driver.up(spec)
    assert driver.status(handle) in {RuntimeState.STARTING, RuntimeState.RUNNING}

    driver.freeze(handle)
    frozen = driver.status(handle)
    thawed = driver.thaw(handle)
    # A platform with no suspend can only show the ended execution; "frozen" is
    # then Grove's own persisted fact, and thaw must hand back a NEW workload.
    assert frozen is RuntimeState.FROZEN or (frozen is RuntimeState.STOPPED and thawed != handle)
    handle = thawed
    assert driver.status(handle) in {RuntimeState.STARTING, RuntimeState.RUNNING}

    driver.stop(handle)
    assert driver.status(handle) is RuntimeState.STOPPED
    driver.rm(handle)
    # A platform that keeps ended executions as history (ACA) still reads
    # STOPPED; what rm may never leave behind is a workload that runs or bills.
    assert driver.status(handle) in {RuntimeState.ABSENT, RuntimeState.STOPPED}


def test_fake_driver_conforms() -> None:
    assert_driver_conformance(FakeDriver())


def test_quota_refusal_is_a_typed_provision_error() -> None:
    driver = FakeDriver(quota=1)
    driver.up(_SPEC)
    with pytest.raises(ProvisionRefused) as refused:
        driver.up(_SPEC)
    # Caught by every existing provision-failure arm.
    assert isinstance(refused.value, ContainerError)


_TURN = TurnRequest(workspace_id="a" * 32, branch="grove/a", session_id="", prompt="hello")


def assert_episodic_conformance(driver: EpisodicDriver, spec: WorkspaceSpec = _SPEC) -> None:
    """Run two turns, the second continuing the session the first reported."""
    assert isinstance(driver, EpisodicDriver)
    assert not isinstance(driver, ServerlessDriver)
    assert driver.emit_spec(spec).strip()
    first = driver.run_turn(spec, _TURN)
    assert first.session_id
    second = driver.run_turn(
        spec, TurnRequest(_TURN.workspace_id, _TURN.branch, first.session_id, "again")
    )
    assert second.session_id == first.session_id


class FakeEpisodic:
    """A platform that answers every turn by echoing the session it was handed."""

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        return f"image: {spec.image}\n"

    def run_turn(self, spec: WorkspaceSpec, turn: TurnRequest) -> TurnResult:
        del spec
        return TurnResult(session_id=turn.session_id or "s-1", head_commit="c", outcome="done")


def test_fake_episodic_driver_conforms() -> None:
    assert_episodic_conformance(FakeEpisodic())


def test_a_result_missing_a_field_is_refused_rather_than_completed() -> None:
    with pytest.raises(ContainerError):
        TurnResult.from_wire({"session_id": "s", "outcome": "done"})


class FakeHibernatingDriver(FakeDriver):
    """A platform that can release a frozen workload to a snapshot and restore it."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots: set[str] = set()
        self.woken: list[WorkspaceSpec] = []
        self.ups: list[WorkspaceSpec] = []
        self.forgotten: list[RemoteHandle] = []

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        self.ups.append(spec)
        return super().up(spec)

    def hibernate(self, handle: RemoteHandle) -> RemoteHandle:
        self._states[handle.ref] = RuntimeState.ABSENT
        sid = f"snap-{handle.ref}"
        self.snapshots.add(sid)
        return RemoteHandle(runtime=handle.runtime, ref=f"snapshot:{sid}")

    def wake(self, handle: RemoteHandle, spec: WorkspaceSpec) -> RemoteHandle:
        self.woken.append(spec)
        return self._start()

    def forget(self, handle: RemoteHandle) -> None:
        self.forgotten.append(handle)
        self.snapshots.discard(handle.ref.removeprefix("snapshot:"))

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        if handle.ref.startswith("snapshot:"):
            held = handle.ref.removeprefix("snapshot:") in self.snapshots
            return RuntimeState.FROZEN if held else RuntimeState.ABSENT
        return super().status(handle)


def assert_hibernate_conformance(driver: HibernatingDriver, spec: WorkspaceSpec = _SPEC) -> None:
    """Freeze, hibernate, wake from the handle plus a FRESH spec alone, and forget.

    The handle is the only state that survives, so wake is handed nothing the
    hibernate call returned beyond it; a fresh spec stands in for the manager's
    re-composed launch with a newly minted bearer.
    """
    assert isinstance(driver, HibernatingDriver)
    assert isinstance(driver, ServerlessDriver)
    handle = driver.up(spec)
    driver.freeze(handle)
    hibernated = driver.hibernate(handle)
    assert hibernated != handle
    assert driver.status(handle) in {RuntimeState.ABSENT, RuntimeState.STOPPED}
    assert driver.status(hibernated) is RuntimeState.FROZEN
    fresh = WorkspaceSpec(spec.workspace_id, spec.image, spec.command, dict(spec.env))
    woken = driver.wake(hibernated, fresh)
    assert driver.status(woken) in {RuntimeState.STARTING, RuntimeState.RUNNING}
    second = driver.hibernate(woken)
    driver.forget(second)
    assert driver.status(second) is RuntimeState.ABSENT
    driver.forget(second)


def test_fake_hibernating_driver_conforms() -> None:
    assert_hibernate_conformance(FakeHibernatingDriver())


def test_a_plain_driver_is_not_hibernating() -> None:
    assert not isinstance(FakeDriver(), HibernatingDriver)
