"""An interactive shell INSIDE the container.

Two layers, the same split ``test_container_tmux.py`` uses:

* the pure COMPOSITION — what ``devcontainer exec … --`` actually ends with,
  including the degradation when the container has no tmux at all,
* the WIRING through the real manager, because window 0 becoming an
  in-container shell is a PRODUCER question, and a member defined, typed and
  tested but never called is a recurring failure shape for this subsystem
  (``core/CLAUDE.md``). The layout fake records ``shell_command`` for exactly
  that reason: a fake that swallowed it would keep every test green with the
  producer unwired.

No Docker, no devcontainer CLI, no network — the shared fake boundaries only.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_shell import ContainerShell
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState
from tests.conftest import FAKE_REMOTE_FOLDER, FakeCli, FakePreflight, FakeTmux

_TMUX = "/grove/tmux/bin/amd64/tmux"

#: TERM fallback OFF, so a test about the invocation reads the invocation
#: rather than the wrapper around it.
_NO_FALLBACK = GroveConfig.model_validate({"container": {"tmux": {"term_fallback": ""}}})


def _shell(
    tmp_path: Path,
    *,
    tmux_command: str = _TMUX,
    subpath: str = "",
    cfg: GroveConfig | None = None,
) -> ContainerShell:
    worktree = tmp_path / "wt"
    cwd = worktree / subpath if subpath else worktree
    cwd.mkdir(parents=True, exist_ok=True)
    return ContainerShell(
        container=ContainerRuntimeState(
            container_id="c" * 64,
            remote_workspace_folder=FAKE_REMOTE_FOLDER,
            id_labels={"grove.workspace": "ws1"},
            override_config_path=str(tmp_path / "override.json"),
            provisioned=True,
            tmux_command=tmux_command,
        ),
        cfg=cfg if cfg is not None else _NO_FALLBACK,
        worktree=worktree,
        cwd=cwd,
        cli=FakeCli(),
    )


# ─── composition ────────────────────────────────────────────────────────────


def test_the_shell_runs_under_its_own_persistent_in_container_session(
    tmp_path: Path,
) -> None:
    """`-A` is what makes leaving and coming back the SAME shell, not a new one."""
    argv = _shell(tmp_path).argv()

    assert argv[0] == "devcontainer"
    assert argv[1] == "exec"
    entry = argv[argv.index("--") + 1 :]
    assert entry[:5] == [_TMUX, "new-session", "-A", "-s", "shell"]


def test_the_shell_session_is_not_the_agents(tmp_path: Path) -> None:
    """One in-container server, two sessions — sharing the name would drop the
    user straight into the agent's own pane instead of a shell."""
    cfg = GroveConfig.model_validate({"container": {"tmux": {"term_fallback": ""}}})

    argv = _shell(tmp_path, cfg=cfg).argv()

    assert "-s" in argv
    assert argv[argv.index("-s") + 1] == "shell"
    assert argv[argv.index("-s") + 1] != cfg.container.tmux.session


def test_the_session_name_is_config_because_it_is_a_reattach_identity(
    tmp_path: Path,
) -> None:
    cfg = GroveConfig.model_validate(
        {"container": {"tmux": {"shell_session": "human", "term_fallback": ""}}}
    )

    argv = _shell(tmp_path, cfg=cfg).argv()

    assert argv[argv.index("-s") + 1] == "human"


def test_which_shell_is_config_and_is_tried_in_order(tmp_path: Path) -> None:
    """The image is somebody else's, so the shell cannot be a constant here."""
    cfg = GroveConfig.model_validate(
        {"container": {"shell": ["zsh", "bash", "sh"], "tmux": {"term_fallback": ""}}}
    )

    script = _shell(tmp_path, cfg=cfg).script

    assert "for __grove_shell in zsh bash sh; do" in script
    assert 'command -v "$__grove_shell" >/dev/null 2>&1 && exec "$__grove_shell"' in script


def test_the_default_chain_survives_an_image_with_no_bash(tmp_path: Path) -> None:
    """`bash` is absent from plenty of real base images that still ship `sh`."""
    script = _shell(tmp_path, cfg=GroveConfig()).script

    assert "for __grove_shell in bash sh; do" in script


