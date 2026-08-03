"""Reading and steering a containerized agent through its own tmux.

Running the multiplexer inside the container lets the agent survive its host
client, but reads and writes still have to address the CONTAINER's tmux, not
the host's. Peek, steering and status reconciliation must all read the
agent's own pane rather than the host pane: a workspace whose host session was
gone would otherwise be persistent, invisible and unsteerable, and one whose
host session was alive would be read through a tmux *client* that keeps no
scrollback.

Everything here drives the real `WorkspaceManager` against a real git repo, the
shared fake container boundaries and the FakeTmux fixture. Two doubles carry the
external facts: `FakeDocker` answers `list-panes` the way a real tmux 3.5a did
against a real container, and `FakeTmux` records WHICH tmux server each
read/steer addressed — which is the whole assertion, since a manager that
steers the host tmux instead of the container's is exactly as green otherwise.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from grove.core import paths as core_paths
from grove.core.activity import ActivityService
from grove.core.agents import AgentActivityState
from grove.core.config import GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_tmux import CONTAINER_TMUX_ROOT, ContainerPaneLiveness
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import WorkspaceStateError
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import DEFAULT_TMUX_COMMAND, ContainerAttach, HostAttach
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus
from tests.conftest import (
    DOCKER_INSPECT_RUNNING,
    DOCKER_INSPECT_STARTED_AT,
    FAKE_REMOTE_FOLDER,
    FakeCli,
    FakePreflight,
    FakeTmux,
)

FULL_ID = "c" * 64
IMAGE = "ghcr.io/example/dev:1"
IN_CONTAINER_TMUX = f"{CONTAINER_TMUX_ROOT}/bin/amd64/tmux"

#: Real `tmux list-panes -F '#{pane_dead}|#{pane_dead_status}|#{window_activity}'`
#: output, captured inside a container: a live pane, and the same pane after its
#: agent exited 7 under `remain-on-exit`.
LIVE_PANE = "0||1785471784\n"
DEAD_PANE = "1|7|1785471399\n"


class FakeDocker:
    """The docker boundary: `inspect` for liveness, `list-panes` for the agent pane.

    Installed at `DockerCli.read_result`, the one seam every best-effort docker
    read funnels through and the same one `tests/conftest.py` neutralizes
    suite-wide — so a path that reaches docker any OTHER way shows up here as a
    real subprocess rather than as a recorded call.
    """

    def __init__(self, *, pane: str | None = LIVE_PANE) -> None:
        self.pane = pane
        self.reachable = True
        self.calls: list[list[str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
        def _read_result(
            _self: object, argv: Sequence[str]
        ) -> subprocess.CompletedProcess[str] | None:
            self.calls.append(list(argv))
            if not self.reachable:
                return None
            if "inspect" in argv:
                return subprocess.CompletedProcess(list(argv), 0, DOCKER_INSPECT_RUNNING, "")
            if "list-panes" in argv:
                if self.pane is None:
                    return subprocess.CompletedProcess(
                        list(argv), 1, "", "no server running on /tmp/tmux-1000/default\n"
                    )
                return subprocess.CompletedProcess(list(argv), 0, self.pane, "")
            return subprocess.CompletedProcess(list(argv), 0, "", "")

        monkeypatch.setattr("grove.core.container_runtime.DockerCli.read_result", _read_result)
        return self

    @property
    def pane_reads(self) -> list[list[str]]:
        return [argv for argv in self.calls if "list-panes" in argv]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return FakeDocker().install(monkeypatch)


@pytest.fixture
def unmemoized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the pane on every reconcile, for tests that move the agent.

    The memo is the production default and is asserted on its own; a test about
    an agent CHANGING state has to opt out or it is really a test of the cache.
    """
    monkeypatch.setattr(ContainerPaneLiveness, "TTL_SECONDS", 0.0)


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
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
        devcontainer_cli=FakeCli(container_id=FULL_ID),
        preflight=FakePreflight(),
    )


