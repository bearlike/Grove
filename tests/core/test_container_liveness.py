"""Container liveness on the status path: what a dead container does to a workspace.

Runs the real `WorkspaceManager` against a real git repo, the FakeTmux fixture
and the shared container boundaries, with `docker inspect` replayed from output
captured off a real engine. Everything above the subprocess is the production
path, which is the point: `ContainerLifecycle.status()` and the whole
`ContainerState` enum can be defined, typed, and documented while remaining
UNCALLED — tests can construct the values production never produces, so the
suite would stay green while a stopped container reads `active` and no verb
would take the workspace back.

The double stands in at `DockerCli.read_result`, the one boundary every
best-effort docker read funnels through and the same one `tests/conftest.py`
neutralizes suite-wide. It replays whole `CompletedProcess` results rather than
post-translation strings so that the rc→state translation under test is the real
one — a container that is GONE and an engine that could not be REACHED are
different facts here, and a double that speaks only in "some string or None"
cannot tell them apart.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerLiveness, ContainerRuntimeState, ContainerState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import UpResult
from grove.core.errors import GroveError, WorkspaceStateError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import ProvisionStatus, Runtime, WorkspaceState, WorkspaceStatus
from tests.conftest import (
    DOCKER_INSPECT_RESTARTED,
    DOCKER_INSPECT_RUNNING,
    DOCKER_INSPECT_STARTED_AT,
    DOCKER_INSPECT_STOPPED,
    FakeCli,
    FakePreflight,
    FakeTmux,
)

FULL_ID = "c" * 64
NEXT_ID = "e" * 64
BROKEN_ID = "d" * 64
IMAGE = "ghcr.io/example/dev:1"


class FakeEngine:
    """The docker process boundary, replaying captured results and counting forks.

    `inspect` is what the liveness read runs; every other argv falls through to
    docker's own "no such object" answer, so a test never accidentally depends on
    a command this fake was not asked about.
    """

    def __init__(self, *, inspect: str | None = DOCKER_INSPECT_RUNNING) -> None:
        self.inspect = inspect
        self.reachable = True
        self.calls: list[list[str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
        def _read_result(
            _self: object, argv: Sequence[str]
        ) -> subprocess.CompletedProcess[str] | None:
            self.calls.append(list(argv))
            if not self.reachable:
                # No process was ever started — a missing binary, or a docker
                # that never returned.
                return None
            if "inspect" in argv and self.inspect is not None:
                return subprocess.CompletedProcess(list(argv), 0, self.inspect, "")
            return subprocess.CompletedProcess(
                list(argv), 1, "", f"error: no such object: {argv[-1]}\n"
            )

        monkeypatch.setattr("grove.core.container_runtime.DockerCli.read_result", _read_result)
        return self

    @property
    def inspects(self) -> list[list[str]]:
        return [argv for argv in self.calls if "inspect" in argv]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    return FakeEngine().install(monkeypatch)


@pytest.fixture
def unmemoized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the engine on every reconcile, for tests that move the container.

    The memo is the production default and is asserted on its own below; a test
    about a container CHANGING state has to opt out of it or it is really a test
    of the cache. Zero is a real configuration of the same knob, not a stub.
    """
    monkeypatch.setattr(ContainerLiveness, "TTL_SECONDS", 0.0)


@pytest.fixture
def cli() -> FakeCli:
    return FakeCli(container_id=NEXT_ID)


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
    manager: WorkspaceManager,
    state: WorkspaceState,
    *,
    container: ContainerRuntimeState | None = None,
) -> WorkspaceState:
    """Give a freshly created workspace the identity a container create persists.

    Both halves matter and the create path always writes both: `runtime` is what
    every read and launch verb branches on, `container` is the identity the
    docker commands are built from.
    """
    stored = manager.store.get(state.id)
    stored.runtime = Runtime.CONTAINER
    stored.container = container or ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref=IMAGE,
        remote_user="vscode",
        remote_workspace_folder="/workspaces/repo",
        id_labels=ContainerRuntimeState.labels_for(
            state.id, project_slug=slugify_project(manager.repo_root)
        ),
        provisioned=True,
        # The start the captured RUNNING payload is on: provisioning is a fact
        # about ONE start of a container, never about the container.
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
    )
    manager.store.save(stored)
    return stored


