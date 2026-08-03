"""Several agents inside ONE container.

The container's tmux server already hosts two sessions (the agent's and `grove
shell`'s), so adding more is a selector question rather than a new mechanism —
and the design decision worth pinning is what it does NOT do: no persisted field,
no plural `agent_session_id`, no second workspace record. Every test here drives
the real `WorkspaceManager` against a real git repo and the shared fake
boundaries; the doubles replay payloads captured from real tools rather than
from production's model of them.

The host-workspace tests are the byte-identical guard: nothing below may change
what a workspace without a container does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_agent import ContainerAgent, ContainerAgentEntry
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_tmux import CONTAINER_TMUX_ROOT, ContainerTmux, TmuxEntry
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import (
    AgentSessionNotFound,
    CapabilityUnavailable,
    ContainerError,
    GroveError,
)
from grove.core.manager import WorkspaceManager
from grove.core.phase import PhaseFile
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import DEFAULT_TMUX_COMMAND, SessionReport
from grove.core.workspace import Runtime, WorkspaceState
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
DOCKER_EXEC_TMUX = ("docker", "exec", "-u", "vscode", FULL_ID, IN_CONTAINER_TMUX)

#: Real `tmux list-sessions -F '#{session_name}|#{session_attached}|#{session_activity}'`
#: output, captured from a real tmux 3.4 running three sessions named
#: exactly as Grove names them. `session_attached` is a client COUNT, not a
#: boolean — a hand-written double would plausibly have written `false`.
THREE_SESSIONS = "agent|0|1785478058\nagent-2|0|1785478058\nshell|0|1785478058\n"
ONE_SESSION = "agent|1|1785478058\n"

#: What tmux says when there is no server at all, alongside a non-zero exit.
NO_SERVER = "no server running on /tmp/tmux-1000/default\n"


class FakeDocker:
    """The docker boundary: `inspect` for liveness, `list-sessions` / `kill-session`.

    Installed at `DockerCli.read_result`, the one seam every best-effort docker
    read funnels through — so a path reaching docker any OTHER way shows up as a
    real subprocess rather than as a recorded call.
    """

    def __init__(self, *, sessions: str | None = THREE_SESSIONS) -> None:
        self.sessions = sessions
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
            if "list-sessions" in argv:
                if self.sessions is None:
                    return subprocess.CompletedProcess(list(argv), 1, "", NO_SERVER)
                return subprocess.CompletedProcess(list(argv), 0, self.sessions, "")
            if "list-panes" in argv:
                return subprocess.CompletedProcess(list(argv), 0, "0||1785478058\n", "")
            return subprocess.CompletedProcess(list(argv), 0, "", "")

        monkeypatch.setattr("grove.core.container_runtime.DockerCli.read_result", _read_result)
        return self

    def argvs(self, verb: str) -> list[list[str]]:
        return [argv for argv in self.calls if verb in argv]


class StartingCli(FakeCli):
    """A `FakeCli` whose exec REGISTERS the session it was asked to start.

    Without this the double answers with a simpler world than the real
    boundary — a start that never changes what the next `list-sessions` reports
    — so the suite would exercise a program production does not run, hiding
    the measured fact that `new-session -d` exits 0 for a command that cannot
    run. `starts=False` is that real failure, and it is what a cross-kind add
    against an image missing the binary actually does.
    """

    def __init__(self, docker: FakeDocker, *, starts: bool = True) -> None:
        super().__init__(container_id=FULL_ID)
        self._docker = docker
        self.starts = starts

    def exec(
        self, workspace_folder: Path, argv: Sequence[str], **kwargs: object
    ) -> tuple[int, str]:
        code, out = super().exec(workspace_folder, argv, **kwargs)  # type: ignore[arg-type]
        tokens = list(argv)
        if self.starts and "new-session" in tokens and "-s" in tokens:
            name = tokens[tokens.index("-s") + 1]
            if self._docker.sessions is not None:
                self._docker.sessions += f"{name}|0|1785478058\n"
        return code, out


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    return FakeDocker().install(monkeypatch)


@pytest.fixture
def cli(docker: FakeDocker) -> StartingCli:
    return StartingCli(docker)


@pytest.fixture
def manager(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, cli: StartingCli
) -> WorkspaceManager:
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


def _create(manager: WorkspaceManager, title: str) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _containerize(
    manager: WorkspaceManager, state: WorkspaceState, *, tmux_command: str = IN_CONTAINER_TMUX
) -> WorkspaceState:
    """Give a created workspace the identity a container create persists."""
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
        # The start the captured RUNNING payload is on: provisioning is a
        # fact about ONE start of a container, never about the container.
        provisioned_start=DOCKER_INSPECT_STARTED_AT,
        tmux_command=tmux_command,
    )
    manager.store.save(stored)
    return stored


# ─── the payload parse (pinned to real tmux output) ─────────────────────────


def test_session_report_parses_a_real_multi_session_listing() -> None:
    """`session_attached` is a client COUNT, and the name may not be last."""
    reports = SessionReport.parse_all(THREE_SESSIONS)

    assert [report.name for report in reports] == ["agent", "agent-2", "shell"]
    assert all(not report.attached for report in reports)
    assert reports[0].activity_epoch == 1785478058
    assert SessionReport.parse_all(ONE_SESSION)[0].attached is True


def test_session_report_keeps_a_name_that_contains_the_separator() -> None:
    """The format's own two separators are the LAST two, so the name keeps the rest."""
    assert SessionReport.parse_all("a|b|0|17\n")[0].name == "a|b"


