"""Persisted runtime selection + the D5 decision tree.

Nothing here needs Docker, a devcontainer CLI, or a network: the CLI is a
subclass whose four verbs are scripted in memory, and the readiness probe is a
`HostPreflight` subclass with a scripted `container_ready`. That is the whole
point — the decision tree is policy, and policy must be testable without the
runtime it decides about.

Coverage maps to the tree: arm 1 (explicit host), arm 2 (`requires_container`
refusal), arm 3 (no `devcontainer.json` → the packaged default, still
containerized), arm 4 (runtime unavailable → loud, persisted fallback), arm 5
(`up` fails → fatal, container kept, workspace rolled back). Plus the three
invariants that outlive the tree: the decoder default, `resume`/`respawn` never
re-resolving from config, and a nested project's own in-container workdir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.client.client import GroveClient
from grove.core.config import ContainerConfig, GroveConfig
from grove.core.container_infra import slugify_project
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceStateView
from grove.core.devcontainer import DevcontainerConfig
from grove.core.errors import ContainerRequired, GroveError
from grove.core.launch import DevcontainerLaunchBackend, LaunchSpec
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import ProvisionStatus, Runtime, WorkspaceState, WorkspaceStatus
from tests.conftest import FAKE_REMOTE_FOLDER as REMOTE_FOLDER
from tests.conftest import FakeCli, FakePreflight, FakeTmux

# ─── harness ────────────────────────────────────────────────────────────────


def _cfg(tmp_path: Path, *, container_enabled: bool = True) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": container_enabled},
            "agents": [{"name": "claude", "command": "claude", "kind": "claude_code"}],
        }
    )


def _manager(
    tmp_repo: Path,
    tmp_path: Path,
    *,
    cli: FakeCli | None = None,
    preflight: FakePreflight | None = None,
    container_enabled: bool = True,
    store: JsonWorkspaceStore | None = None,
) -> WorkspaceManager:
    resolved_cli = cli if cli is not None else FakeCli()
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path, container_enabled=container_enabled),
        store=store if store is not None else JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=resolved_cli,
        # The engine half is scripted; the CLI half is the REAL preflight check
        # over the injected CLI, so `FakeCli(available=False)` still exercises
        # arm 4 through the same definition `grove doctor` reads.
        preflight=(
            preflight if preflight is not None else FakePreflight(devcontainer_cli=resolved_cli)  # type: ignore[arg-type]
        ),
    )


def _write_devcontainer(repo: Path, *, requires_container: bool = False) -> Path:
    payload: dict[str, object] = {"image": "python:3.12"}
    if requires_container:
        payload["customizations"] = {"grove": {"requires_container": True}}
    target = repo / ".devcontainer" / "devcontainer.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# ─── arm 1: an explicit host choice is a decision, not a fallback ────────────


def test_explicit_host_records_no_fallback_and_never_touches_the_container_runtime(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cli = FakeCli()
    mgr = _manager(tmp_repo, tmp_path, cli=cli)

    state = mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title="hostly", runtime=Runtime.HOST)
    )

    assert state.runtime is Runtime.HOST
    # The distinction the whole design rests on: a *chosen* host workspace has
    # no reason attached, which is what stops respawn from auto-upgrading it.
    assert state.runtime_fallback_reason is None
    assert state.provision_status is ProvisionStatus.SKIPPED
    assert state.container is None
    assert cli.ups == []
    # It launched on the ordinary tmux path.
    assert state.tmux_session in fake_tmux.sessions


# ─── arm 2: a committed layer may raise the floor ───────────────────────────


def test_requires_container_refuses_a_host_create_before_any_side_effect(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    _write_devcontainer(tmp_repo, requires_container=True)
    cli = FakeCli(
        config=DevcontainerConfig.model_validate(
            {"image": "python:3.12", "customizations": {"grove": {"requires_container": True}}}
        )
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = _manager(tmp_repo, tmp_path, cli=cli, store=store)

    with pytest.raises(ContainerRequired, match="requires_container"):
        mgr.create(
            CreateWorkspaceRequest(agent_name="claude", title="forced", runtime=Runtime.HOST)
        )

    # Refused before ANY side effect: no record, no worktree, no tmux session.
    assert store.load_all() == []
    assert not (tmp_path / "trees").exists()
    assert fake_tmux.sessions == set()


# ─── arm 3: absence gets the default container, with a notice ───────────────


def test_a_repo_without_a_devcontainer_still_gets_a_container_on_the_packaged_config(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cli = FakeCli()
    mgr = _manager(tmp_repo, tmp_path, cli=cli)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="defaulted"))

    assert state.runtime is Runtime.CONTAINER
    # Absence is NOT a degradation, so nothing is recorded as a fallback.
    assert state.runtime_fallback_reason is None
    assert state.provision_status is ProvisionStatus.OK
    assert state.container is not None
    assert state.container.provisioned
    assert state.container.remote_workspace_folder == REMOTE_FOLDER
    # The packaged default config was the one handed to read-configuration.
    assert cli.reads and cli.reads[0][1] is not None
    assert cli.reads[0][1].name == "default-devcontainer.json"
    # The container is identified by the labels every later verb re-derives from
    # — `grove.managed=1` included, since that is what the fail-closed teardown
    # sweep filters on and a container without it would be invisible to it.
    assert state.container.id_labels == ContainerRuntimeState.labels_for(
        state.id, project_slug=slugify_project(tmp_repo)
    )
    # Every label a teardown filter can require, stamped at create — the scope
    # label included, because the workspace-teardown sweep filters on it and a
    # container missing it is invisible to the very sweep meant to remove it.
    assert state.container.id_labels["grove.managed"] == "1"
    assert state.container.id_labels["grove.scope"] == "workspace"
    assert state.tmux_session in fake_tmux.sessions


def test_a_repo_with_its_own_devcontainer_uses_it_rather_than_the_packaged_default(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    _write_devcontainer(tmp_repo)
    cli = FakeCli()
    mgr = _manager(tmp_repo, tmp_path, cli=cli)

    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="own-config"))

    assert cli.reads and cli.reads[0][1] is None  # discovered, not overridden


# ─── arm 4: unavailability falls back, loudly and durably ───────────────────


def test_an_unavailable_runtime_falls_back_to_host_and_persists_the_reason(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cli = FakeCli(available=False)
    mgr = _manager(tmp_repo, tmp_path, cli=cli)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="no-cli"))

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is not None
    assert "unavailable" in state.runtime_fallback_reason
    # The probe ran BEFORE any side effect, so nothing container-side was even
    # attempted — that is what makes the fallback free of rollback work.
    assert cli.ups == []
    assert cli.reads == []
    assert state.tmux_session in fake_tmux.sessions
    # It survives a round-trip: the reason is the only thing that later
    # authorizes a respawn promotion.
    assert mgr.store.get(state.id).runtime_fallback_reason == state.runtime_fallback_reason


def test_an_unreachable_engine_is_also_a_fallback_not_a_failure(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    mgr = _manager(tmp_repo, tmp_path, cli=FakeCli(), preflight=FakePreflight(ready=False))

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="engine-down"))

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is not None
    assert "engine is unreachable" in state.runtime_fallback_reason


def test_arm_four_reports_the_failing_preflight_check_verbatim(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Doctor and the create probe are ONE definition, not two that agree today.

    The invariant the docs claim: a container check exists once, in
    ``HostPreflight``, and the create path consumes it. Pinned by feeding the
    resolver a check no probe of its own could ever have produced and asserting
    the persisted reason carries that check's own name, detail and hint — which
    is only possible if the reason came from preflight rather than from a
    duplicated probe.
    """
    preflight = FakePreflight(ready=False, detail="socket /var/run/docker.sock is gone")
    mgr = _manager(tmp_repo, tmp_path, cli=FakeCli(), preflight=preflight)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="drift"))

    assert state.runtime is Runtime.HOST
    reason = state.runtime_fallback_reason or ""
    assert "docker daemon" in reason
    assert "socket /var/run/docker.sock is gone" in reason
    assert "start the Docker daemon" in reason


