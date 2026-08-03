"""Which lifecycle verbs touch the container, and in what order.

Runs the real `WorkspaceManager` against a real git repo, the FakeTmux fixture,
and the shared in-memory container boundaries (`FakeCli` + `FakePreflight` from
conftest) — so what is pinned here is the manager's *sequencing*, including the
one verb that must do nothing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import DevcontainerConfig, UpResult
from grove.core.errors import ContainerError, GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus
from tests.conftest import FakeCli, FakePreflight, FakeTmux

FULL_ID = "c" * 64
NEXT_ID = "e" * 64


@pytest.fixture
def cli() -> FakeCli:
    """The devcontainer boundary every `up` on this path goes through."""
    return FakeCli(container_id=NEXT_ID)


@pytest.fixture
def docker_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every docker argv the lifecycle verbs emit."""
    calls: list[list[str]] = []

    def _run(self: object, argv: Sequence[str], *, action: str) -> str:
        del self, action
        calls.append(list(argv))
        return ""

    def _read(self: object, argv: Sequence[str]) -> str | None:
        del self
        calls.append(list(argv))
        return ""

    monkeypatch.setattr("grove.core.container_runtime.DockerCli.run", _run)
    monkeypatch.setattr("grove.core.container_runtime.DockerCli.read", _read)
    return calls


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, cli: FakeCli) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": False},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )


def _containerize(
    manager: WorkspaceManager, state: WorkspaceState, *, tmux_command: str = ""
) -> WorkspaceState:
    """Attach a container identity to a freshly created workspace.

    Stands in for the create path: every test in this module starts from a
    workspace that already has a persisted identity.

    *tmux_command* is an agent running under a tmux INSIDE the container, which
    is the only thing a graceful shutdown can address. It defaults to empty for
    the tests pinning behavior that predates in-container tmux.
    """
    stored = manager.store.get(state.id)
    # Both halves matter: `runtime` is what every launch verb reads to pick a
    # backend, and `container` is the identity the teardown verbs act on. A
    # record with one and not the other is not a state the create path produces.
    stored.runtime = Runtime.CONTAINER
    stored.container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref="ghcr.io/example/dev:1",
        remote_user="vscode",
        remote_workspace_folder="/workspaces/repo",
        id_labels=ContainerRuntimeState.labels_for(
            state.id, project_slug=slugify_project(manager.repo_root)
        ),
        provisioned=True,
        tmux_command=tmux_command,
    )
    manager.store.save(stored)
    return stored


def _docker_only(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in calls if argv and argv[0] == "docker"]


#: The verbs that END a container's life (or its stack's). Everything else a
#: lifecycle verb emits is a read or a graceful stop the id survives.
_DESTRUCTIVE = {"rm", "down", "kill"}


def _destructive(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in _docker_only(calls) if _DESTRUCTIVE & set(argv)]