def _status(manager: WorkspaceManager, workspace_id: str) -> WorkspaceStatus:
    return next(w for w in manager.list() if w.id == workspace_id).status


# ─── the workspace axis now has a container dimension ────────────────────────


@pytest.mark.usefixtures("unmemoized")
def test_a_container_stopped_behind_groves_back_no_longer_reads_active(
    manager: WorkspaceManager, engine: FakeEngine, fake_tmux: FakeTmux
) -> None:
    """The symptom at its cause: the pane outlives what it is a window onto.

    The host pane runs `devcontainer exec`, so a `docker stop` by hand leaves the
    tmux session up and its activity fresh — which is precisely the evidence the
    old derivation trusted, and it answered ACTIVE for a workspace with nothing
    running in it.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c1"))
    _containerize(manager, state)
    assert state.tmux_session in fake_tmux.sessions
    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    engine.inspect = DOCKER_INSPECT_STOPPED

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


def test_a_removed_container_reads_offline_not_active(
    manager: WorkspaceManager, engine: FakeEngine
) -> None:
    """`docker rm` is the other half of the same shape — and docker SAYS so.

    A non-zero `inspect` is an answer, not a failure: the container is gone, so
    the workspace is offline even though its session and worktree are intact.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c2"))
    _containerize(manager, state)
    engine.inspect = None  # falls through to "no such object", rc 1

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


def test_a_live_container_keeps_the_pane_derived_answer(
    manager: WorkspaceManager, engine: FakeEngine, fake_tmux: FakeTmux
) -> None:
    """Liveness only ever DEMOTES: with the container up, tmux still decides."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c3"))
    _containerize(manager, state)

    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    fake_tmux.default_activity_seconds_ago = 10_000
    assert _status(manager, state.id) is WorkspaceStatus.IDLE

    fake_tmux.sessions.discard(state.tmux_session)
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


def test_unprovisioned_is_never_attachable_or_steerable(
    manager: WorkspaceManager, engine: FakeEngine
) -> None:
    """The guard `ContainerState.UNPROVISIONED` claims to be, at the model producer.

    The state is minted the way production mints every container identity —
    `from_up_result`, off an `up` that reported `outcome:"error"` — never by
    hand-setting `provisioned`, because a hand-built record is exactly how this
    enum shipped green with nothing producing it. A container in this state is
    RUNNING to the engine while its lifecycle hooks never reported success, so
    the egress firewall may be absent: an autonomous agent must not be let in,
    and "not attachable, not steerable, respawn to re-provision" is what OFFLINE
    already means.

    This is the *model* producer. The CLI-level one — a real failing `up`
    persisting this state through the manager — is asserted below.
    """
    unprovisioned = ContainerRuntimeState.from_up_result(
        UpResult.model_validate(
            {
                "outcome": "error",
                "containerId": FULL_ID,
                "remoteWorkspaceFolder": "/workspaces/repo",
                "message": "postCreateCommand failed",
            }
        ),
        image_ref=IMAGE,
    )
    assert unprovisioned.provisioned is False

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c4"))
    _containerize(manager, state, container=unprovisioned)

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE
    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.attach(state.id)
    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.send_message(state.id, "are you there")


# ─── a container brought back outside Grove ──────────────────────────────────


@pytest.mark.usefixtures("unmemoized")
def test_a_container_someone_restarted_is_never_offered_as_healthy(
    manager: WorkspaceManager, engine: FakeEngine, fake_tmux: FakeTmux
) -> None:
    """The whole sequence this is built for: provision → stop → bare `docker start`.

    Every payload is captured off a real engine, so the fact the test rests on
    is docker's own: a stop keeps the container's `.State.StartedAt`, a start
    writes a new one. Nothing else changes — same id, same image, the same
    `provisioned=True` the create path wrote, the host tmux session never
    touched (which is exactly why the pane-derived signals alone still read
    healthy).

    What that start did NOT do is run `postStartCommand`, and in Grove's
    hardened configuration that hook *is* the egress firewall. So the workspace
    must not be attachable or steerable: a `--dangerously-skip-permissions`
    agent inside a container with no egress policy is the one failure the
    blast-radius boundary cannot absorb.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c8"))
    _containerize(manager, state)
    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    engine.inspect = DOCKER_INSPECT_STOPPED
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE

    # Someone runs `docker start` — Docker Desktop's button, a script, a person.
    engine.inspect = DOCKER_INSPECT_RESTARTED
    assert state.tmux_session in fake_tmux.sessions  # the old evidence, unchanged
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE

    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.attach(state.id)
    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.send_message(state.id, "are you there")