# ─── arm 5: a failing `up` is fatal, never a downgrade ───────────────────────


def test_a_failed_up_rolls_the_workspace_back_and_never_downgrades_to_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, init_logs: Path
) -> None:
    cli = FakeCli(up_fails="feature install exploded", failed_container_id="ctr-broken")
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    mgr = _manager(tmp_repo, tmp_path, cli=cli, store=store)

    with pytest.raises(GroveError) as excinfo:
        mgr.create(CreateWorkspaceRequest(agent_name="claude", title="broken-env"))

    message = str(excinfo.value)
    assert "feature install exploded" in message
    # The container the CLI managed to create is named and KEPT for diagnosis.
    assert "ctr-broken" in message
    assert "KEPT" in message
    # Nothing in Grove ever references this container again (it was never
    # persisted), so the message itself must be actionable: name the exact
    # command that removes it, not just the id.
    assert "docker rm -f ctr-broken" in message
    # The workspace itself is gone — a broken environment must never silently
    # become a host workspace.
    assert store.load_all() == []
    assert fake_tmux.sessions == set()
    # The log was written AS the provision ran, so it survives the rollback and
    # still carries the CLI's own progress output.
    provision_logs = list(init_logs.glob("*-provision.log"))
    assert provision_logs, "the provision log must survive the create rollback"
    assert "pulling base image" in provision_logs[0].read_text(encoding="utf-8")


