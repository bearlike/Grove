"""The persisted runtime selects the post-launch implementation, and nothing else does."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.config import CloudRunJobRuntime, GroveConfig
from grove.core.container_runtime import ContainerRuntimeState, ContainerState
from grove.core.devcontainer import DevcontainerCli
from grove.core.errors import CapabilityUnavailable, ContainerError
from grove.core.native_owners import WorktreeRead
from grove.core.runtime import RuntimeResolver
from grove.core.runtimes import RemoteHandle
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus
from grove.core.workspace_runtime import (
    ContainerRuntime,
    HostRuntime,
    RemoteRuntime,
    WorkspaceRuntimes,
    WorktreeRunner,
)
from tests.core.test_runtimes_contract import _SPEC, FakeDriver

CONTAINER = ContainerRuntimeState(container_id="c" * 64, remote_user="vscode")


def _state(
    tmp_path: Path,
    runtime: Runtime,
    container: ContainerRuntimeState | None,
    remote: RemoteHandle | None = None,
) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id="a" * 32,
        title="t",
        repo_root=str(tmp_path),
        branch="b",
        base_branch="main",
        worktree_path=str(tmp_path),
        tmux_session="s",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        runtime=runtime,
        container=container,
        remote=remote,
    )


@pytest.fixture
def runtimes() -> WorkspaceRuntimes:
    return WorkspaceRuntimes(GroveConfig(), cli=DevcontainerCli)


def test_the_persisted_runtime_selects_the_implementation(
    runtimes: WorkspaceRuntimes, tmp_path: Path
) -> None:
    assert isinstance(runtimes.for_state(_state(tmp_path, Runtime.HOST, None)), HostRuntime)
    container = runtimes.for_state(_state(tmp_path, Runtime.CONTAINER, CONTAINER))
    assert isinstance(container, ContainerRuntime)


@pytest.mark.parametrize(
    ("found", "expected"),
    [(ContainerState.RUNNING, True), (ContainerState.UNPROVISIONED, False), (None, None)],
)
def test_container_liveness_never_spells_cannot_tell_as_an_answer(
    runtimes: WorkspaceRuntimes,
    tmp_path: Path,
    found: ContainerState | None,
    expected: bool | None,
) -> None:
    runtimes.container.bind_liveness(lambda _id: found)
    state = _state(tmp_path, Runtime.CONTAINER, CONTAINER)
    assert runtimes.for_state(state).liveness(state) is expected


def test_a_host_record_carrying_a_failed_promotion_still_tears_its_container_down(
    runtimes: WorkspaceRuntimes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed host-to-container respawn grafts the container onto a HOST record."""
    torn: list[str] = []
    monkeypatch.setattr(
        "grove.core.container_runtime.ContainerLifecycle.teardown",
        lambda self: torn.append(self.state.container_id),
    )
    state = _state(tmp_path, Runtime.HOST, CONTAINER)
    assert runtimes.for_state(state).teardown(state).failure is None
    assert torn == [CONTAINER.container_id]


def test_worktree_reader_runs_where_the_agent_sees_the_worktree(
    runtimes: WorkspaceRuntimes, tmp_path: Path
) -> None:
    host = _state(tmp_path, Runtime.HOST, None)
    host_reader = runtimes.for_state(host).worktree_reader(host)
    assert host_reader is not None and host_reader(["git", "status"]) == ["git", "status"]
    boxed = _state(tmp_path, Runtime.CONTAINER, CONTAINER)
    box_reader = runtimes.for_state(boxed).worktree_reader(boxed)
    assert box_reader is not None
    assert box_reader(["git"]) == ["docker", "exec", "-u", "vscode", "c" * 64, "git"]