def test_session_report_on_no_server_is_empty_not_a_crash() -> None:
    assert SessionReport.parse_all("") == ()


# ─── naming is validated, because a name IS a tmux target ───────────────────


@pytest.mark.parametrize("bad", ["agent:1", "agent.2", "", "  ", "-lead", "a" * 65])
def test_an_agent_name_that_would_address_something_else_is_refused(bad: str) -> None:
    """`:` and `.` are tmux target syntax — a value that becomes syntax if unescaped."""
    with pytest.raises(GroveError, match="invalid agent name"):
        ContainerAgent.validate_name(bad)


def test_the_minted_name_skips_what_is_already_running() -> None:
    assert ContainerAgent.mint_name(None, base="agent", taken=frozenset({"agent"})) == "agent-2"
    assert (
        ContainerAgent.mint_name(None, base="agent", taken=frozenset({"agent", "agent-2"}))
        == "agent-3"
    )


def test_a_requested_name_that_is_already_running_is_refused_not_reattached() -> None:
    """`-A` would attach to the live agent and report success — which is not a second agent."""
    with pytest.raises(GroveError, match="already running"):
        ContainerAgent.mint_name("agent-2", base="agent", taken=frozenset({"agent-2"}))


def test_the_roster_excludes_the_shell_and_puts_the_primary_first() -> None:
    roster = ContainerAgent.roster(
        SessionReport.parse_all(THREE_SESSIONS), primary="agent", shell="shell"
    )

    assert [(a.name, a.primary) for a in roster] == [("agent", True), ("agent-2", False)]


# ─── entry composition ──────────────────────────────────────────────────────


def test_a_detached_start_passes_minus_d_and_drops_the_term_fallback() -> None:
    """A detached start has no client, so the whole TERM-refusal mechanism is inert."""
    entry = TmuxEntry(
        command="/t", session="agent-2", term_fallback="xterm-256color", detached=True
    )

    assert entry.tokens(("claude",)) == ["/t", "new-session", "-A", "-d", "-s", "agent-2", "claude"]


def test_an_attached_entry_still_wraps_in_the_measured_fallback_script() -> None:
    entry = TmuxEntry(command="/t", session="agent-2", term_fallback="xterm-256color")

    tokens = entry.tokens(())

    assert tokens[0] == "sh"
    assert "has-session -t agent-2" in tokens[2]