def test_pause_stops_the_container_and_keeps_the_id(
    manager: WorkspaceManager, docker_calls: list[list[str]]
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c1"))
    _containerize(manager, state)
    docker_calls.clear()

    paused = manager.pause(state.id)

    assert _docker_only(docker_calls) == [["docker", "stop", "-t", "30", FULL_ID]]
    # The identity survives a pause untouched — the id is what resume reuses.
    # There is deliberately no persisted substate to assert on: `docker stop`
    # having been issued IS the observable, and the container's live state is a
    # read, not a field (see `test_container_runtime.py`).
    assert paused.container is not None
    assert paused.container == manager.store.get(state.id).container
    assert paused.container.container_id == FULL_ID


def test_resume_brings_the_container_back_through_devcontainer_up(
    manager: WorkspaceManager, docker_calls: list[list[str]], cli: FakeCli
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c2"))
    _containerize(manager, state)
    manager.pause(state.id)
    docker_calls.clear()
    cli.ups.clear()

    resumed = manager.resume(state.id)

    assert len(cli.ups) == 1
    # A raw `docker start` would skip postStartCommand — the egress firewall.
    assert all("start" not in argv for argv in _docker_only(docker_calls))
    # The override config is regenerated, not reused: pause deleted the worktree
    # it lives in, so the persisted path from the create `up` no longer exists.
    override = cli.ups[0]["override_config"]
    assert override is not None
    assert Path(override).is_file()
    assert resumed.container is not None
    assert resumed.container.container_id == NEXT_ID
    assert resumed.status == WorkspaceStatus.RUNNING


def test_respawn_never_destroys_the_container_it_re_attaches_to(
    manager: WorkspaceManager, fake_tmux: FakeTmux, docker_calls: list[list[str]], cli: FakeCli
) -> None:
    """Respawn restarts the AGENT; the environment is re-attached, never rebuilt.

    It cannot be a pure no-op — the tmux session vanishing is exactly the shape a
    host reboot has, and then the container is down too and there is nothing to
    exec into. `devcontainer up` is idempotent and re-attaches by id-label, so
    recovery costs a resolve when the container is healthy. What must never
    happen is a destructive verb: fixing a dead pane by removing a working
    container would throw away the agent's whole environment.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c3"))
    containerized = _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)  # session vanished → OFFLINE
    docker_calls.clear()
    cli.ups.clear()

    manager.respawn(state.id)

    assert _destructive(docker_calls) == []
    # `up` can only ever find THIS workspace's container: it is addressed by the
    # same id-labels the create path stamped, never by a name or a bare id.
    assert cli.ups[0]["id_labels"] == ContainerRuntimeState.labels_for(
        state.id, project_slug=slugify_project(manager.repo_root)
    )
    assert containerized.container is not None


def test_kill_is_tmux_first_then_container_then_worktree(
    manager: WorkspaceManager, fake_tmux: FakeTmux, docker_calls: list[list[str]]
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c4"))
    _containerize(manager, state)
    docker_calls.clear()
    worktree = Path(state.worktree_path)

    manager.kill(state.id, delete_branch=False)

    assert state.tmux_session not in fake_tmux.sessions
    assert _docker_only(docker_calls) == [["docker", "rm", "-f", FULL_ID]]
    assert not worktree.exists()


# ─── the agent's own shutdown ─────────────────────────────────────────────────

_IN_TMUX = "/grove/tmux/bin/amd64/tmux"


def _shutdown_execs(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in _docker_only(calls) if argv[1:2] == ["exec"]]


@pytest.mark.parametrize("verb", ["pause", "kill"])
def test_pause_and_kill_signal_the_agent_in_its_own_namespace_first(
    manager: WorkspaceManager, docker_calls: list[list[str]], verb: str
) -> None:
    """Killing the HOST session does not stop the agent — it only closes the viewport.

    The agent lives under a tmux inside the container, so the only thing that
    can address it is an exec into that namespace. The session name comes from
    the cascade (`container.tmux.session`), which is why the manager supplies
    it: a name baked into `container_runtime` would be policy in code, and the
    shutdown has to address exactly what the launch created.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title=f"c-{verb}"))
    _containerize(manager, state, tmux_command=_IN_TMUX)
    docker_calls.clear()

    getattr(manager, verb)(state.id)

    docker = _docker_only(docker_calls)
    assert docker[0][:2] == ["docker", "exec"], "the agent is signalled before anything else"
    assert _IN_TMUX in docker[0]
    assert manager.config.container.tmux.session in docker[0]
    # The destructive verb still runs, and still runs SECOND.
    assert docker[1][1] in {"stop", "rm"}


def test_a_container_with_no_tmux_is_stopped_with_no_extra_exec(
    manager: WorkspaceManager, docker_calls: list[list[str]]
) -> None:
    """No in-container tmux means nothing can name the agent — and no extra fork."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c-bare"))
    _containerize(manager, state)
    docker_calls.clear()

    manager.pause(state.id)

    assert _shutdown_execs(docker_calls) == []
    assert _docker_only(docker_calls) == [["docker", "stop", "-t", "30", FULL_ID]]


def test_a_kill_whose_teardown_failed_keeps_the_record_that_names_the_container(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record is the last thing on this host that can name a live container.

    Deleting it on a failed teardown leaves a running container nothing can
    ever find again — no verb, and with the orphan sweep unwired, no sweep
    either. Every other stage of `kill` may fail toward something visible on
    disk; this one cannot, so it is the one failure that raises.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c-stuck"))
    _containerize(manager, state)

    def _boom(self: object, argv: Sequence[str], *, action: str) -> str:
        del self, argv
        raise ContainerError(f"failed to {action}: device or resource busy")

    monkeypatch.setattr("grove.core.container_runtime.DockerCli.run", _boom)

    with pytest.raises(GroveError, match="record was KEPT"):
        manager.kill(state.id, delete_branch=False)

    kept = manager.store.get(state.id)
    assert kept.status is WorkspaceStatus.ERROR
    assert kept.error_detail is not None
    assert "container teardown failed" in kept.error_detail
    assert kept.container is not None
    assert kept.container.container_id == FULL_ID


def test_a_kill_whose_teardown_succeeded_still_forgets_the_workspace(
    manager: WorkspaceManager, docker_calls: list[list[str]]
) -> None:
    """The guard above must not turn every kill into a record that lingers."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c-clean"))
    _containerize(manager, state)
    del docker_calls

    manager.kill(state.id, delete_branch=False)

    assert [ws.id for ws in manager.list()] == []


