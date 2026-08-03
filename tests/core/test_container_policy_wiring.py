"""The autonomy planners, wired: what actually reaches `up` and the agent pane.

`test_container_policy.py` pins the planners as pure functions — the mount list,
the script text, the argv. This module pins the half that cannot be tested there:
that the create path *applies* them, that a resume re-applies them, and that the
two producers of `GIT_CONFIG_COUNT` end up with one consistent numbering.

Everything runs the real manager against the shared fake container boundaries;
no Docker, no devcontainer CLI, no network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from grove.core.channel import channel_settings_path
from grove.core.config import ChannelsConfig, GroveConfig, HooksConfig, PermissionConfig
from grove.core.container_policy import (
    CONTAINER_CONFIG_ROOT,
    CONTAINER_CONTROL_ROOT,
    EgressPolicy,
)
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.devcontainer import (
    OVERRIDE_CONFIG_RELPATH,
    DevcontainerConfig,
    UpResult,
)
from grove.core.errors import ContainerRequired
from grove.core.manager import WorkspaceManager
from grove.core.permission import permission_mcp_config_path
from grove.core.runtime import ContainerProvisioner
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, WorkspaceState
from tests.conftest import FakeCli, FakePreflight, FakeTmux

#: A full 64-character container id. Every command that NAMES a container
#: validates this, because `docker update`/`rm` resolve a short id as a PREFIX —
#: a fake short id would silently skip the assertion under test.
_CTR = "c" * 64
#: The compose project name the CLI mints from the worktree basename.
_PROJECT = "repo_devcontainer"


class ComposeCli(FakeCli):
    """A `devcontainer up` that reports a compose project, as a stack's does."""

    def up(self, *args: object, **kwargs: object) -> UpResult:
        result = super().up(*args, **kwargs)  # type: ignore[arg-type]
        return result.model_copy(update={"compose_project_name": _PROJECT})


def _cfg(tmp_path: Path, **container: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True, **container},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


@pytest.fixture
def cli() -> FakeCli:
    return FakeCli()


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, cli: FakeCli) -> WorkspaceManager:
    del fake_tmux
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )


def _create(manager: WorkspaceManager, title: str = "ws") -> WorkspaceState:
    return manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))


def _record_docker(
    monkeypatch: pytest.MonkeyPatch, *, fail: Exception | None = None
) -> list[list[str]]:
    """Record `docker` argv, passing every other command through to real git.

    `import subprocess` binds the ONE process-wide module object, so patching
    `grove.core.runtime.subprocess.run` also intercepts `grove.core.git`'s calls
    — the passthrough is what keeps this a docker spy rather than a git outage
    (tests/CLAUDE.md).
    """
    real = subprocess.run
    calls: list[list[str]] = []

    def _spy(argv, **kwargs):  # type: ignore[no-untyped-def]
        if not argv or Path(str(argv[0])).name != "docker":
            return real(argv, **kwargs)
        calls.append([str(a) for a in argv])
        if fail is not None:
            raise fail
        return subprocess.CompletedProcess(args=list(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("grove.core.runtime.subprocess.run", _spy)
    return calls


def _updates(calls: list[list[str]]) -> list[list[str]]:
    """Just the `docker update` argv — a provision also emits one mint-time read."""
    return [argv for argv in calls if argv[:2] == ["docker", "update"]]


def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **claude_json: object) -> Path:
    """A fabricated `$HOME` with a `.claude.json` in it.

    Any test that reaches the seed writer needs one: the developer's real home
    is not a fixture, and reading it makes the assertion depend on whoever runs
    the suite.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps(claude_json), encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _seed(state: WorkspaceState) -> dict[str, object]:
    """The `.claude.json` a provision seeded for *state*."""
    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    raw = (paths.agent_workspace_config_dir(state.id) / ".claude.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def _override(state: WorkspaceState) -> DevcontainerConfig:
    raw = (Path(state.worktree_path) / OVERRIDE_CONFIG_RELPATH).read_text(encoding="utf-8")
    return DevcontainerConfig.model_validate_json(raw)


# ─── the agent-config share ─────────────────────────────────────────────────


def test_the_share_mount_and_the_env_that_points_at_it_come_from_one_plan(
    manager: WorkspaceManager, cli: FakeCli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A mount with no env is unread; an env with no mount points at nothing."""
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    state = _create(manager)

    container_dir = CONTAINER_CONFIG_ROOT / "claude_code"
    # Through the override config's `mounts`, NOT `up --mount`: these binds are
    # read-only, and the CLI's flag parser rejects that option outright — it
    # accepts only type/source/target[/external] and fails the whole
    # invocation, which is every containerized create.
    mounts = [str(m) for m in _override(state).mounts]
    assert any(f"target={container_dir / 'settings.json'}" in m for m in mounts)
    assert any(f"target={container_dir / 'skills'}" in m for m in mounts)
    assert all("readonly" in m for m in mounts if f"{container_dir}/" in m)
    # The config root itself is bound writable — that bind is what makes the
    # agent's transcript (and the seeded config) exist on the host at all.
    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    assert any(
        m == f"type=bind,source={paths.agent_workspace_config_dir(state.id)},target={container_dir}"
        for m in mounts
    )
    # The env half rides the launch spec, so it reaches the agent through
    # `devcontainer exec --remote-env` — same plan, same directory.
    assert _override(state).remote_env["CLAUDE_CONFIG_DIR"] == str(container_dir)


def test_a_host_workspace_never_gets_the_container_config_dir_env(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """On the host that var would redirect the agent away from the user's config."""
    del fake_tmux
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )
    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="hostly", runtime=Runtime.HOST)
    )
    agent = manager.config.find_agent("claude")
    assert agent is not None
    assert manager._share_plan(state, agent) is None