def test_an_exhausted_chain_says_so_and_exits_rather_than_substituting(
    tmp_path: Path,
) -> None:
    """A baked-in final fallback would be a shell name in code that the config
    exists to own — and would hide the misconfiguration behind a prompt."""
    cfg = GroveConfig.model_validate({"container": {"shell": ["fish"]}})

    script = _shell(tmp_path, cfg=cfg).script

    assert "container.shell" in script
    assert script.strip().endswith("exit 127")


def test_the_shell_starts_where_the_agent_does(tmp_path: Path) -> None:
    """A nested project's shell lands in the project, not at the mount root —
    the exec itself only ever lands in the configuration's workspaceFolder."""
    script = _shell(tmp_path, subpath="services/api").script

    assert script.splitlines()[0] == f"cd {FAKE_REMOTE_FOLDER}/services/api || exit 1"


def test_a_flat_workspace_needs_no_cd_at_all(tmp_path: Path) -> None:
    assert not _shell(tmp_path).script.startswith("cd ")


def test_a_container_with_no_tmux_degrades_to_a_shell_that_dies_with_its_client(
    tmp_path: Path,
) -> None:
    """The honest degradation: a shell you get, persistence you do not."""
    argv = _shell(tmp_path, tmux_command="").argv()

    entry = argv[argv.index("--") + 1 :]
    assert "new-session" not in entry
    assert entry[:2] == ["sh", "-c"]
    assert 'exec "$__grove_shell"' in entry[2]


def test_an_unknown_client_TERM_retries_once_for_the_shell_too(tmp_path: Path) -> None:
    """The refusal is a property of attaching, not of what is being attached to,
    so the shell needs the identical retry the agent has."""
    argv = _shell(tmp_path, cfg=GroveConfig()).argv()

    wrapper = argv[-2]
    assert "grove: tmux could not start a client for TERM=$TERM" in wrapper
    assert "has-session -t shell" in wrapper
    assert "TERM=xterm-256color" in wrapper


def test_the_exec_carries_the_identity_and_config_up_used(tmp_path: Path) -> None:
    """An exec that disagrees with `up` resolves a DIFFERENT container."""
    shell = _shell(tmp_path)

    argv = shell.argv()

    assert "--id-label" in argv
    assert argv[argv.index("--id-label") + 1] == "grove.workspace=ws1"
    assert argv[argv.index("--override-config") + 1] == shell.container.override_config_path


def test_the_pane_line_is_the_argv_shell_quoted(tmp_path: Path) -> None:
    """Window 0 takes a command STRING, `grove shell` takes argv — one
    composition, quoted at exactly the one boundary that needs it."""
    shell = _shell(tmp_path)

    assert shell.command == " ".join(shlex.quote(token) for token in shell.argv())


# ─── wiring: window 0 really becomes the container shell ────────────────────


def _cfg(tmp_path: Path, *, container: bool) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": container, "tmux": {"term_fallback": ""}},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


def _manager(
    tmp_repo: Path, tmp_path: Path, *, container: bool, cli: FakeCli | None = None
) -> WorkspaceManager:
    """A real manager over the shared fakes.

    A ``cli`` with ``image_has_tmux=False`` is the DEGRADED container arm — no
    tmux anywhere in there, so the agent really does run in a host pane and
    window 0 is still what carries the shell into the container. With a tmux,
    no host session is laid out at all and the shell is started detached
    inside instead, so the CLI rather than the layout is where it is read.
    """
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, container=container),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli if cli is not None else FakeCli(image_has_tmux=True),
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title="ws"))


