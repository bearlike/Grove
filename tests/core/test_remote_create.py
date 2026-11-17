"""A create onto a remote runtime starts a workload that dials back and keeps its secret."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import AgentSpec, CloudRunJobRuntime, DaemonConfig, GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import CapabilityUnavailable, ConfigError, ContainerError
from grove.core.manager import WorkspaceManager
from grove.core.native_worker import NativeWorkerConfig
from grove.core.runtimes import (
    PUSH_TOKEN_ENV,
    WORKER_CONFIG_ENV,
    WORKER_TOKEN_ENV,
    ProvisionRefused,
    RemoteHandle,
    WorkspaceSpec,
)
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from tests.core.test_runtimes_contract import FakeDriver, FakeHibernatingDriver


class RecordingDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.specs: list[WorkspaceSpec] = []
        self.emitted: list[str] = []

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        rendered = json.dumps({"image": spec.image, "env": dict(spec.env)})
        self.emitted.append(rendered)
        return rendered

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        self.specs.append(spec)
        return super().up(spec)


def _cfg(**runtimes: CloudRunJobRuntime) -> GroveConfig:
    return GroveConfig(
        agents=[AgentSpec(name="claude", command="claude", kind="claude_code", native=True)],
        daemon=DaemonConfig(mailbox_url="https://grove.example.test"),
        runtimes=runtimes,
    )


def _block() -> CloudRunJobRuntime:
    return CloudRunJobRuntime(project="p", region="r", job="j", image="grove-runtime:1")


@pytest.fixture
def repo(tmp_repo: Path, tmp_state_dir: Path) -> Path:
    del tmp_state_dir
    subprocess.run(
        ["git", "remote", "add", "origin", "https://git.example.test/acme/app.git"],
        cwd=tmp_repo,
        check=True,
    )
    return tmp_repo


def _manager(repo: Path, cfg: GroveConfig, driver: FakeDriver) -> WorkspaceManager:
    manager = WorkspaceManager(
        repo_root=repo, cfg=cfg, store=JsonWorkspaceStore(path=repo.parent / "state.json")
    )
    manager._runtimes.remote._factory = lambda name, block: driver
    return manager


def test_a_remote_create_persists_the_handle_and_hands_the_worker_its_branch(
    repo: Path,
) -> None:
    driver = RecordingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )

    assert state.runtime is Runtime.REMOTE and state.native
    assert state.remote == RemoteHandle("gcp", "exec-1")
    assert manager._store.get(state.id).remote == state.remote
    [spec] = driver.specs
    assert spec.image == "grove-runtime:1" and spec.command == ("grove-native-worker",)
    worker = NativeWorkerConfig.model_validate_json(spec.env[WORKER_CONFIG_ENV])
    assert (worker.repo_url, worker.branch) == (
        "https://git.example.test/acme/app.git",
        state.branch,
    )
    assert worker.start_commit == state.base_commit
    assert worker.daemon_url == "https://grove.example.test" and worker.daemon_token is None


def test_the_worker_bearer_reaches_no_spec_record_or_repr(repo: Path) -> None:
    driver = RecordingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    [spec] = driver.specs
    token = spec.secret_env[WORKER_TOKEN_ENV]
    assert token
    assert token not in "".join(driver.emitted)
    assert token not in repr(spec)
    assert token not in (repo.parent / "state.json").read_text()
    assert token not in json.dumps(dict(spec.env))
    assert state.remote is not None


@pytest.mark.parametrize(
    ("cfg", "request_runtime", "reason"),
    [
        (_cfg(), None, "no runtimes are configured"),
        (_cfg(a=_block(), b=_block()), None, "name the remote runtime"),
        (_cfg(gcp=_block()), "missing", "not a configured resident runtime"),
        (
            _cfg(gcp=_block()).model_copy(update={"daemon": DaemonConfig()}),
            None,
            "daemon.mailbox_url",
        ),
    ],
)
def test_a_remote_create_that_cannot_dial_back_refuses_before_any_side_effect(
    repo: Path, cfg: GroveConfig, request_runtime: str | None, reason: str
) -> None:
    driver = RecordingDriver()
    manager = _manager(repo, cfg, driver)
    with pytest.raises(CapabilityUnavailable, match=reason):
        manager.create(
            CreateWorkspaceRequest(
                title="remote",
                agent_name="claude",
                runtime=Runtime.REMOTE,
                remote_runtime=request_runtime,
            )
        )
    assert driver.specs == []
    assert manager.list() == []
    assert not (repo / ".worktrees").exists()


def test_a_refused_workload_rolls_the_create_back(repo: Path) -> None:
    manager = _manager(repo, _cfg(gcp=_block()), FakeDriver(quota=0))
    with pytest.raises(ProvisionRefused):
        manager.create(
            CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
        )
    assert manager.list() == []


def test_an_environment_over_the_platform_limit_refuses_whole() -> None:
    spec = WorkspaceSpec("w", "i", ("c",), env={"K": "x" * 100}, secret_env={"T": "t"})
    spec.require_env_within(200, "Cloud Run")
    with pytest.raises(ProvisionRefused, match=r"103 bytes, over Lambda's 64-byte"):
        spec.require_env_within(64, "Lambda")


def test_a_thaw_whose_workload_vanished_starts_a_fresh_one_on_the_branch(repo: Path) -> None:
    class Forgetful(RecordingDriver):
        def thaw(self, handle: RemoteHandle) -> RemoteHandle:
            raise ContainerError("no such sandbox")

    driver = Forgetful()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    assert state.remote is not None
    manager._store.save(replace(state, frozen_at=datetime.now(UTC)))
    driver.rm(state.remote)

    thawed = manager.thaw(state.id)

    assert thawed.frozen_at is None
    assert thawed.remote == RemoteHandle("gcp", "exec-2")
    assert len(driver.specs) == 2


def test_a_thaw_that_fails_while_the_workload_lives_stays_frozen(repo: Path) -> None:
    class Stuck(RecordingDriver):
        def thaw(self, handle: RemoteHandle) -> RemoteHandle:
            raise ContainerError("resume refused")

    manager = _manager(repo, _cfg(gcp=_block()), Stuck())
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    manager._store.save(replace(state, frozen_at=datetime.now(UTC)))
    with pytest.raises(ContainerError, match="resume refused"):
        manager.thaw(state.id)
    assert manager._store.get(state.id).frozen_at is not None


def _hibernated(repo: Path) -> tuple[WorkspaceManager, FakeHibernatingDriver, str]:
    driver = FakeHibernatingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    manager._store.save(replace(state, frozen_at=datetime.now(UTC)))
    manager.hibernate(state.id)
    return manager, driver, state.id


def test_a_frozen_remote_workspace_hibernates_to_a_snapshot_handle(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    state = manager._store.get(workspace_id)
    assert state.hibernated_at is not None and state.frozen_at is not None
    assert state.remote is not None and state.remote.ref.startswith("snapshot:")
    assert driver.snapshots


def test_waking_a_hibernated_workspace_runs_before_the_absent_fallback(repo: Path) -> None:
    """The snapshot handle reads ABSENT as a sandbox, which would otherwise respawn fresh."""
    manager, driver, workspace_id = _hibernated(repo)
    ups_before = len(driver.ups)

    woken = manager.thaw(workspace_id)

    assert len(driver.woken) == 1, "wake was not called"
    assert len(driver.ups) == ups_before, "a fresh up ran instead of the wake"
    assert woken.frozen_at is None and woken.hibernated_at is None
    assert woken.remote is not None and not woken.remote.ref.startswith("snapshot:")
    [spec] = driver.woken
    assert spec.secret_env[WORKER_TOKEN_ENV], "wake carries a freshly minted bearer"


def test_wake_keeps_its_snapshot_until_the_restored_owner_registers(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)

    woken = manager.thaw(workspace_id)

    pending = manager._store.get(workspace_id).pending_snapshot
    assert pending is not None and pending.ref.startswith("snapshot:")
    assert driver.snapshots, "a restore that has not dialled in can still fail"
    assert driver.forgotten == []
    assert woken.remote is not None and woken.remote != pending


def test_owner_registration_confirms_a_wake_and_forgets_its_snapshot_once(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    manager.thaw(workspace_id)

    manager.confirm_wake(workspace_id)
    manager.confirm_wake(workspace_id)

    assert driver.snapshots == set()
    assert len(driver.forgotten) == 1
    assert manager._store.get(workspace_id).pending_snapshot is None


def test_a_restart_between_wake_and_registration_still_forgets_the_snapshot(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    manager.thaw(workspace_id)

    restarted = _manager(repo, _cfg(gcp=_block()), driver)
    restarted.confirm_wake(workspace_id)

    assert driver.snapshots == set()
    assert len(driver.forgotten) == 1
    assert restarted._store.get(workspace_id).pending_snapshot is None


def test_rehibernate_forgets_an_unconfirmed_older_snapshot(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    woken = manager.thaw(workspace_id)
    old_snapshot = woken.pending_snapshot
    assert old_snapshot is not None
    manager._store.save(replace(woken, frozen_at=datetime.now(UTC)))

    rehibernated = manager.hibernate(workspace_id)

    assert driver.forgotten == [old_snapshot]
    assert rehibernated.remote is not None and rehibernated.remote != old_snapshot
    assert driver.snapshots == {rehibernated.remote.ref.removeprefix("snapshot:")}


def test_killing_a_woken_workspace_forgets_its_pending_snapshot(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    manager.thaw(workspace_id)

    manager.kill(workspace_id)

    assert driver.snapshots == set()
    assert len(driver.forgotten) == 1


def test_killing_a_hibernated_workspace_forgets_its_snapshot(repo: Path) -> None:
    manager, driver, workspace_id = _hibernated(repo)
    manager.kill(workspace_id)
    assert driver.snapshots == set()


def test_a_runtime_that_cannot_hibernate_stays_frozen(repo: Path) -> None:
    driver = RecordingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    manager._store.save(replace(state, frozen_at=datetime.now(UTC)))
    assert manager.hibernate(state.id).hibernated_at is None


def test_the_sandboxed_agent_gets_its_env_and_resolved_values_stay_private(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-telemetry-secret")
    cfg = _cfg(gcp=_block()).model_copy(
        update={
            "agents": [
                AgentSpec(
                    name="claude",
                    command="claude",
                    kind="claude_code",
                    native=True,
                    env={"ANTHROPIC_BASE_URL": "https://gateway.example.test"},
                )
            ]
        }
    )
    cfg = cfg.model_copy(
        update={
            "telemetry": cfg.telemetry.model_copy(
                update={"enabled": True, "passthrough_kinds": ("claude_code",)}
            )
        }
    )
    driver = RecordingDriver()
    manager = _manager(repo, cfg, driver)
    manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    [spec] = driver.specs
    assert spec.env["ANTHROPIC_BASE_URL"] == "https://gateway.example.test"
    assert "GROVE_PHASE_FILE" not in spec.env and "GROVE_PHASE_FILE" not in spec.secret_env
    secret_values = "".join(spec.secret_env.values())
    assert "sk-telemetry-secret" in secret_values
    assert "sk-telemetry-secret" not in "".join(driver.emitted)


def test_a_reference_in_agent_env_rides_the_private_channel(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_state_dir: Path
) -> None:
    from grove.core import paths  # noqa: PLC0415
    from grove.core.config import load_config  # noqa: PLC0415

    del tmp_state_dir
    monkeypatch.setenv("SANDBOX_PROVIDER_KEY", "sk-provider-secret")
    user = paths.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        json.dumps(
            {
                "container": {"enabled": False},
                "daemon": {"mailbox_url": "https://grove.example.test"},
                "runtimes": {"gcp": _block().model_dump()},
                "agents": [
                    {
                        "name": "claude",
                        "command": "claude",
                        "kind": "claude_code",
                        "native": True,
                        "env": {
                            "ANTHROPIC_API_KEY": "${SANDBOX_PROVIDER_KEY}",
                            "ANTHROPIC_BASE_URL": "https://gateway.example.test",
                        },
                    }
                ],
            }
        )
    )
    cfg = load_config(repo)
    driver = RecordingDriver()
    manager = _manager(repo, cfg, driver)
    manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    [spec] = driver.specs
    assert spec.secret_env["ANTHROPIC_API_KEY"] == "sk-provider-secret"
    assert "ANTHROPIC_API_KEY" not in spec.env
    assert spec.env["ANTHROPIC_BASE_URL"] == "https://gateway.example.test"
    assert "sk-provider-secret" not in "".join(driver.emitted)


def test_a_refused_remote_create_revokes_the_worker_bearer_it_minted(repo: Path) -> None:
    from grove.core.auth import AuthInvalidToken, SessionStore  # noqa: PLC0415

    class Refusing(RecordingDriver):
        def up(self, spec: WorkspaceSpec) -> RemoteHandle:
            self.specs.append(spec)
            raise ProvisionRefused("quota")

    driver = Refusing()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    with pytest.raises(ProvisionRefused):
        manager.create(
            CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
        )
    [spec] = driver.specs
    with pytest.raises(AuthInvalidToken):
        SessionStore().validate(spec.secret_env[WORKER_TOKEN_ENV])


def _with_push_token(cfg: GroveConfig, name: str) -> GroveConfig:
    return cfg.model_copy(
        update={"worktree": cfg.worktree.model_copy(update={"push_token_env": name})}
    )


def test_the_push_token_rides_only_the_private_channel(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1068: the workload must be able to push its branch, and the token that
    lets it must never reach the emitted spec, the record, or the spec's repr."""
    monkeypatch.setenv("ACME_PUSH_TOKEN", "push-secret-1068")
    driver = RecordingDriver()
    manager = _manager(repo, _with_push_token(_cfg(gcp=_block()), "ACME_PUSH_TOKEN"), driver)
    manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    [spec] = driver.specs
    assert spec.secret_env[PUSH_TOKEN_ENV] == "push-secret-1068"
    assert "push-secret-1068" not in json.dumps(dict(spec.env))
    assert "push-secret-1068" not in "".join(driver.emitted)
    assert "push-secret-1068" not in repr(spec)
    assert "push-secret-1068" not in (repo.parent / "state.json").read_text()