def test_the_seed_is_a_copy_without_the_project_history(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sign-in carries; other workspaces' history does not."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "dev@example.test"}, "projects": {"/a": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    state = _create(manager)

    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    seeded = json.loads(
        (paths.agent_workspace_config_dir(state.id) / ".claude.json").read_text(encoding="utf-8")
    )
    assert seeded["oauthAccount"]["emailAddress"] == "dev@example.test"
    # The host's map is dropped whole; the only folder the seed knows about is
    # the one this container runs in, freshly stamped.
    assert list(seeded["projects"]) == ["/workspaces/repo"]


def test_the_create_path_stamps_the_folder_the_cli_reported_as_trusted(
    manager: WorkspaceManager, cli: FakeCli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unattended by definition: nobody is at the terminal to answer the trust
    dialog, so a container workspace that meets it never begins working.

    Pinned end to end because the ordering is the load-bearing part — the stamp
    is filed under the folder `read-configuration` reports, which therefore has
    to be read BEFORE the seed is written, and all of it before `up`.
    """
    _fake_home(monkeypatch, tmp_path)

    state = _create(manager)

    entry = _seed(state)["projects"]["/workspaces/repo"]  # type: ignore[index]
    assert entry["hasTrustDialogAccepted"] is True
    # Written before the container was created, not after: the agent launch is
    # a `devcontainer exec` and the dialog fires at its first start, so a
    # post-`up` write races the very thing it prevents.
    assert cli.reads and cli.ups


def test_the_stamp_approves_the_worktrees_committed_mcp_servers(
    manager: WorkspaceManager,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The approval for a project-scoped `.mcp.json` sits in the same per-folder
    block — trusting the folder alone swaps one blocking prompt for another."""
    _fake_home(monkeypatch, tmp_path)
    (tmp_repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"gitea": {"type": "http", "url": "http://forge.test"}}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_repo), "add", ".mcp.json"], check=True)
    subprocess.run(["git", "-C", str(tmp_repo), "commit", "-m", "mcp"], check=True)

    state = _create(manager)

    entry = _seed(state)["projects"]["/workspaces/repo"]  # type: ignore[index]
    assert entry["enabledMcpjsonServers"] == ["gitea"]