def test_an_empty_target_composes_a_bare_attach() -> None:
    """A caller that already proved the session exists must not re-derive a command."""
    entry = TmuxEntry(command="/t", session="agent-2")

    assert entry.tokens(()) == ["/t", "new-session", "-A", "-s", "agent-2"]


def test_the_agent_script_is_the_one_the_primary_launch_uses(tmp_path: Path) -> None:
    """Shared so the nested-cwd `cd` and the `"$@"` hand-off cannot drift."""
    container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref=IMAGE,
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels={},
    )
    flat = ContainerAgentEntry.script_for(
        container, worktree=tmp_path, cwd=tmp_path, command="claude"
    )
    nested = ContainerAgentEntry.script_for(
        container, worktree=tmp_path, cwd=tmp_path / "api", command="claude"
    )

    assert flat == 'exec claude "$@"'
    assert nested == f'cd {FAKE_REMOTE_FOLDER}/api && exec claude "$@"'


# ─── the verbs, through the real manager ────────────────────────────────────


def test_listing_reads_the_container_and_flags_the_workspaces_own_agent(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    state = _create(manager, "m1")
    _containerize(manager, state)

    agents = manager.container_agents(state.id)

    assert [(a.name, a.primary) for a in agents] == [("agent", True), ("agent-2", False)]
    assert docker.argvs("list-sessions")[0][:6] == list(DOCKER_EXEC_TMUX)


def test_listing_raises_rather_than_reporting_an_empty_roster_when_docker_cannot_run(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """ "Cannot tell" must never be spelled as an answer."""
    state = _create(manager, "m2")
    _containerize(manager, state)
    docker.reachable = False

    with pytest.raises(ContainerError, match="could not read the tmux server"):
        manager.container_agents(state.id)


def test_adding_an_agent_starts_it_detached_in_its_own_session(
    manager: WorkspaceManager, docker: FakeDocker, cli: StartingCli
) -> None:
    state = _create(manager, "m3")
    _containerize(manager, state)

    added = manager.add_container_agent(state.id)

    assert added.name == "agent-3"  # agent + agent-2 are already running
    argv = cli.execs[-1]["argv"]
    assert argv[:6] == [IN_CONTAINER_TMUX, "new-session", "-A", "-d", "-s", "agent-3"]
    assert argv[6:8] == ["sh", "-c"]
    # The launch script, behind the `remain-on-exit` prefix every in-container
    # session start now carries (see the test on that below).
    assert argv[8].endswith('exec claude "$@"')


def test_an_added_agent_carries_the_same_launch_decoration_as_the_workspaces_own(
    manager: WorkspaceManager, docker: FakeDocker, cli: StartingCli
) -> None:
    """Composed through `_compose_launch`, so it is configured identically."""
    state = _create(manager, "m4")
    _containerize(manager, state)

    manager.add_container_agent(state.id, model="opus", initial_prompt="go")

    argv = cli.execs[-1]["argv"]
    assert "--session-id" in argv
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[-1] == "go"
    # A FRESH session id — an added agent is a new conversation, never a second
    # pointer at the workspace's own.
    assert argv[argv.index("--session-id") + 1] != state.agent_session_id


def test_an_added_agent_crosses_under_the_same_hermetic_env(
    manager: WorkspaceManager, docker: FakeDocker, cli: StartingCli
) -> None:
    """An exec has no other way to carry env; a miss would blank its transcript.

    Same env as the workspace's own agent in every field but ONE: the phase file
    is per AGENT, so the added agent is handed its own — that is the only
    licensed difference, and asserting equality on the rest is what keeps the
    two launch roads from drifting anywhere else.
    """
    state = _create(manager, "m5")
    _containerize(manager, state)
    stored = manager.store.get(state.id)

    slot = manager.add_container_agent(state.id).name

    spec = manager.config.find_agent("claude")
    assert spec is not None
    assert cli.execs[-1]["remote_env"] == manager._launch_env(stored, spec, agent_slot=slot)
    own = manager._launch_env(stored, spec)
    assert cli.execs[-1]["remote_env"][PhaseFile.PATH_ENV] != own[PhaseFile.PATH_ENV]


def test_an_added_agents_death_is_as_inspectable_as_the_primary_ones(
    manager: WorkspaceManager, docker: FakeDocker, cli: StartingCli
) -> None:
    """`remain-on-exit` is composed onto `ContainerAgentEntry`, not the launch
    backend, so EVERY in-container session start carries it — an added agent
    that dies gets the same exit status and the same last screen as the
    workspace's own, for free.

    Keeping a corpse around is safe because only the primary session is ever
    relaunched under its own name (`_clear_dead_agent_session`), and
    `mint_name` walks past a name the container still reports rather than
    trying to `-A` into a dead one.
    """
    state = _create(manager, "m6")
    _containerize(manager, state)

    manager.add_container_agent(state.id)

    script = cli.execs[-1]["argv"][8]
    assert f"{IN_CONTAINER_TMUX} set-option -w remain-on-exit on" in script
    assert script.index("remain-on-exit") < script.index("exec claude")


def test_a_named_agent_may_not_collide_with_the_shell_session(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    state = _create(manager, "m7")
    _containerize(manager, state)

    with pytest.raises(GroveError, match="already running"):
        manager.add_container_agent(state.id, name="shell")


def test_a_failed_start_is_loud_and_names_what_the_container_said(
    manager: WorkspaceManager, docker: FakeDocker, monkeypatch: pytest.MonkeyPatch, cli: StartingCli
) -> None:
    state = _create(manager, "m8")
    _containerize(manager, state)
    monkeypatch.setattr(type(cli), "exec", lambda *a, **k: (127, "tmux: not found"))

    with pytest.raises(ContainerError, match="tmux: not found"):
        manager.add_container_agent(state.id)


def test_an_agent_that_could_not_start_is_reported_as_failed_not_started(
    manager: WorkspaceManager, docker: FakeDocker, cli: StartingCli
) -> None:
    """`new-session -d` exits 0 when the command inside cannot run — measured.

    Real behaviour on a container: tmux creates the session, the pane
    process fails, and the session is already gone by the next command, while
    the exit status stays 0. Reporting off that status hands the user a name
    that addresses nothing — the ordinary cross-kind case (e.g. adding a codex
    agent to an image that ships no codex), not an edge case.
    """
    state = _create(manager, "m15")
    _containerize(manager, state)
    cli.starts = False  # tmux said 0; the pane process could not run

    with pytest.raises(ContainerError, match="did not stay running"):
        manager.add_container_agent(state.id)


def test_killing_an_extra_agent_ends_only_its_session(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    state = _create(manager, "m9")
    _containerize(manager, state)

    manager.kill_container_agent(state.id, "agent-2")

    assert docker.argvs("kill-session")[-1][-3:] == ["kill-session", "-t", "agent-2"]


def test_killing_the_workspaces_own_agent_through_this_verb_is_refused(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """It has lifecycle verbs of its own that also deal with the container and record."""
    state = _create(manager, "m10")
    _containerize(manager, state)

    with pytest.raises(CapabilityUnavailable, match="pause, respawn or kill"):
        manager.kill_container_agent(state.id, "agent")
    assert docker.argvs("kill-session") == []


def test_peeking_a_named_agent_captures_that_agents_pane(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    state = _create(manager, "m11")
    _containerize(manager, state)
    fake_tmux.snapshots["agent-2"] = "the second agent's screen"

    snapshot, _ = manager.peek_pane(state.id, agent="agent-2")

    assert snapshot == "the second agent's screen"
    assert fake_tmux.commands[-1] == ("agent-2", DOCKER_EXEC_TMUX)


def test_steering_a_named_agent_types_into_that_agent_only(
    manager: WorkspaceManager, docker: FakeDocker, fake_tmux: FakeTmux
) -> None:
    """The whole point of naming one: never silently pick one and pretend."""
    state = _create(manager, "m12")
    _containerize(manager, state)

    manager.send_message(state.id, "run the tests", agent="agent-2")

    assert fake_tmux.sent_texts[-1] == ("agent-2", "run the tests")
    assert fake_tmux.commands[-1] == ("agent-2", DOCKER_EXEC_TMUX)


def test_attaching_composes_a_bare_reattach_onto_a_running_agent(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    state = _create(manager, "m13")
    _containerize(manager, state)

    argv = manager.container_agent_argv(state.id, "agent-2")

    assert argv[0] == "devcontainer"
    assert "--" in argv
    tail = argv[argv.index("--") + 1 :]
    assert "agent-2" in " ".join(tail)
    assert "new-session" in " ".join(tail)


def test_attaching_to_an_agent_that_has_ended_says_so_rather_than_starting_one(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    state = _create(manager, "m14")
    _containerize(manager, state)
    docker.sessions = ONE_SESSION

    with pytest.raises(AgentSessionNotFound, match="no agent 'agent-2' is running"):
        manager.container_agent_argv(state.id, "agent-2")


# ─── a host workspace is untouched, and says why ────────────────────────────


def test_a_host_workspace_refuses_every_multi_agent_verb_with_the_remedy(
    manager: WorkspaceManager,
) -> None:
    """One host workspace hosts one agent; another agent is another workspace."""
    state = _create(manager, "h1")

    for call in (
        lambda: manager.container_agents(state.id),
        lambda: manager.add_container_agent(state.id),
        lambda: manager.kill_container_agent(state.id, "agent-2"),
        lambda: manager.container_agent_argv(state.id, "agent-2"),
        lambda: manager.peek_pane(state.id, agent="agent-2"),
        lambda: manager.send_message(state.id, "hi", agent="agent-2"),
    ):
        with pytest.raises(CapabilityUnavailable, match="runs on the host"):
            call()


def test_a_host_workspaces_own_peek_and_steer_are_byte_identical(
    manager: WorkspaceManager, fake_tmux: FakeTmux
) -> None:
    """The selector defaults to empty, so nothing about a host workspace moved."""
    state = _create(manager, "h2")
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "host pane"

    snapshot, _ = manager.peek_pane(state.id)
    manager.send_message(state.id, "carry on")

    assert snapshot == "host pane"
    assert fake_tmux.sent_texts[-1] == (f"{state.tmux_session}:agent", "carry on")
    assert fake_tmux.commands[-1] == (f"{state.tmux_session}:agent", DEFAULT_TMUX_COMMAND)


def test_a_container_with_no_reachable_tmux_refuses_and_names_the_remedy(
    manager: WorkspaceManager, docker: FakeDocker
) -> None:
    """The honest degradation: the workspace works, it just hosts one agent."""
    state = _create(manager, "h3")
    _containerize(manager, state, tmux_command="")

    with pytest.raises(CapabilityUnavailable, match="no reachable tmux"):
        manager.add_container_agent(state.id)


def test_the_reader_addresses_whichever_session_it_was_built_for() -> None:
    """One reader class, one selector — which is why N agents needed no second one."""
    container = ContainerRuntimeState(
        container_id=FULL_ID,
        image_ref=IMAGE,
        remote_user="vscode",
        remote_workspace_folder=FAKE_REMOTE_FOLDER,
        id_labels={},
        tmux_command=IN_CONTAINER_TMUX,
    )
    cfg = GroveConfig().container

    default = ContainerTmux.for_container(container, cfg=cfg)
    named = ContainerTmux.for_container(container, cfg=cfg, session="agent-2")

    assert default is not None and named is not None
    assert default.pane.target == "agent"
    assert named.pane.target == "agent-2"
    assert named.pane.command == default.pane.command
    assert named.pane.display.endswith(":agent-2")