def test_the_launch_puts_the_shell_session_inside_the_container(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The producer, through the real manager.

    A HOST shell rooted at the worktree and typed into window 0 would put a
    user attaching and switching windows on the host, beside a container they
    believe they are inside. Instead there is no host session on this arm at
    all: the launch starts the same `-A -s shell` session DETACHED in the
    container's own tmux — one shell, sitting there before anyone asks, rather
    than one minted by whoever asks first.
    """
    cli = FakeCli(image_has_tmux=True)
    state = _create(_manager(tmp_repo, tmp_path, container=True, cli=cli))

    assert fake_tmux.shell_commands == []  # nothing host-side to type it into
    assert state.container is not None
    # `-f` precedes the command word because it is a SERVER option, and it is
    # present here because the decor bundle ships with Grove and so is always
    # mounted by default — asserted through the record rather than as a literal,
    # since a workspace whose bundle was unavailable records `""` and correctly
    # composes no flag at all.
    conf = ["-f", state.container.tmux_conf] if state.container.tmux_conf else []
    assert list(cli.execs[-1]["argv"])[: 6 + len(conf)] == [
        state.container.tmux_command,
        *conf,
        "new-session",
        "-A",
        "-d",
        "-s",
        "shell",
    ]


def test_a_degraded_container_still_carries_the_shell_into_window_0(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """The degraded arm: no tmux in there, so the host session is real.

    The agent genuinely runs in a host pane here, and window 0 beside it is the
    only place a shell can go — which is exactly why it must still cross the
    namespace boundary rather than quietly staying on the host.
    """
    manager = _manager(tmp_repo, tmp_path, container=True, cli=FakeCli(image_has_tmux=False))
    state = _create(manager)

    (session, shell_command) = fake_tmux.shell_commands[-1]
    assert session == state.tmux_session
    assert shell_command.startswith("devcontainer exec")
    assert "new-session" not in shell_command  # no tmux in there to enter
    assert 'exec "$__grove_shell"' in shell_command


def test_a_host_workspace_keeps_exactly_the_shell_window_it_always_had(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """Absence is the default: nothing is typed, so window 0 stays the plain
    host shell rooted at the worktree."""
    _create(_manager(tmp_repo, tmp_path, container=False))

    assert [cmd for _, cmd in fake_tmux.shell_commands] == [""]


def test_the_shell_the_launch_started_and_the_shell_verb_are_the_same_session(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    """Two entries into one session, or "the shell the launch started" and "the
    shell verb" become two shells with two histories — the whole point of `-A`.

    They are deliberately NOT the same argv: the launch starts it detached
    (`-d`, nobody is dropped into it) and the verb attaches a client. What has
    to match is everything that decides WHICH shell that is — the session
    name and the script it runs.
    """
    del fake_tmux
    cli = FakeCli(image_has_tmux=True)
    manager = _manager(tmp_repo, tmp_path, container=True, cli=cli)
    state = _create(manager)
    assert state.container is not None
    started: list[str] = list(cli.execs[-1]["argv"])

    verb = ContainerShell(
        container=state.container,
        cfg=manager.config,
        worktree=Path(state.worktree_path),
        cwd=state.agent_cwd,
        cli=FakeCli(),
    ).argv()

    assert started[started.index("-s") + 1] == verb[verb.index("-s") + 1] == "shell"
    assert started[-1] == verb[-1]  # the same `sh -c` script, so the same shell
    assert "-d" in started and "-d" not in verb


@pytest.mark.parametrize(
    ("container", "image_has_tmux", "laid_out"),
    [
        # A container with a tmux in it has no host session at all, so there
        # is no layout call to carry anything.
        (True, True, False),
        # The degraded container: a real host pane, and window 0 must cross.
        (True, False, True),
        # A host workspace: laid out, and window 0 stays the plain host shell.
        (False, False, True),
    ],
)
def test_only_a_host_pane_gets_a_layout_and_it_carries_the_right_shell(
    tmp_repo: Path,
    tmp_path: Path,
    fake_tmux: FakeTmux,
    container: bool,
    image_has_tmux: bool,
    laid_out: bool,
) -> None:
    """Pins the threading across all three arms rather than one — an unwired
    producer reads as "handled" in review and in a green suite, and so does a
    host session laid out for a workspace that has no use for one."""
    _create(
        _manager(
            tmp_repo,
            tmp_path,
            container=container,
            cli=FakeCli(image_has_tmux=image_has_tmux),
        )
    )

    assert len(fake_tmux.shell_commands) == int(laid_out)
    if laid_out:
        # Non-empty only where the shell has to cross a namespace boundary; a
        # host workspace's window 0 is left exactly as it always was.
        assert bool(fake_tmux.shell_commands[0][1]) is container