def test_trust_off_leaves_the_dialog_in_place(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    cli: FakeCli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cascaded config value, no bespoke flag: turning it off means the
    agent will not start on its own until a human attaches and answers."""
    del fake_tmux
    _fake_home(monkeypatch, tmp_path)
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, agent_config={"share": "full", "trust": False}),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(),
    )

    state = _create(manager)

    assert "projects" not in _seed(state)


def test_the_first_ever_container_create_still_mounts_groves_control_files(
    manager: WorkspaceManager,
) -> None:
    """The machine where no agent has EVER launched on the host.

    Pure sequencing trap: `AgentSharePlan.plan` drops a bind source that does
    not exist — correctly, since Docker would materialize a missing one
    root-owned at the very path Grove's writer later needs — but the settings
    file is written at LAUNCH and the plan is built at PROVISION, which on
    `create` runs first. So the first container create on a fresh install, a CI
    runner, or a container-only user plans nothing and the workspace comes up
    with hooks silently dead. It self-heals on the second create, which is why
    it can survive unnoticed on a developer machine that has already created
    a container once.

    The ordering is therefore the whole test, and it is why nothing here
    pre-creates the file: the autouse `_isolated_agent_hook_paths` fixture
    points the path at a tmp directory that no test has written to, so this
    manager's `create` is genuinely the first launch this "machine" has seen.
    Asserting on the plan, or on a fixture that touched the file first, would
    pass against the bug.
    """
    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    settings = paths.agent_hooks_settings_path()
    assert not settings.exists(), "the fixture must not have written it — that IS the bug"

    state = _create(manager)

    # Read back off what was handed to `up`, not off a re-derived plan: a mount
    # the plan dropped is exactly what this cannot afford to compute twice.
    mounts = [str(m) for m in _override(state).mounts]
    assert any(f"source={settings}," in m for m in mounts), mounts
    assert any(
        m == f"type=bind,source={settings},target={CONTAINER_CONTROL_ROOT / settings.name},readonly"
        for m in mounts
    )


def test_a_disabled_control_feature_writes_nothing_and_mounts_nothing(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The pre-provision render is gated on the same switches the launch is.

    Otherwise "ensure it exists early" would quietly turn every opt-in feature
    into a file in the user's config directory plus a mount in every container.
    """
    del fake_tmux
    cfg = _cfg(tmp_path).model_copy(update={"hooks": HooksConfig(enabled=False)})
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )

    state = _create(manager, title="no-hooks")

    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    assert not paths.agent_hooks_settings_path().exists()
    assert not any(str(CONTAINER_CONTROL_ROOT) in str(m) for m in _override(state).mounts)


def test_a_container_launch_omits_the_flags_whose_server_runs_on_the_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """An enabled feature whose server cannot exist here composes NO flag.

    `--channels` and `--mcp-config` register a stdio server run by
    `sys.executable` — the host interpreter. Both files are rendered on the host
    (the features are on) and both are perfectly mountable, so nothing upstream
    of the mount table can tell that what they NAME is unreachable. Mounting
    neither is what makes the launch omit both, and omitting `--mcp-config` is
    what takes `--permission-prompt-tool` with it: pointing Claude Code's
    permission gate at a server that can never answer would block every tool
    call, in the one runtime whose blast radius already *is* the safety
    mechanism that prompt provides.

    The hook `--settings` flag is asserted present in the same breath, so this
    cannot pass by a launch that refuses every control file.
    """
    cfg = _cfg(tmp_path).model_copy(
        update={
            "channels": ChannelsConfig(enabled=True),
            "permission": PermissionConfig(enabled=True),
        }
    )
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )

    state = _create(manager, title="host-servers")

    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    host_only = (channel_settings_path(), permission_mcp_config_path())
    # Rendered on the host — the features ARE on, so this is not a no-op test.
    assert all(path.exists() for path in host_only)

    mounts = [str(m) for m in _override(state).mounts]
    assert not any(f"source={path}," in m for path in host_only for m in mounts), mounts
    assert any(f"source={paths.agent_hooks_settings_path()}," in m for m in mounts), mounts

    decoration = " ".join(dict(fake_tmux.launch_decorations)[state.tmux_session])
    assert "--channels" not in decoration
    assert "--mcp-config" not in decoration
    assert "--permission-prompt-tool" not in decoration
    assert f"--settings {CONTAINER_CONTROL_ROOT}/" in decoration


