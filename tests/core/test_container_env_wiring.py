"""`container.env_file` / `env_command`, wired: what actually crosses into a container.

`test_env_source.py` pins the source as a unit — the dotenv grammar, the host
read/exec, the 0600 render. This module pins the half that cannot be tested
there: that a create *applies* it to both roads into the container (the CLI's
`--secrets-file`, which is what puts the values in the project's own lifecycle
hooks, and the agent's launch env), that a resume re-applies it, that the
precedence between the three env producers is what the docs promise, and that a
host workspace is left alone.

Everything runs the real manager against the shared fake container boundaries;
no Docker, no devcontainer CLI, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.conftest import FakeCli, FakePreflight, FakeTmux

#: A dotenv payload that would break a naive parser: a value holding `=`, one
#: holding a `$` that must NOT be expanded, an `export ` prefix, a comment.
DOTENV = """\
# the repo's container secrets
export API_TOKEN=tok-abc=123
LITERAL_DOLLAR='raw ${NOT_EXPANDED}'
"""


def _cfg(tmp_path: Path, **container: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True, **container},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


def _manager(
    repo: Path, store_dir: Path, cfg: GroveConfig, cli: FakeCli | None = None
) -> WorkspaceManager:
    return WorkspaceManager(
        repo_root=repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=store_dir / "state.json"),
        devcontainer_cli=cli if cli is not None else FakeCli(),
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager, title: str = "ws", **kw: object) -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title, **kw))  # type: ignore[arg-type]


def _pane_command(fake_tmux: FakeTmux, state: WorkspaceState) -> str:
    """The command string the agent window runs — where `--remote-env` lands.

    The LATEST layout for this session: a resume lays the session out again, and
    a test asserting a re-injection must read the relaunch, not the create.
    """
    return [cmd for session, cmd in fake_tmux.layouts if session == state.tmux_session][-1]


# ─── the two roads into the container ───────────────────────────────────────


def test_env_file_reaches_the_secrets_file_and_the_agent(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Lifecycle hooks get it via `--secrets-file`; the agent via `--remote-env`."""
    (tmp_repo / ".grove").mkdir(exist_ok=True)
    (tmp_repo / ".grove" / "container.env").write_text(DOTENV, encoding="utf-8")
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file=".grove/container.env"), cli)

    state = _create(manager)

    up = cli.ups[-1]
    assert up["secrets"] == {"API_TOKEN": "tok-abc=123", "LITERAL_DOLLAR": "raw ${NOT_EXPANDED}"}
    # 0600 at the moment the CLI read it, not merely after a later chmod.
    assert up["secrets_mode"] == 0o600
    # Gone the instant `up` returned: nothing is left on disk to leak or to
    # go stale, which is the whole point of resolving per start.
    assert up["secrets_file"] is not None
    assert not Path(up["secrets_file"]).exists()

    command = _pane_command(fake_tmux, state)
    assert "--remote-env" in command
    assert "API_TOKEN=tok-abc=123" in command


def test_the_secrets_file_never_lands_inside_the_worktree(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The worktree is a git checkout AND a bind mount — a secret there is one
    `git add -A` from being committed."""
    del fake_tmux
    (tmp_repo / ".grove").mkdir(exist_ok=True)
    (tmp_repo / ".grove" / "container.env").write_text("K=v\n", encoding="utf-8")
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file=".grove/container.env"), cli)

    state = _create(manager)

    secrets = cli.ups[-1]["secrets_file"]
    assert secrets is not None
    assert not secrets.is_relative_to(Path(state.worktree_path))
    assert not secrets.is_relative_to(tmp_repo)


def test_env_command_stdout_is_parsed_as_dotenv(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The mechanism knows nothing about any particular secret store: any
    command that prints dotenv to stdout works."""
    script = "import sys; sys.stdout.write('FROM_STORE=s3cret\\n')"
    cfg = _cfg(tmp_path, env_command=f"{sys.executable} -c {script!r}")
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, cfg, cli)

    state = _create(manager)

    assert cli.ups[-1]["secrets"] == {"FROM_STORE": "s3cret"}
    assert "FROM_STORE=s3cret" in _pane_command(fake_tmux, state)