def test_container_freeze_is_docker_pause_and_thaw_unpause(
    runtimes: WorkspaceRuntimes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#970: pause keeps memory and the firewall the last start applied; stop would not."""
    ran: list[list[str]] = []
    monkeypatch.setattr(
        "grove.core.container_runtime.DockerCli.run",
        lambda self, argv, **_kw: ran.append(argv),
    )
    state = _state(tmp_path, Runtime.CONTAINER, CONTAINER)
    runtimes.for_state(state).freeze(state)
    runtimes.for_state(state).thaw(state)
    assert ran == [["docker", "pause", "c" * 64], ["docker", "unpause", "c" * 64]]


def test_host_freeze_and_thaw_do_nothing(runtimes: WorkspaceRuntimes, tmp_path: Path) -> None:
    state = _state(tmp_path, Runtime.HOST, None)
    runtimes.for_state(state).freeze(state)
    runtimes.for_state(state).thaw(state)


_RUNTIMES = {"gcp": CloudRunJobRuntime(project="p", region="r", job="j", image="i")}
_REFUSING = ["attach_instruction", "worktree_reader"]


def _remote(tmp_path: Path, driver: FakeDriver) -> tuple[RemoteRuntime, WorkspaceState]:
    runtime = RemoteRuntime(_RUNTIMES, factory=lambda name, cfg: driver)
    handle = RemoteHandle(runtime="gcp", ref=driver.up(_SPEC).ref)
    return runtime, _state(tmp_path, Runtime.REMOTE, None, handle)


def test_a_remote_record_dispatches_to_the_remote_runtime(
    tmp_path: Path, runtimes: WorkspaceRuntimes
) -> None:
    _, state = _remote(tmp_path, FakeDriver())
    assert runtimes.for_state(state) is runtimes.remote


def test_remote_liveness_and_teardown_go_through_the_driver(tmp_path: Path) -> None:
    runtime, state = _remote(tmp_path, FakeDriver())
    assert runtime.liveness(state) is True
    assert runtime.teardown(state).failure is None
    assert runtime.liveness(state) is False


def test_a_handle_naming_an_unconfigured_runtime_is_unknown_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    runtime, _ = _remote(tmp_path, FakeDriver())
    state = _state(tmp_path, Runtime.REMOTE, None, RemoteHandle(runtime="gone", ref="x"))
    assert runtime.liveness(state) is None
    assert runtime.teardown(state).failure


@pytest.mark.parametrize("verb", _REFUSING)
def test_remote_verbs_needing_the_worker_refuse_and_leave_it_running(
    tmp_path: Path, verb: str
) -> None:
    runtime, state = _remote(tmp_path, FakeDriver())
    with pytest.raises(CapabilityUnavailable):
        getattr(runtime, verb)(state)
    assert runtime.liveness(state) is True


def test_a_remote_request_resolves_remote_never_a_downgrade(tmp_path: Path) -> None:
    resolver = RuntimeResolver(GroveConfig(), cli=DevcontainerCli())
    assert resolver.resolve(requested=Runtime.REMOTE, repo_root=tmp_path).runtime is Runtime.REMOTE


def _pushing(answer: WorktreeRead, pushes: list[list[str]]) -> WorktreeRunner:
    def run(state: WorkspaceState, argv: Sequence[str], timeout: float) -> WorktreeRead:
        pushes.append(list(argv))
        return answer

    return run


@pytest.mark.parametrize("verb", ["freeze", "stop"])
def test_freeze_and_stop_push_the_branch_before_ending_the_workload(
    tmp_path: Path, verb: str
) -> None:
    driver, pushes = FakeDriver(), []
    runtime = RemoteRuntime(
        _RUNTIMES, factory=lambda n, c: driver, runner=_pushing(WorktreeRead(0, ""), pushes)
    )
    state = _state(tmp_path, Runtime.REMOTE, None, RemoteHandle("gcp", driver.up(_SPEC).ref))
    getattr(runtime, verb)(state)
    assert pushes == [["git", "push", "-q", "origin", "HEAD:refs/heads/b"]]
    assert runtime.liveness(state) is False


@pytest.mark.parametrize("verb", ["freeze", "stop"])
def test_a_failed_push_leaves_the_workload_running(tmp_path: Path, verb: str) -> None:
    driver = FakeDriver()
    runtime = RemoteRuntime(
        _RUNTIMES,
        factory=lambda n, c: driver,
        runner=_pushing(WorktreeRead(1, "rejected"), []),
    )
    state = _state(tmp_path, Runtime.REMOTE, None, RemoteHandle("gcp", driver.up(_SPEC).ref))
    with pytest.raises(ContainerError, match="rejected"):
        getattr(runtime, verb)(state)
    assert runtime.liveness(state) is True


def test_thaw_returns_the_new_workload_handle(tmp_path: Path) -> None:
    driver = FakeDriver()
    runtime = RemoteRuntime(_RUNTIMES, factory=lambda n, c: driver)
    state = _state(tmp_path, Runtime.REMOTE, None, RemoteHandle("gcp", driver.up(_SPEC).ref))
    assert runtime.thaw(state) == RemoteHandle("fake", "exec-2")


def test_launch_names_the_runtime_block_on_the_handle(tmp_path: Path) -> None:
    runtime = RemoteRuntime(_RUNTIMES, factory=lambda n, c: FakeDriver())
    assert runtime.launch("gcp", _SPEC).runtime == "gcp"


@pytest.mark.parametrize(("connected", "expected"), [(True, True), (False, False)])
def test_a_running_workload_whose_worker_is_gone_reads_offline(
    tmp_path: Path, connected: bool, expected: bool
) -> None:
    driver = FakeDriver()
    runtime = RemoteRuntime(
        _RUNTIMES, factory=lambda name, cfg: driver, reachable=lambda state: connected
    )
    handle = RemoteHandle(runtime="gcp", ref=driver.up(_SPEC).ref)
    state = _state(tmp_path, Runtime.REMOTE, None, handle)
    assert runtime.liveness(state) is expected


def _booting_case(
    tmp_path: Path, *, started_ago: float, thawed_ago: float | None = None
) -> bool | None:
    driver = FakeDriver()
    runtime = RemoteRuntime(
        _RUNTIMES,
        factory=lambda name, cfg: driver,
        reachable=lambda state: False,
        boot_seconds=180,
    )
    now = datetime.now(UTC)
    state = replace(
        _state(tmp_path, Runtime.REMOTE, None, RemoteHandle("gcp", driver.up(_SPEC).ref)),
        created_at=now - timedelta(seconds=started_ago),
        thawed_at=None if thawed_ago is None else now - timedelta(seconds=thawed_ago),
    )
    return runtime.liveness(state)


def test_a_workload_inside_its_boot_bound_is_not_offline_before_its_worker_dials_in(
    tmp_path: Path,
) -> None:
    """#1076: the platform says RUNNING long before a woken worker dials in. Reading
    that as OFFLINE sent a just-woken workspace to respawn, discarding its session."""
    assert _booting_case(tmp_path, started_ago=5) is True
    assert _booting_case(tmp_path, started_ago=600) is False


def test_a_thaw_or_wake_restarts_the_boot_bound(tmp_path: Path) -> None:
    assert _booting_case(tmp_path, started_ago=3600, thawed_ago=2) is True
    assert _booting_case(tmp_path, started_ago=3600, thawed_ago=600) is False