def test_the_seed_registers_no_mcp_server_the_container_cannot_start(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: the host's own MCP registry never reaches the container.

    Every entry in `~/.claude.json`'s `mcpServers` resolves against a filesystem
    and a network namespace the container does not have — Grove's `grove-mcp`
    console script was the observed case. The seed is written before `up`, so
    nothing can probe the container's `PATH`; the key is dropped whole rather
    than filtered by command shape, which would guess wrong in both directions.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"emailAddress": "dev@example.test"},
                "mcpServers": {
                    "grove": {"type": "stdio", "command": "~/.local/bin/grove-mcp", "args": []}
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    state = _create(manager)

    from grove.core import paths  # noqa: PLC0415 - the redirected path, read late

    seeded = json.loads(
        (paths.agent_workspace_config_dir(state.id) / ".claude.json").read_text(encoding="utf-8")
    )
    assert "mcpServers" not in seeded
    assert seeded["oauthAccount"]["emailAddress"] == "dev@example.test"


# ─── egress ─────────────────────────────────────────────────────────────────


def test_the_firewall_script_and_its_hook_and_caps_all_reach_the_container(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    state = _create(manager)
    override = _override(state)

    assert (Path(state.worktree_path) / EgressPolicy.SCRIPT_RELPATH).is_file()
    assert EgressPolicy.SCRIPT_RELPATH.name in str(override.post_start_command)
    # Without these the script cannot touch iptables, and it fails closed — so
    # omitting them would turn every container start into a hard failure.
    assert set(EgressPolicy.CAP_ADD) <= set(override.cap_add)


def test_grove_alone_supervises_the_container_it_creates(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The two supervision properties a create must stamp, and why each is there.

    Both hold against Docker 29.6.1 and `@devcontainers/cli` 0.88.0.
    `--restart no` is asserted because a project's own `runArgs` can set a
    policy that resurrects a PAUSED container when the docker
    daemon restarts — and a docker-level restart does NOT re-run
    `postStartCommand`, so the agent would come back with no egress firewall.
    `init` closes the zombie leak in the shapes where the CLI's own reaping
    entrypoint `exec`s away (`overrideCommand: false`, compose services).
    """
    override = _override(_create(manager))

    assert override.run_args[-2:] == ["--restart", "no"]
    assert override.init is True


def test_the_reaper_applies_when_the_project_never_mentioned_init(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """The merged envelope materializes `init: false`, which reads as a project
    decision unless the overlay decides against the RAW envelope instead —
    driven through the MERGED envelope here, where the CLI reports `init:
    false` whether or not any layer asked, to prove the overlay's default
    still applies when nobody actually pinned it.
    """
    manager._devcontainer_cli = FakeCli(
        config=DevcontainerConfig.model_validate({"image": "python:3.12"})
    )
    manager._container_provisioner = None
    manager._runtime_resolver = None

    override = _override(_create(manager, title="unspecified-init"))

    assert override.init is True
    assert "privileged" not in json.loads(override.to_json()), (
        "the same materialized default, dropped by the same table — it is listed "
        "before Grove reads it so the trap cannot be sprung a second time"
    )


def test_a_project_that_pins_init_itself_is_not_overridden(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """An image running systemd checks `getpid() == 1`; tini in front breaks it.

    The opt-out is decided against the RAW envelope, not the merged one: both
    configs below say `init: false`, and only the raw one proves the project
    wrote it rather than the CLI materializing it as a default.
    """
    devcontainer = tmp_repo / ".devcontainer"
    devcontainer.mkdir(parents=True, exist_ok=True)
    (devcontainer / "devcontainer.json").write_text(
        json.dumps({"image": "python:3.12", "init": False}), encoding="utf-8"
    )
    manager._devcontainer_cli = FakeCli(
        config=DevcontainerConfig.model_validate({"image": "python:3.12", "init": False})
    )
    manager._container_provisioner = None
    manager._runtime_resolver = None

    override = _override(_create(manager, title="systemd-ish"))

    assert override.init is False
    # The restart assertion is NOT a default — it still holds here.
    assert override.run_args[-2:] == ["--restart", "no"]


def test_the_repos_own_remotes_are_allowed_so_an_agent_can_push(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """A self-hosted forge is nobody's to hard-code — it comes from `git remote`."""
    subprocess.run(
        ["git", "remote", "add", "origin", "https://forge.example.test/team/repo.git"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    )

    state = _create(manager)
    script = (Path(state.worktree_path) / EgressPolicy.SCRIPT_RELPATH).read_text(encoding="utf-8")

    assert "forge.example.test" in script


def test_a_projects_own_post_start_command_is_composed_with_never_replaced(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """Dropping the project's hook breaks the workspace as if it were its own bug.

    Driven through the MERGED envelope, which is what the read path actually
    gets — the raw-only arm can pass this assertion while `_compose_post_start`
    is handed `existing=None` every time (its "nothing to compose with" branch),
    silently replacing the project's hook outright instead of composing with it.
    """
    devcontainer = tmp_repo / ".devcontainer"
    devcontainer.mkdir(parents=True, exist_ok=True)
    (devcontainer / "devcontainer.json").write_text(
        json.dumps({"image": "python:3.12", "postStartCommand": "make services"}),
        encoding="utf-8",
    )
    manager._devcontainer_cli = FakeCli(
        config=DevcontainerConfig.model_validate(
            {"image": "python:3.12", "postStartCommand": "make services"}
        )
    )
    manager._container_provisioner = None
    manager._runtime_resolver = None

    state = _create(manager, title="own-hook")
    override = _override(state)
    composed = str(override.post_start_command)

    assert "make services" in composed
    # Grove's runs FIRST: the boundary is up before anything the project starts
    # can reach the network.
    assert composed.index(EgressPolicy.SCRIPT_RELPATH.name) < composed.index("make services")
    assert "postStartCommands" not in json.loads(override.to_json()), (
        "the plural name the merged envelope uses must not survive into the "
        "override — its reader knows only the singular one"
    )


def test_a_projects_create_hook_reaches_the_override_with_every_features_own(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """End to end at the wiring level: the hook a container never ran.

    Grove models no `postCreateCommand` field at all, so this rides `extra`
    the whole way — which is exactly how the plural name can reach the override
    file and be silently ignored there.
    """
    # The one site that pins an explicit merged envelope rather than deriving
    # one: the array holds a FEATURE's command beside the project's, and the
    # shared fake resolves no Features. Content the derivation cannot invent is
    # exactly what the explicit form is for.
    manager._devcontainer_cli = FakeCli(
        merged=DevcontainerConfig.model_validate(
            {
                "image": "python:3.12",
                "postCreateCommands": ["feature-setup.sh", "make bootstrap"],
                "onCreateCommands": ["echo first"],
            }
        )
    )
    manager._container_provisioner = None
    manager._runtime_resolver = None

    payload = json.loads(_override(_create(manager, title="create-hook")).to_json())

    assert payload["postCreateCommand"] == "feature-setup.sh && make bootstrap"
    assert payload["onCreateCommand"] == "echo first"
    assert not [key for key in payload if key.endswith("Commands")]


def test_requires_container_refuses_a_host_create_off_the_merged_customizations(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The L2 floor, driven through the envelope the read path really gets.

    `test_runtime_selection.py` pins this refusal against a `configuration`-only
    result, where `customizations.grove` is still an object. Production reads the
    merged envelope, where it is a LIST of per-layer objects — a floor that
    checked `isinstance(grove, dict)` there would answer "not required" and
    quietly allow a host create in a repo that refuses one.
    """
    del fake_tmux
    devcontainer = tmp_repo / ".devcontainer"
    devcontainer.mkdir(parents=True, exist_ok=True)
    (devcontainer / "devcontainer.json").write_text(
        json.dumps({"image": "py", "customizations": {"grove": {"requires_container": True}}}),
        encoding="utf-8",
    )
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(
            config=DevcontainerConfig.model_validate(
                {"image": "python:3.12", "customizations": {"grove": {"requires_container": True}}}
            )
        ),
        preflight=FakePreflight(),
    )

    with pytest.raises(ContainerRequired, match="requires_container"):
        manager.create(
            CreateWorkspaceRequest(agent_name="claude", title="forced", runtime=Runtime.HOST)
        )


def test_egress_open_writes_no_script_and_asks_for_no_capabilities(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`open` is a supported, un-nagged opt-out — not a degraded mode."""
    del fake_tmux
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, egress={"mode": "open"}),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(),
        preflight=FakePreflight(),
    )
    state = _create(manager, title="open")
    override = _override(state)

    assert not (Path(state.worktree_path) / EgressPolicy.SCRIPT_RELPATH).exists()
    assert not set(EgressPolicy.CAP_ADD) & set(override.cap_add)


def test_resume_reapplies_the_firewall_because_pause_took_the_worktree_with_it(
    manager: WorkspaceManager,
) -> None:
    """The script lives in the worktree, so a resume that skipped this would
    start the agent in a container with no boundary at all."""
    state = _create(manager, title="resumed")
    manager.pause(state.id)
    assert not (Path(state.worktree_path) / EgressPolicy.SCRIPT_RELPATH).exists()

    manager.resume(state.id)

    assert (Path(state.worktree_path) / EgressPolicy.SCRIPT_RELPATH).is_file()


# ─── git config ─────────────────────────────────────────────────────────────


def test_curated_git_config_wins_the_numbering_over_the_overlays_own_entry(
    manager: WorkspaceManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both producers write GIT_CONFIG_COUNT; a merge would leave it under-counting.

    Git reads exactly COUNT entries, so a count of 1 beside four written pairs
    silently drops three of them — including the identity the commits need.
    """
    monkeypatch.setattr(
        "grove.core.git.GitRepo.global_config",
        classmethod(
            lambda cls: {
                "user.name": "Dev Eloper",
                "user.email": "dev@example.test",
                # Never forwarded: this is the reason the host gitconfig is not
                # simply mounted.
                "credential.helper": "store",
            }
        ),
    )

    env = _override(_create(manager)).remote_env
    count = int(env["GIT_CONFIG_COUNT"])
    keys = {env[f"GIT_CONFIG_KEY_{i}"] for i in range(count)}

    # The count matches what was actually written — no orphaned pairs above it.
    assert all(f"GIT_CONFIG_KEY_{i}" in env for i in range(count))
    assert f"GIT_CONFIG_KEY_{count}" not in env
    assert {"safe.directory", "user.name", "user.email"} <= keys
    assert "credential.helper" not in keys


# ─── resource limits ────────────────────────────────────────────────────────


def test_resource_limits_are_applied_after_up_and_never_fail_the_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap the host kernel cannot honour is a courtesy lost, not a broken create."""
    del fake_tmux
    calls = _record_docker(monkeypatch, fail=OSError("no pids controller"))
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, resources={"memory": "4g", "cpus": "2", "pids": 512}),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=_CTR),
        preflight=FakePreflight(),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="capped"))

    assert state.container is not None and state.container.provisioned
    assert _updates(calls) == [
        ["docker", "update", "--restart", "no", _CTR],
        # `--memory` never applied without its twin: docker rejects the pair as
        # incomplete on a container that has no swap limit yet, which is every
        # container the CLI just created.
        [
            "docker",
            "update",
            "--memory",
            "4g",
            "--memory-swap",
            "4g",
            "--cpus",
            "2",
            "--pids-limit",
            "512",
            _CTR,
        ],
    ]


def test_an_uncapped_workspace_still_asserts_the_restart_policy(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one post-`up` command that is not optional.

    Resource caps are user configuration and an unset one emits nothing. The
    restart policy is Grove's own invariant — it owns this container's
    lifecycle, so docker must never bring it back — and it is asserted whether
    or not anything else is configured.
    """
    del fake_tmux
    calls = _record_docker(monkeypatch)
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=FakeCli(container_id=_CTR),
        preflight=FakePreflight(),
    )

    manager.create(CreateWorkspaceRequest(agent_name="claude", title="uncapped"))

    assert _updates(calls) == [["docker", "update", "--restart", "no", _CTR]]


def test_a_compose_workspace_gets_the_same_assertion_without_any_compose_file(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compose path needs no override of its own, and gets none.

    Compose ignores `runArgs`, so the overlay's creation-time `--restart no`
    does not reach a stack — a project writing `restart: always` on its
    workspace service won outright, and an `always` container resurrects a
    PAUSED workspace on a daemon restart with no `postStartCommand` and
    therefore no egress firewall. `up` reports the workspace service's own
    container id for a stack, so the post-hoc assertion covers it with the same
    command and Grove writes no compose file at all.
    """
    del fake_tmux
    calls = _record_docker(monkeypatch)
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=ComposeCli(container_id=_CTR),
        preflight=FakePreflight(),
    )

    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="stack"))

    assert state.container is not None and state.container.is_compose
    assert _updates(calls) == [["docker", "update", "--restart", "no", _CTR]]
    # The project's own compose configuration is untouched: Grove generates a
    # devcontainer override and nothing else, so no `.yml`/`.yaml` is written.
    worktree = Path(state.worktree_path)
    assert not [p.name for p in worktree.rglob("*.y*ml") if ".git" not in p.parts]


def test_every_artifact_a_provision_leaves_in_the_worktree_is_git_excluded(
    manager: WorkspaceManager, tmp_repo: Path
) -> None:
    """`git worktree remove` refuses on untracked files, so a missed artifact
    breaks `pause` and `kill` on EVERY containerized workspace — the loudest
    possible failure for the quietest possible omission.

    The trap this pins is that not every artifact is Grove's own: `devcontainer
    up` writes its feature lockfile beside whichever config it read, and that
    one went unexcluded. Reads the real exclude file rather than the constant,
    so a pattern that is declared but never written still fails.
    """
    state = _create(manager)
    assert state.container is not None

    excluded = (tmp_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    for pattern in ContainerProvisioner.GENERATED_EXCLUDES:
        assert pattern in excluded, f"{pattern} is declared but never written"
    # The CLI's lockfile lands next to the config it was given, and Grove hands
    # it a config in `.devcontainer/` — but a project whose config is a
    # top-level `.devcontainer.json` puts it at the worktree root instead, so
    # both anchored locations have to be covered.
    assert "/.devcontainer/devcontainer-lock.json" in excluded
    assert "/devcontainer-lock.json" in excluded