def test_no_env_source_configured_passes_no_secrets_file(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """An empty mapping writes no file and adds no flag — not an empty one."""
    del fake_tmux
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path), cli)

    _create(manager)

    assert cli.ups[-1]["secrets_file"] is None


# ─── precedence, the thing people get wrong ─────────────────────────────────


def test_agent_env_outranks_the_injected_values(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`agents[].env` is the most specific explicit override, so it wins."""
    (tmp_repo / "c.env").write_text("SHARED=from-dotenv\n", encoding="utf-8")
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True, "env_file": "c.env"},
            "agents": [
                {
                    "name": "claude",
                    "command": "claude",
                    "kind": "claude_code",
                    "env": {"SHARED": "from-agent"},
                }
            ],
        }
    )
    manager = _manager(tmp_repo, tmp_path, cfg)

    command = _pane_command(fake_tmux, _create(manager))

    assert "SHARED=from-agent" in command
    assert "SHARED=from-dotenv" not in command


def test_the_agent_config_share_outranks_the_injected_values(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Grove's config-dir pointer is the isolation contract, and a user's dotenv
    must not be able to redirect the agent away from its own mounted config."""
    (tmp_repo / "c.env").write_text("CLAUDE_CONFIG_DIR=/tmp/hijacked\n", encoding="utf-8")
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file="c.env"))

    command = _pane_command(fake_tmux, _create(manager))

    assert "CLAUDE_CONFIG_DIR=/tmp/hijacked" not in command


# ─── scope: host workspaces are left alone ──────────────────────────────────


def test_a_host_workspace_is_never_injected(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A host agent already inherits the user's shell — this solves a problem it
    does not have, and injecting there would be a surprise."""
    (tmp_repo / "c.env").write_text("API_TOKEN=tok\n", encoding="utf-8")
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file="c.env"))

    state = _create(manager, runtime=Runtime.HOST)

    assert state.runtime is Runtime.HOST
    assert all("API_TOKEN" not in env for _, env, _ in fake_tmux.launch_envs)


# ─── resume re-applies, on both roads ───────────────────────────────────────


def test_resume_reinjects_current_values(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Nothing is persisted, so a resume picks up whatever the source says NOW —
    which is exactly what makes a rotated secret work without a recreate."""
    env_path = tmp_repo / "c.env"
    env_path.write_text("API_TOKEN=first\n", encoding="utf-8")
    cli = FakeCli()
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file="c.env"), cli)
    state = _create(manager)
    manager.pause(state.id)

    env_path.write_text("API_TOKEN=rotated\n", encoding="utf-8")
    resumed = manager.resume(state.id)

    assert cli.ups[-1]["secrets"] == {"API_TOKEN": "rotated"}
    assert "API_TOKEN=rotated" in _pane_command(fake_tmux, resumed)


# ─── failure is loud, and never leaks a value ───────────────────────────────


def test_a_missing_env_file_fails_the_create(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A silently-empty environment is the exact failure this feature exists to
    fix, so a configured-but-absent file must not be shrugged off."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file="absent.env"))

    with pytest.raises(GroveError, match=r"absent\.env"):
        _create(manager)


def test_the_provision_log_records_key_names_and_never_values(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The log is a diagnosis surface a human and the failure detail both read."""
    del fake_tmux
    (tmp_repo / "c.env").write_text("API_TOKEN=super-secret-value\n", encoding="utf-8")
    manager = _manager(tmp_repo, tmp_path, _cfg(tmp_path, env_file="c.env"))

    state = _create(manager)

    assert state.provision_log_path is not None
    log = Path(state.provision_log_path).read_text(encoding="utf-8")
    assert "API_TOKEN" in log
    assert "super-secret-value" not in log