def _containerize(
    manager: WorkspaceManager, state: WorkspaceState, *, tmux_command: str = IN_CONTAINER_TMUX
) -> WorkspaceState:
    """Give a created workspace the identity a container create persists.

    `tmux_command` is the fact everything here turns on: empty means the image
    had no tmux and the launch composed a bare exec, so there is no
    in-container pane to read.
    """
    stored = manager.store.get(state.id)
    stored.runtime = Runtime.CONTAINER
    stored.container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref=IMAGE,
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels=ContainerRuntimeState.labels_for(
            state.id, project_slug=slugify_project(manager.repo_root)
        ),
        provisioned=True,
        # Provisioning is a fact about ONE start of a container, never about
        # the container: this is the start the captured RUNNING payload is on.
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
        tmux_command=tmux_command,
    )
    manager.store.save(stored)
    return stored


def _create(manager: WorkspaceManager, title: str) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _status(manager: WorkspaceManager, workspace_id: str) -> WorkspaceStatus:
    return next(w for w in manager.list() if w.id == workspace_id).status


DOCKER_EXEC_TMUX = ("docker", "exec", "-u", "vscode", FULL_ID, IN_CONTAINER_TMUX)


# ─── reads and steers address the agent's own pane ──────────────────────────


def test_peek_captures_the_agents_own_pane_inside_the_container(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """Not the host pane that displays it — which is a tmux CLIENT.

    Measured on a real container: the host pane repaints a viewport and keeps no
    scrollback, so peek's `-S` capture returned 22 lines of an agent's 120
    plus the in-container status bar, while the in-container pane returned all
    120 and no chrome.
    """
    state = _create(manager, "c1")
    _containerize(manager, state)
    fake_tmux.snapshots["agent"] = "the agent's own screen"

    snapshot, taken_at = manager.peek_pane(state.id)

    assert snapshot == "the agent's own screen"
    assert taken_at is not None
    assert fake_tmux.commands[-1] == ("agent", DOCKER_EXEC_TMUX)


def test_steering_types_into_the_container_not_the_host_pane(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    state = _create(manager, "c2")
    _containerize(manager, state)

    manager.send_message(state.id, "carry on")

    assert fake_tmux.sent_texts == [("agent", "carry on")]
    assert fake_tmux.commands[-1] == ("agent", DOCKER_EXEC_TMUX)


def test_the_wire_handle_says_the_pane_is_inside_a_container(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """`pane_target` is a handle a human reads; an unreachable one must not pose.

    The in-container session name is meaningless to this host's tmux, so it is
    labelled rather than rendered as something a reader could attach to.
    """
    state = _create(manager, "c3")
    _containerize(manager, state)

    target = manager.pane_target(state.id)

    assert target is not None
    assert target.startswith("container:")
    assert target.endswith(":agent")


def test_a_container_without_its_own_tmux_still_reads_the_host_pane(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """No tmux in the image means no in-container pane, so the host pane is
    read instead."""
    state = _create(manager, "c4")
    _containerize(manager, state, tmux_command="")
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "host pane"

    assert manager.peek_pane(state.id)[0] == "host pane"
    assert fake_tmux.commands[-1] == (f"{state.tmux_session}:agent", DEFAULT_TMUX_COMMAND)


# ─── the host runtime is untouched, in behaviour and in cost ────────────────


def test_a_host_workspace_reads_and_steers_exactly_as_before(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """The gate is `state.runtime`, so a host workspace constructs no reader.

    Both halves are the promise: the same host target as before, and not one
    docker invocation anywhere on the read or steer path.
    """
    state = _create(manager, "h1")
    assert state.runtime is Runtime.HOST
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "host pane"

    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE
    assert manager.peek_pane(state.id)[0] == "host pane"
    manager.send_message(state.id, "hello")

    assert manager.pane_target(state.id) == f"{state.tmux_session}:agent"
    assert {command for _, command in fake_tmux.commands} == {DEFAULT_TMUX_COMMAND}
    assert docker.calls == []


def test_a_host_workspace_reads_its_exit_record_not_a_container(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The exit recorder is still the host's answer — the seam picks per runtime."""
    state = _create(manager, "h2")
    path = core_paths.agent_exit_path(state.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("7\n", encoding="utf-8")

    exit_record = manager.agent_exit(manager.get(state.id))

    assert exit_record is not None
    assert exit_record.code == 7
    assert docker.calls == []


# ─── the host session is a viewport, not the liveness signal ───────────────


@pytest.mark.usefixtures("unmemoized")
def test_a_detached_workspace_is_not_offline_while_its_agent_lives(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """ "No host tmux session" must not mean "no agent" for a container runtime.

    OFFLINE is not a label — `ensure_can_attach`/`ensure_can_steer` refuse it,
    so treating a missing host session as the agent's own death would make a
    live, reachable agent unreachable through every verb.
    """
    state = _create(manager, "c5")
    _containerize(manager, state)
    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    fake_tmux.sessions.discard(state.tmux_session)  # the host tmux died

    assert _status(manager, state.id) is WorkspaceStatus.IDLE
    manager.send_message(state.id, "still with me?")
    assert fake_tmux.sent_texts == [("agent", "still with me?")]


@pytest.mark.usefixtures("unmemoized")
def test_a_quiet_agent_reads_idle_and_a_busy_one_active(
    manager: WorkspaceManager,
    docker: FakeDocker,
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same activity question, asked of the pane that is actually producing output."""
    state = _create(manager, "c6")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)
    monkeypatch.setattr("grove.core.tmux.time.time", lambda: 1785471784.0)

    docker.pane = "0||1785471784\n"  # activity now
    assert _status(manager, state.id) is WorkspaceStatus.ACTIVE

    docker.pane = "0||1785471000\n"  # ~13 minutes ago
    assert _status(manager, state.id) is WorkspaceStatus.IDLE


@pytest.mark.usefixtures("unmemoized")
def test_a_dead_agent_with_no_viewport_still_reads_offline(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """A live host session must not make a dead agent look alive.

    A dead pane is not something to attach to or steer — it is what `respawn`
    exists for, and respawn requires OFFLINE. The agent's own death is reported
    on the agent axis instead, separate from the host session's own liveness.
    """
    state = _create(manager, "c7")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)
    docker.pane = DEAD_PANE

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


@pytest.mark.usefixtures("unmemoized")
def test_a_vanished_in_container_session_reads_offline(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """tmux answering "no server running" IS an answer: the agent's tmux is gone."""
    state = _create(manager, "c8")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)
    docker.pane = None

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


@pytest.mark.usefixtures("unmemoized")
def test_an_unreadable_docker_never_invents_liveness(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """ "Could not tell" keeps the pre-existing answer, which here is OFFLINE."""
    state = _create(manager, "c9")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)
    docker.reachable = False

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE


def test_a_container_without_its_own_tmux_keeps_the_old_liveness_rule(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """Nothing survived its client there, so a missing host session IS the agent's."""
    state = _create(manager, "c10")
    _containerize(manager, state, tmux_command="")
    fake_tmux.sessions.discard(state.tmux_session)

    assert _status(manager, state.id) is WorkspaceStatus.OFFLINE
    assert docker.pane_reads == []


# ─── recovery: attach and respawn stay reachable ────────────────────────────


def test_attach_enters_the_containers_own_tmux(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The container's tmux owns the session, so attach execs into it.

    Nothing host-side is a multiplexer here: a host session whose pane merely
    runs `devcontainer exec … tmux attach` displays this session while CLAMPING
    every later client's terminal to its own size (measured: a client asking
    for 200x50 got 161x41).
    """
    state = _create(manager, "c11")
    _containerize(manager, state)

    instruction = manager.attach(state.id)

    assert isinstance(instruction, ContainerAttach)
    line = " ".join(instruction.argv)
    assert "devcontainer" in line
    assert f"--id-label grove.workspace={state.id}" in line
    # The in-container tmux, reattaching (`-A`) the agent's own session. It
    # rides inside the TERM-fallback wrapper `TmuxEntry` composes for every
    # interactive entry, which is why this reads the tokens rather than a prefix.
    assert IN_CONTAINER_TMUX in line
    assert "new-session -A -s agent" in line
    # The host session name appears nowhere: it is not what is being entered.
    assert state.tmux_session not in line
    assert instruction.terminal_argv() == list(instruction.argv)


def test_attach_needs_no_host_session_at_all(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """A tmux-capable container workspace never gets a host session in the
    first place, so attach must not demand one — a guard requiring a host
    session would refuse every one of them and name `respawn` as a remedy
    for a healthy workspace.
    """
    state = _create(manager, "c11b")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)

    assert isinstance(manager.attach(state.id), ContainerAttach)


def test_attach_falls_back_to_the_host_pane_when_the_image_has_no_tmux(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The one deliberately preserved exception, on the launch's own predicate.

    With `tmux_command` empty the agent really does run as a bare exec in a host
    pane, so the host session IS the thing to attach to.
    """
    state = _create(manager, "c11c")
    _containerize(manager, state, tmux_command="")

    instruction = manager.attach(state.id)

    assert isinstance(instruction, HostAttach)
    assert instruction.tmux_session == state.tmux_session


def test_respawn_still_takes_a_live_detached_workspace(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """A container's missing host session must not gate out its own remedy.

    Respawn demands OFFLINE, which this workspace's status no longer reads off
    the host session alone — so respawn takes the missing session as the
    trigger fact instead. It re-provisions on the way through, and the
    re-provisioned container here reports no tmux — so what it launches is the
    degraded host-pane arm, which is why a host session is back afterwards. A
    container that keeps its tmux gets no host session at all; see
    `tests/core/test_runtime_selection.py`.
    """
    state = _create(manager, "c12")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)

    respawned = manager.respawn(state.id)

    assert respawned.status is WorkspaceStatus.RUNNING
    assert state.tmux_session in fake_tmux.sessions


def test_respawn_still_refuses_a_healthy_workspace(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The gate is widened by a missing session, never by the runtime."""
    state = _create(manager, "c13")
    _containerize(manager, state)

    with pytest.raises(WorkspaceStateError, match="expected offline"):
        manager.respawn(state.id)


# ─── the dead-agent signal ───────────────────────────────────────────────────


def test_a_dead_pane_carries_the_agents_exit_status(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """The exit signal must survive a container's own multiplexer.

    Under an in-container tmux the host recorder captures the exit of the tmux
    CLIENT, so an agent dying inside a live session would record nothing there.
    tmux holds the fact instead — and the blend is what a user actually sees, so
    this drives `ActivityService`, not the seam alone.
    """
    del fake_tmux
    state = _create(manager, "c14")
    _containerize(manager, state)
    docker.pane = DEAD_PANE
    service = ActivityService(
        registry=RepoRegistry(cfg=manager.config, store=manager.store, config_loader=None)
    )

    reconciled = next(w for w in manager.list() if w.id == state.id)
    primary = service.sessions_for(manager, reconciled)[0]

    assert primary.activity.state is AgentActivityState.ERROR
    assert primary.activity.current_task == "agent exited with status 7"
    # …and the host recorder was never involved: under an in-container tmux it
    # only ever saw the tmux client, which is exactly why this arm exists.
    assert not core_paths.agent_exit_path(state.id).exists()


def test_a_live_agent_is_never_reported_as_exited(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """Absence keeps meaning "has not exited" on both roads (the no-flapping shape)."""
    state = _create(manager, "c15")
    _containerize(manager, state)

    assert manager.agent_exit(manager.get(state.id)) is None

    docker.reachable = False
    assert manager.agent_exit(manager.get(state.id)) is None


# ─── cost ───────────────────────────────────────────────────────────────────


def test_listing_a_healthy_container_workspace_never_reads_its_pane(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The poll path pays nothing new while a viewport exists.

    `docker exec` was measured at 60 ms against a 3.5 ms tmux fork, so the pane
    read is asked for only where the cheap signals cannot answer: reconcile
    still derives ACTIVE/IDLE from the host session it can see, and the pane
    read is what replaces the OFFLINE line beneath it.
    """
    state = _create(manager, "c16")
    _containerize(manager, state)

    for _ in range(10):
        manager.list()

    assert docker.pane_reads == []


def test_the_pane_is_read_once_per_window_however_often_the_poll_runs(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """The poll amplification bound applied to the most expensive read here."""
    state = _create(manager, "c17")
    _containerize(manager, state)
    fake_tmux.sessions.discard(state.tmux_session)

    for _ in range(10):
        manager.list()
        manager.agent_exit(manager.get(state.id))

    assert len(docker.pane_reads) == 1