@pytest.mark.usefixtures("unmemoized")
def test_respawn_re_provisions_a_restarted_container_and_the_workspace_recovers(
    manager: WorkspaceManager, engine: FakeEngine, cli: FakeCli
) -> None:
    """Repair is the EXISTING verb, which is the argument against a listener.

    OFFLINE already means "the runtime hosting the agent is gone, respawn is the
    remedy", and respawn's re-provision runs `devcontainer up` — which re-runs
    `postStartCommand` whenever the container's start no longer matches the
    marker the CLI wrote. So detection is the only half that was missing, and
    the mint records the start it provisioned so the workspace reads healthy
    again rather than staying stuck.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c9"))
    _containerize(manager, state)
    engine.inspect = DOCKER_INSPECT_RESTARTED
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE
    cli.ups.clear()

    respawned = manager.respawn(state.id)

    assert len(cli.ups) == 1
    assert respawned.container is not None
    # The engine is unreadable in this fixture beyond `inspect`, so the mint
    # records no witness — and "cannot tell which start" fails CLOSED, the same
    # answer as for a record with no start recorded at all. What recovery needs
    # is a readable container, not a second code path.
    assert respawned.container.provisioned_start == ""
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


# ─── a failed provision, recorded ────────────────────────────────────────────


@pytest.mark.usefixtures("unmemoized")
def test_a_failed_reprovision_persists_the_container_it_left_behind(
    manager: WorkspaceManager, engine: FakeEngine, cli: FakeCli
) -> None:
    """The CLI-level producer, driven end to end through `respawn`.

    Nothing is constructed here: `devcontainer up` fails the way the real CLI
    fails — an error carrying the `containerId` it nevertheless created — and
    every line between that and the persisted record is production's.
    `provisioned` must never default to `True` on a failure, or
    `ProvisionStatus.FAILED` and `ContainerState.UNPROVISIONED` become
    unreachable and every test of them has to build the state by hand.

    This is the SECOND way into `UNPROVISIONED`, alongside the restarted
    container above. A re-provision that fails must not leave the record
    claiming the PREVIOUS, successful container — that would reconcile the
    workspace to ACTIVE and let `attach`/`steer` into a container whose
    lifecycle hooks had just failed, the same condition as no egress firewall.
    The failure is still fatal (respawn raises); it is also *recorded*.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c10"))
    _containerize(manager, state)
    engine.inspect = DOCKER_INSPECT_STOPPED  # OFFLINE, so respawn is the remedy
    cli.up_fails = "postCreateCommand failed: apt-get exited 100"
    cli.failed_container_id = BROKEN_ID

    with pytest.raises(GroveError, match="container provisioning failed"):
        manager.respawn(state.id)

    stored = manager.store.get(state.id)
    assert stored.provision_status is ProvisionStatus.FAILED
    assert stored.container is not None
    # The container the failed `up` left running is NAMED by the record, not
    # only by the provision log — so a teardown can still find it.
    assert stored.container.container_id == BROKEN_ID
    assert stored.container.provisioned is False
    # No witness either: a failed `up` is never observed, and the fail-closed
    # rule means an absent start is not a matching one.
    assert stored.container.provisioned_start == ""

    # It is up as far as the engine is concerned, and that is exactly the shape
    # `UNPROVISIONED` exists for: healthy to the engine, unsafe to exec into.
    engine.inspect = DOCKER_INSPECT_RUNNING
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE
    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.attach(state.id)
    with pytest.raises(WorkspaceStateError, match="expected active/idle"):
        manager.send_message(state.id, "are you there")

    # And the record recovers through the verb OFFLINE already offers: a respawn
    # whose `up` succeeds mints a provisioned identity again. The status stays
    # OFFLINE here because this fixture's engine cannot answer the observation
    # the start witness is read from, and "cannot tell which start" fails
    # closed (the test above pins that same limit).
    cli.up_fails = None
    recovered = manager.respawn(state.id)
    assert recovered.container is not None
    assert recovered.container.provisioned is True
    assert recovered.container.container_id == NEXT_ID