def test_no_push_token_configured_hands_the_workload_none(repo: Path) -> None:
    driver = RecordingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    [spec] = driver.specs
    assert PUSH_TOKEN_ENV not in spec.secret_env


def test_a_named_but_unset_push_token_refuses_by_name_before_any_side_effect(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused at admission, naming the variable and the dotted path, rather than
    an hour later as a freeze that can never push."""
    monkeypatch.delenv("ACME_PUSH_TOKEN", raising=False)
    driver = RecordingDriver()
    manager = _manager(repo, _with_push_token(_cfg(gcp=_block()), "ACME_PUSH_TOKEN"), driver)
    with pytest.raises(ConfigError, match=r"worktree\.push_token_env names ACME_PUSH_TOKEN"):
        manager.create(
            CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
        )
    assert driver.specs == []
    assert manager.list() == []
    # BEFORE any side effect: a later refusal (at spec composition) would also
    # roll the record back, but only admission stops the worktree being made.
    assert not (repo / ".worktrees").exists()


@pytest.mark.parametrize("resume", [True, False])
def test_a_remote_respawn_tells_the_new_worker_whether_it_resumes(repo: Path, resume: bool) -> None:
    """#1102: respawn decides `--resume` from the mirrored transcript, so the new
    worker must be told too, or it never seeds its home and the provider is
    asked to resume a session that is not there."""
    driver = RecordingDriver()
    manager = _manager(repo, _cfg(gcp=_block()), driver)
    state = manager.create(
        CreateWorkspaceRequest(title="remote", agent_name="claude", runtime=Runtime.REMOTE)
    )
    manager._runtimes.remote.push = lambda state: None  # type: ignore[method-assign]
    agent = manager._cfg.find_agent("claude")
    assert agent is not None
    decoration = manager._compose_launch(
        agent, state.agent_session_id, state=state, native=True, resume=resume
    )
    manager._respawn_remote(state, agent, decoration, resume=resume)

    worker = NativeWorkerConfig.model_validate_json(driver.specs[-1].env[WORKER_CONFIG_ENV])
    assert worker.resume is resume