#: The per-container volume the docker-in-docker feature mints, and the shared
#: cache declared beside it — the pair every ownership assertion below turns on.
_DIND = f"dind-var-lib-docker-{'z' * 52}"
_SHARED = "devc-repo-uv"
#: A real compose project name's shape: the CLI derives it from the worktree
#: basename plus `_devcontainer`, so Grove never gets to choose it.
_PROJECT = "repo-20260730-194640_devcontainer"


class ComposeCli(FakeCli):
    """A `devcontainer up` that reports a compose project, as a compose config does."""

    def up(self, *args: object, **kwargs: object) -> UpResult:
        result = super().up(*args, **kwargs)  # type: ignore[arg-type]
        return result.model_copy(update={"compose_project_name": _PROJECT})


def _project_config(*mounts: object) -> DevcontainerConfig:
    return DevcontainerConfig.model_validate(
        {"name": "repo", "image": "ghcr.io/example/dev:1", "mounts": list(mounts)}
    )


#: The docker-in-docker feature's own mount entry, exactly as
#: `read-configuration --include-merged-configuration` hands it over: the token
#: is UNSUBSTITUTED until `up` runs, which is what makes lifetime answerable.
_DIND_MOUNT = {
    "source": "dind-var-lib-docker-${devcontainerId}",
    "target": "/var/lib/docker",
    "type": "volume",
}


def _spy_docker(
    monkeypatch: pytest.MonkeyPatch, *, mounts: list[dict[str, object]], labels: dict[str, str]
) -> list[list[str]]:
    """Record every docker argv, and answer the mint's one inspect for real."""
    calls: list[list[str]] = []

    def _run(self: object, argv: Sequence[str], *, action: str) -> str:
        del self, action
        calls.append(list(argv))
        return ""

    def _read(self: object, argv: Sequence[str]) -> str | None:
        del self
        calls.append(list(argv))
        if not any("json .Mounts" in arg for arg in argv):
            return ""
        return json.dumps({"labels": labels, "mounts": mounts, "image": "vsc-repo-9f2a-uid"})

    monkeypatch.setattr("grove.core.container_runtime.DockerCli.run", _run)
    monkeypatch.setattr("grove.core.container_runtime.DockerCli.read", _read)
    return calls


def _container_manager(
    tmp_repo: Path, tmp_path: Path, cli: FakeCli, *, name: str = "state.json"
) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
        }
    )
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / name),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )


def test_a_container_create_records_the_volume_it_owns_and_kill_removes_it(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Producer → store → teardown, on the real create path.

    The other tests here start from a hand-built identity, which would leave
    `owned_volumes` untested if it silently stayed empty in production. This
    one drives a real container create so the field is filled by the code that
    actually mints it, survives the store, and reaches `docker volume rm` — and
    so the shared cache mounted beside it is provably never named.
    """
    del fake_tmux
    calls = _spy_docker(
        monkeypatch,
        mounts=[
            {"Type": "bind", "Source": str(tmp_repo), "Destination": "/workspaces/repo"},
            {"Type": "volume", "Name": _DIND},
            {"Type": "volume", "Name": _SHARED},
        ],
        labels={"grove.managed": "1"},
    )
    manager = _container_manager(
        tmp_repo,
        tmp_path,
        FakeCli(
            container_id=NEXT_ID,
            config=_project_config(_DIND_MOUNT, f"source={_SHARED},target=/caches/uv,type=volume"),
        ),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c7"))
    stored = JsonWorkspaceStore(path=manager.store.path).get(state.id)
    assert stored.container is not None
    assert stored.container.owned_volumes == [_DIND]
    # `up` never reports the image; only the mint's own read can fill it.
    assert stored.container.image_ref == "vsc-repo-9f2a-uid"

    calls.clear()
    manager.kill(state.id, delete_branch=False)
    assert _docker_only(calls) == [
        ["docker", "rm", "-f", NEXT_ID],
        ["docker", "volume", "rm", _DIND],
    ]
    assert not any(_SHARED in argv for argv in calls)


def test_a_compose_create_records_the_stack_and_kill_takes_the_whole_thing_down(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose stack teardown, end to end on the real path.

    A name-pattern ownership test (a `grove-` prefix Grove is never in a
    position to satisfy) would leave `kill` emitting one `docker rm -f` for the
    primary service while the stack's siblings, its network and its volumes
    all survive — a `mongo-1` with `restart: unless-stopped` most visibly.
    What proves the fix is the SHAPE of the teardown command, not its absence:
    `compose down` reaches the whole stack, and never carries `-v`.
    """
    del fake_tmux
    calls = _spy_docker(
        monkeypatch,
        mounts=[
            {"Type": "volume", "Name": f"{_PROJECT}_{_DIND}"},
            {"Type": "volume", "Name": f"{_PROJECT}_mongo-data"},
        ],
        labels={"grove.managed": "1", "com.docker.compose.project": _PROJECT},
    )
    manager = _container_manager(
        tmp_repo,
        tmp_path,
        ComposeCli(container_id=NEXT_ID, config=_project_config(_DIND_MOUNT)),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c8"))
    stored = JsonWorkspaceStore(path=manager.store.path).get(state.id)
    assert stored.container is not None
    assert stored.container.compose_project == _PROJECT
    assert stored.container.compose_owned is True
    # Compose names a stack's volumes `<project>_<name>`, so the feature volume
    # is recognised only because the project is known — and the database that
    # sits right beside it under the same prefix still is not ours.
    assert stored.container.owned_volumes == [f"{_PROJECT}_{_DIND}"]

    calls.clear()
    manager.kill(state.id, delete_branch=False)
    assert _docker_only(calls) == [
        ["docker", "compose", "-p", _PROJECT, "down", "--remove-orphans"],
        ["docker", "volume", "rm", f"{_PROJECT}_{_DIND}"],
    ]
    assert not any("mongo-data" in arg for argv in calls for arg in argv)
    assert not any("-v" in argv or "--volumes" in argv for argv in calls)


def test_a_compose_stack_grove_cannot_vouch_for_is_never_named_by_project(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honest "cannot tell": tear down what the labels reach, claim nothing more.

    The container's own label disagrees with what `up` reported, so the stack is
    not provably Grove's. Teardown must fall back rather than aim a `compose
    down` at somebody else's project — and must not quietly claim the stack's
    volumes either.
    """
    del fake_tmux
    calls = _spy_docker(
        monkeypatch,
        mounts=[{"Type": "volume", "Name": f"{_PROJECT}_{_DIND}"}],
        labels={"grove.managed": "1", "com.docker.compose.project": "somebody-elses-stack"},
    )
    manager = _container_manager(
        tmp_repo,
        tmp_path,
        ComposeCli(container_id=NEXT_ID, config=_project_config(_DIND_MOUNT)),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c9"))
    stored = JsonWorkspaceStore(path=manager.store.path).get(state.id)
    assert stored.container is not None
    assert stored.container.is_compose is True, "the mode is not in doubt, only the ownership"
    assert stored.container.compose_owned is False
    assert stored.container.owned_volumes == []

    calls.clear()
    manager.kill(state.id, delete_branch=False)
    assert not any("compose" in argv for argv in _docker_only(calls))
    assert not any("volume" in argv for argv in _docker_only(calls))


def test_host_mode_workspaces_never_invoke_docker(
    manager: WorkspaceManager, docker_calls: list[list[str]], cli: FakeCli
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c5"))
    manager.pause(state.id)
    manager.resume(state.id)
    manager.kill(state.id, delete_branch=False)
    assert docker_calls == []
    assert cli.ups == []


def test_container_identity_round_trips_through_the_store(
    manager: WorkspaceManager, docker_calls: list[list[str]]
) -> None:
    """A container id that is not on disk is a container nothing can ever find."""
    del docker_calls
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c6"))
    _containerize(manager, state)

    reloaded = JsonWorkspaceStore(path=manager.store.path).get(state.id)
    assert reloaded.container is not None
    assert reloaded.container.container_id == FULL_ID
    assert reloaded.container.id_labels["grove.managed"] == "1"