# ─── migration: existing records load as host workspaces ────────────────────


def test_a_record_written_before_runtime_selection_decodes_as_a_host_workspace(
    tmp_path: Path,
) -> None:
    """The decoder default IS the migration story — no version bump, no step."""
    legacy = {
        "version": 1,
        "workspaces": {
            "ws1": {
                "id": "ws1",
                "title": "legacy",
                "repo_root": str(tmp_path),
                "branch": "feat/x",
                "base_branch": "main",
                "worktree_path": str(tmp_path / "wt"),
                "tmux_session": "grove-legacy",
                "agent_name": "claude",
                "status": "running",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    state = JsonWorkspaceStore(path=path).get("ws1")

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is None
    assert state.container is None
    assert state.provision_status is None


def test_the_runtime_fields_round_trip_through_the_store(tmp_path: Path) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    container = ContainerRuntimeState(
        container_id="ctr-abc",
        remote_workspace_folder=REMOTE_FOLDER,
        id_labels={"grove.workspace": "ws1"},
        provisioned=True,
    )
    store.save(_state(tmp_path, runtime=Runtime.CONTAINER, container=container))

    loaded = store.get("ws1")

    assert loaded.runtime is Runtime.CONTAINER
    assert loaded.container == container
    assert loaded.runtime_fallback_reason == "docker was down"


def _state(
    tmp_path: Path,
    *,
    runtime: Runtime,
    container: ContainerRuntimeState | None = None,
) -> WorkspaceState:
    from datetime import UTC, datetime  # noqa: PLC0415 - local to the helper

    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id="ws1",
        title="t",
        repo_root=str(tmp_path),
        branch="feat/x",
        base_branch="main",
        worktree_path=str(tmp_path / "wt"),
        tmux_session="grove-t",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        runtime=runtime,
        runtime_fallback_reason="docker was down",
        container=container,
    )


# ─── the invariant: only create consults the cascade ────────────────────────


def test_resume_and_respawn_never_re_resolve_the_runtime_from_config(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A flipped default must not move an existing workspace into a container.

    The regression this guards is silent and severe: every paused workspace
    would change isolation on its next resume, for work already in flight.
    """
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    off = _manager(tmp_repo, tmp_path, container_enabled=False, store=store)
    state = off.create(CreateWorkspaceRequest(agent_name="claude", title="stays-host"))
    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is None

    off.pause(state.id)

    # A NEW manager with containers enabled and a perfectly healthy runtime.
    cli = FakeCli()
    on = _manager(tmp_repo, tmp_path, cli=cli, store=store)
    resumed = on.resume(state.id)
    assert resumed.runtime is Runtime.HOST
    assert cli.ups == []

    fake_tmux.kill_session(resumed.tmux_session)
    respawned = on.respawn(resumed.id)
    assert respawned.runtime is Runtime.HOST
    assert cli.ups == []


# ─── promotion: respawn is the conversion verb ──────────────────────────────


def test_respawn_promotes_a_fallback_workspace_and_clears_the_reason(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    down = _manager(tmp_repo, tmp_path, cli=FakeCli(available=False), store=store)
    state = down.create(CreateWorkspaceRequest(agent_name="claude", title="promote-me"))
    assert state.runtime_fallback_reason is not None

    fake_tmux.kill_session(state.tmux_session)
    cli = FakeCli()
    up = _manager(tmp_repo, tmp_path, cli=cli, store=store)
    promoted = up.respawn(state.id)

    assert promoted.runtime is Runtime.CONTAINER
    assert promoted.runtime_fallback_reason is None
    assert promoted.container is not None
    assert promoted.provision_status is ProvisionStatus.OK
    assert len(cli.ups) == 1
    # The promotion survives the store round-trip the respawn ends with.
    assert store.get(state.id).runtime is Runtime.CONTAINER


def test_a_live_fallback_workspace_promotes_without_killing_its_session_first(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The documented recovery must work from the state a user is actually in.

    The banner says "fix the runtime, then `grove respawn` to promote", and a
    fallback workspace is ALIVE on the host — never OFFLINE. Gating respawn on
    OFFLINE alone made that instruction unreachable through every verb a user
    has: respawn refused with "expected offline", and pausing first only moved
    the complaint to "expected offline, got paused". The promotion machinery
    existed the whole time and nothing could reach it. Note this test does NOT
    kill the tmux session — that omission is the assertion.
    """
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    down = _manager(tmp_repo, tmp_path, cli=FakeCli(available=False), store=store)
    state = down.create(CreateWorkspaceRequest(agent_name="claude", title="live-fallback"))
    assert state.runtime_fallback_reason is not None
    assert state.status is not WorkspaceStatus.OFFLINE

    up = _manager(tmp_repo, tmp_path, cli=FakeCli(), store=store)
    promoted = up.respawn(state.id)

    assert promoted.runtime is Runtime.CONTAINER
    assert promoted.runtime_fallback_reason is None


def test_respawn_never_auto_upgrades_a_workspace_that_chose_the_host(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    cli = FakeCli()
    mgr = _manager(tmp_repo, tmp_path, cli=cli, store=store)
    state = mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title="chose-host", runtime=Runtime.HOST)
    )

    fake_tmux.kill_session(state.tmux_session)
    respawned = mgr.respawn(state.id)

    assert respawned.runtime is Runtime.HOST
    assert cli.ups == []


# ─── a nested project keeps its own workdir inside the container ───────────


def test_a_nested_project_resolves_its_workdir_under_the_reported_workspace_folder(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The launch spec carries the worktree ROOT and the agent cwd separately.

    Conflating them would collapse every nested project to the mount root: the
    offset between the two is what places the agent inside the container.
    """
    del fake_tmux
    nested = tmp_repo / "services" / "api"
    nested.mkdir(parents=True)
    mgr = _manager(tmp_repo, tmp_path)

    state = mgr.create(
        CreateWorkspaceRequest(agent_name="claude", title="nested", project_cwd=nested)
    )

    assert state.project_subpath == "services/api"
    assert state.container is not None
    workdir = state.container.workdir(worktree=Path(state.worktree_path), cwd=state.agent_cwd)
    assert workdir == f"{REMOTE_FOLDER}/services/api"


def test_a_flat_workspace_execs_at_the_reported_workspace_folder(tmp_path: Path) -> None:
    container = ContainerRuntimeState(remote_workspace_folder=REMOTE_FOLDER)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    assert container.workdir(worktree=worktree, cwd=worktree) == REMOTE_FOLDER


def test_the_container_backend_execs_by_id_label_and_cds_only_when_nested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from grove.core import tmux as tmux_mod  # noqa: PLC0415 - local to this test

    laid_out: list[dict[str, object]] = []
    monkeypatch.setattr(tmux_mod, "create_session", lambda *a, **k: None)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", lambda name, **kw: laid_out.append(kw))
    worktree = tmp_path / "wt"
    (worktree / "services" / "api").mkdir(parents=True)
    container = ContainerRuntimeState(
        container_id="ctr-abc",
        remote_workspace_folder=REMOTE_FOLDER,
        id_labels={"grove.workspace": "ws1"},
        provisioned=True,
    )
    spec = LaunchSpec(
        session_name="test-sess",
        cwd=worktree / "services" / "api",
        command="claude",
        decoration=("--session-id", "abc"),
        env={"FOO": "bar"},
        env_unset=("CLAUDE_CONFIG_DIR",),
        cfg=GroveConfig(),
        worktree=worktree,
        kind="claude_code",
        container=container,
    )

    DevcontainerLaunchBackend(cli=FakeCli()).launch(spec)

    command = str(laid_out[0]["command"])
    assert "devcontainer exec" in command
    assert "--id-label grove.workspace=ws1" in command
    assert "--remote-env FOO=bar" in command
    assert f"cd {REMOTE_FOLDER}/services/api" in command
    # Nothing leaks into the HOST pane env — it all crossed via --remote-env.
    assert laid_out[0]["env"] == {}
    assert laid_out[0]["env_unset"] == ()


def test_the_container_backend_refuses_to_launch_without_a_container(
    tmp_path: Path,
) -> None:
    """Failing loudly beats the one thing the runtime contract forbids: a silent
    launch on the host under a workspace that asked to be isolated.

    The refusal is about the ABSENCE of a container, not about an unprovisioned
    one. Provisioning failure is fatal at one site,
    `WorkspaceManager._provision_container`, which every launch verb passes
    through first — so a second `provisioned` check here could never fire, and
    the state it claimed to guard is refused upstream and persisted (see
    `tests/core/test_container_liveness.py`)."""
    from grove.core.errors import ContainerError  # noqa: PLC0415 - local to this test

    spec = LaunchSpec(
        session_name="s",
        cwd=tmp_path,
        command="claude",
        decoration=(),
        env={},
        env_unset=(),
        cfg=GroveConfig(),
        worktree=tmp_path,
        kind="claude_code",
        container=None,
    )
    with pytest.raises(ContainerError, match="no container to exec into"):
        DevcontainerLaunchBackend(cli=FakeCli()).launch(spec)


# ─── a tmux-capable container gets no host session at all ──────────────────


def _tmux_container(*, tmux_command: str) -> ContainerRuntimeState:
    return ContainerRuntimeState(
        container_id="ctr-abc",
        remote_workspace_folder=REMOTE_FOLDER,
        id_labels={"grove.workspace": "ws1"},
        provisioned=True,
        tmux_command=tmux_command,
    )


def _spec(tmp_path: Path, *, tmux_command: str) -> LaunchSpec:
    worktree = tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    return LaunchSpec(
        session_name="test-sess",
        cwd=worktree,
        command="claude",
        decoration=("--session-id", "abc"),
        env={"FOO": "bar"},
        env_unset=(),
        cfg=GroveConfig(),
        worktree=worktree,
        kind="claude_code",
        container=_tmux_container(tmux_command=tmux_command),
    )


def _no_host_tmux(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any host tmux session the launch tries to create."""
    from grove.core import tmux as tmux_mod  # noqa: PLC0415 - local to this test

    created: list[str] = []
    monkeypatch.setattr(tmux_mod, "create_session", lambda name, **kw: created.append(name))
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", lambda name, **kw: None)
    return created


def test_the_container_backend_starts_the_agent_inside_the_container_and_no_host_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A host session would be a pure shadow of the in-container one.

    Its pane would run `devcontainer exec … tmux new-session -A` into a
    session that already exists, and stay on as an attached client CLAMPING
    every later client's size (measured: a client asking for 200x50 got
    161x41). So the agent starts DETACHED in the container and `attach` execs
    straight in.
    """
    created = _no_host_tmux(monkeypatch)
    cli = FakeCli()

    DevcontainerLaunchBackend(cli=cli).launch(_spec(tmp_path, tmux_command="/grove/tmux/tmux"))

    assert created == []
    agent = " ".join(cli.execs[0]["argv"])
    assert "new-session -A -d -s agent" in agent
    # The decoration still rides through the `sh -c … "$@"` hand-off, and the
    # agent's death is still inspectable inside the container.
    assert agent.endswith("--session-id abc")
    assert "remain-on-exit on" in agent
    assert cli.execs[0]["remote_env"] == {"FOO": "bar"}


def test_the_container_backend_also_puts_the_shell_session_in_the_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`grove shell` and the shell a user switches to after attaching are one.

    With no host window 0 to type it into, the launch starts the same
    `-A -s shell` session detached in the container.
    """
    _no_host_tmux(monkeypatch)
    cli = FakeCli()

    DevcontainerLaunchBackend(cli=cli).launch(_spec(tmp_path, tmux_command="/grove/tmux/tmux"))

    shell = " ".join(cli.execs[1]["argv"])
    assert "new-session -A -d -s shell" in shell


def test_a_container_with_no_tmux_still_runs_the_agent_in_a_host_pane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one deliberately preserved exception, on the launch's own predicate.

    `tmux_command == ""` means the image ships no tmux and Grove has no bundle
    for its architecture. The agent genuinely runs as a bare exec in a host
    pane, so the host session is correct and `attach` targets it.
    """
    created = _no_host_tmux(monkeypatch)
    cli = FakeCli()

    DevcontainerLaunchBackend(cli=cli).launch(_spec(tmp_path, tmux_command=""))

    assert created == ["test-sess"]
    # Nothing was exec'd to start an agent: the pane runs the exec itself.
    assert cli.execs == []


# ─── the deadline invariant ─────────────────────────────────────────────────


def test_the_up_timeout_sits_inside_every_client_deadline() -> None:
    """A client that gives up before the engine's own bound is the worst shape.

    The daemon may finish the create it was told had failed, leaving a container
    with no record. Both the HTTP client and the MCP server (which speaks through
    that same `GroveClient`) inherit this one budget.
    """
    assert ContainerConfig().up_timeout_seconds < GroveClient._LIFECYCLE_TIMEOUT_S


# ─── the wire keeps decoding for older clients ──────────────────────────────


def test_the_state_view_defaults_every_runtime_field_to_an_ordinary_host_workspace(
    tmp_path: Path,
) -> None:
    view = WorkspaceStateView.from_state(_state(tmp_path, runtime=Runtime.HOST))

    assert view.runtime is Runtime.HOST
    assert view.container is None
    # A payload from an older daemon (no runtime keys at all) still decodes.
    trimmed = view.model_dump(mode="json")
    for key in ("runtime", "runtime_fallback_reason", "provision_status", "container"):
        trimmed.pop(key)
    assert WorkspaceStateView.model_validate(trimmed).runtime is Runtime.HOST