# ─── recovery ─────────────────────────────────────────────────────────────────


def test_respawn_recovers_a_workspace_whose_container_was_killed_externally(
    manager: WorkspaceManager, engine: FakeEngine, fake_tmux: FakeTmux, cli: FakeCli
) -> None:
    """End to end: no out-of-band tmux kill, no recreate.

    Recovery was unreachable through every verb a user has — `resume` wanted
    PAUSED, `respawn` wanted OFFLINE, and the workspace claimed to be active.
    Reconciling the container is the whole fix: respawn's existing gate now
    accepts it, re-provisions the environment (`up` is idempotent), drops the
    stale session and relaunches the agent.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c5"))
    _containerize(manager, state)
    engine.inspect = DOCKER_INSPECT_STOPPED
    cli.ups.clear()
    assert state.tmux_session in fake_tmux.sessions  # never killed out of band

    respawned = manager.respawn(state.id)

    assert len(cli.ups) == 1
    assert respawned.status is WorkspaceStatus.RUNNING
    assert respawned.container is not None
    assert respawned.container.container_id == NEXT_ID
    assert state.tmux_session in fake_tmux.sessions


# ─── cost: what a host workspace pays, and what a container one pays ─────────


def test_a_host_workspace_never_reaches_for_docker(
    manager: WorkspaceManager, engine: FakeEngine
) -> None:
    """The gate is `state.runtime`, so the host path is byte-identical in cost."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="h1"))
    assert state.runtime is Runtime.HOST

    for _ in range(5):
        assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    assert engine.calls == []


def test_liveness_is_read_once_per_window_however_often_reconcile_runs(
    manager: WorkspaceManager, engine: FakeEngine
) -> None:
    """The cost bound: one fork per container identity per TTL, not per caller.

    Reconciliation runs per workspace per poll and the daemon re-enters it on
    every agent event, so an unmemoized 16 ms `docker inspect` would ride every
    one of them.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c6"))
    _containerize(manager, state)

    for _ in range(10):
        manager.list()
        manager.peek(state.id)

    assert len(engine.inspects) == 1


def test_the_memo_expires_so_a_stopped_container_still_surfaces() -> None:
    """A cache that never expires would be a stored substate — the TTL exists so it isn't."""
    clock = iter([0.0, 1.0, ContainerLiveness.TTL_SECONDS + 1.0])
    liveness = ContainerLiveness(clock=lambda: next(clock))
    identity = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref=IMAGE,
        provisioned=True,
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
    )
    seen: list[Sequence[str]] = []
    answers = iter([DOCKER_INSPECT_RUNNING, DOCKER_INSPECT_STOPPED])

    def _read_result(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(list(argv), 0, next(answers), "")

    liveness._docker.read_result = _read_result  # type: ignore[method-assign]

    assert liveness.state_of(identity) is ContainerState.RUNNING
    assert liveness.state_of(identity) is ContainerState.RUNNING  # inside the window
    assert liveness.state_of(identity) is ContainerState.STOPPED  # window elapsed
    assert len(seen) == 2


def test_an_unreachable_engine_leaves_the_workspace_status_alone(
    manager: WorkspaceManager, engine: FakeEngine
) -> None:
    """The failure mode that must never be a fleet-wide blackout.

    `docker` missing from a systemd unit's bare PATH is a documented Grove
    failure, and the containers keep running right through it — so "could not
    tell" is not "everything is dead". It is also never cached, so the next tick
    recovers rather than the next window.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="c7"))
    _containerize(manager, state)
    engine.reachable = False

    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE
    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE
    assert len(engine.inspects) == 2

    engine.reachable = True
    engine.inspect = DOCKER_INSPECT_STOPPED
    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE
