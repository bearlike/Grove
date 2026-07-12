"""Container runtime driver (#65): the Docker driver's argv shape + the
DockerExecLaunchBackend that hosts `docker exec` in a tmux pane.

No real Docker is ever invoked — `subprocess.run` is monkeypatched to a
recorder in the driver tests, and the launch-backend test injects a fake
`ContainerDriver` plus the shared `fake_tmux` seam. The point mirrors the tmux
backend's test: prove the assembled command routes through the container
boundary correctly and that the pane is still built so attach/steer keep working.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from grove.core import container as container_mod
from grove.core.config import ContainerConfig, GroveConfig
from grove.core.container import ContainerStatus, DockerContainerDriver
from grove.core.errors import ContainerError
from grove.core.launch import DockerExecLaunchBackend, LaunchSpec
from tests.conftest import FakeTmux

_NO_ENV: Mapping[str, str] = {}


def _cfg(**overrides: object) -> ContainerConfig:
    base: dict[str, object] = {"enabled": True, "image": "python:3.12"}
    base.update(overrides)
    return ContainerConfig.model_validate(base)


# ─── DockerContainerDriver: argv shape + narrowing ──────────────────────────


class _Recorder:
    """Records every argv passed to a stand-in `subprocess.run`, replies scripted."""

    def __init__(self, replies: list[SimpleNamespace]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, argv: Sequence[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append(list(argv))
        return self._replies.pop(0)


def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def test_exec_argv_maps_env_workdir_user_and_tty_to_flags() -> None:
    cfg = _cfg(exec_user="1000:1000")
    argv = DockerContainerDriver().exec_argv(
        cfg, name="grove-x", env={"FOO": "bar"}, workdir="/workspace/pkg", tty=True
    )
    assert argv == [
        "docker", "exec", "-i", "-t",
        "-u", "1000:1000",
        "-w", "/workspace/pkg",
        "-e", "FOO=bar",
        "grove-x",
    ]  # fmt: skip


def test_exec_argv_omits_optional_flags_when_unset() -> None:
    argv = DockerContainerDriver().exec_argv(_cfg(), name="grove-x")
    assert argv == ["docker", "exec", "-i", "grove-x"]


def test_up_runs_a_detached_container_with_the_worktree_bind_mounted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # inspect() → absent, then `docker run` succeeds.
    rec = _Recorder([_fail(), _ok()])  # inspect: rc!=0 (absent); run: ok
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().up(
        _cfg(mounts=("/cache:/cache",), network="grove-net"),
        name="grove-x",
        worktree=tmp_path,
    )
    run_argv = rec.calls[1]
    assert run_argv[:5] == ["docker", "run", "-d", "--name", "grove-x"]
    assert "-w" in run_argv and "/workspace" in run_argv
    assert f"{tmp_path}:/workspace" in run_argv
    assert "/cache:/cache" in run_argv
    assert run_argv[run_argv.index("--network") + 1] == "grove-net"
    # Kept alive by an idle entrypoint so the agent can be exec'd in later.
    assert run_argv[-3:] == ["python:3.12", "sleep", "infinity"]


def test_up_is_idempotent_when_already_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _Recorder([_ok("true running")])  # inspect: running → no further calls
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().up(_cfg(), name="grove-x", worktree=tmp_path)
    assert len(rec.calls) == 1  # only the inspect, no run/start


def test_up_starts_an_existing_stopped_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _Recorder([_ok("false exited"), _ok()])  # inspect: exists+stopped; start ok
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().up(_cfg(), name="grove-x", worktree=tmp_path)
    assert rec.calls[1] == ["docker", "start", "grove-x"]


def test_lifecycle_op_raises_container_error_on_docker_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _Recorder([_fail(), _fail("no such image")])  # inspect absent; run fails
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    with pytest.raises(ContainerError, match="no such image"):
        DockerContainerDriver().up(_cfg(), name="grove-x", worktree=tmp_path)


def test_up_without_image_raises_before_touching_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _Recorder([])
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    with pytest.raises(ContainerError, match="image"):
        DockerContainerDriver().up(_cfg(image=""), name="grove-x", worktree=tmp_path)
    assert rec.calls == []


def test_build_no_ops_without_a_dockerfile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = _Recorder([])
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().build(_cfg(), worktree=tmp_path)
    assert rec.calls == []


def test_build_tags_the_image_from_the_dockerfile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = _Recorder([_ok()])
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().build(
        _cfg(image="grove/dev", dockerfile="Dockerfile"), worktree=tmp_path
    )
    build_argv = rec.calls[0]
    assert build_argv[:4] == ["docker", "build", "-t", "grove/dev"]
    assert build_argv[-1] == str((tmp_path / ".").resolve())


def test_inspect_never_raises_and_parses_running_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_mod.subprocess, "run", _Recorder([_ok("true running")]))
    assert DockerContainerDriver().inspect(_cfg(), name="x") == ContainerStatus(
        exists=True, running=True, status="running"
    )


def test_inspect_absent_container_collapses_to_not_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(container_mod.subprocess, "run", _Recorder([_fail()]))
    assert DockerContainerDriver().inspect(_cfg(), name="x") == ContainerStatus(
        exists=False, running=False
    )


def test_inspect_swallows_subprocess_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **k: object) -> object:
        raise OSError("docker not found")

    monkeypatch.setattr(container_mod.subprocess, "run", _boom)
    assert DockerContainerDriver().inspect(_cfg(), name="x").exists is False


def test_down_removes_only_an_existing_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder([_ok("false exited"), _ok()])  # inspect: exists; rm ok
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().down(_cfg(), name="grove-x")
    assert rec.calls[1] == ["docker", "rm", "-f", "grove-x"]


def test_down_no_ops_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder([_fail()])  # inspect: absent
    monkeypatch.setattr(container_mod.subprocess, "run", rec)
    DockerContainerDriver().down(_cfg(), name="grove-x")
    assert len(rec.calls) == 1


# ─── DockerExecLaunchBackend: hosts `docker exec` in a tmux pane ─────────────


@dataclass
class _FakeDriver:
    """Records driver calls; the launch backend's container-side seam under test."""

    ups: list[str] = field(default_factory=list)
    builds: int = 0
    exec_argvs: list[list[str]] = field(default_factory=list)

    def build(self, cfg: ContainerConfig, *, worktree: Path) -> None:
        self.builds += 1

    def up(self, cfg: ContainerConfig, *, name: str, worktree: Path) -> None:
        self.ups.append(name)

    def exec(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        argv: Sequence[str],
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
    ) -> int:
        return 0

    def exec_argv(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
        tty: bool = False,
    ) -> list[str]:
        # Delegate to the real (pure, no-subprocess) builder so the backend test
        # exercises the true argv shape, then record it.
        built = DockerContainerDriver().exec_argv(cfg, name=name, env=env, workdir=workdir, tty=tty)
        self.exec_argvs.append(built)
        return built

    def stop(self, cfg: ContainerConfig, *, name: str) -> None: ...

    def down(self, cfg: ContainerConfig, *, name: str) -> None: ...

    def inspect(self, cfg: ContainerConfig, *, name: str) -> ContainerStatus:
        return ContainerStatus(exists=True, running=True)


def _spec(tmp_path: Path, *, subpath: str = "") -> LaunchSpec:
    worktree = tmp_path / "wt"
    cwd = worktree / subpath if subpath else worktree
    cfg = GroveConfig.model_validate(
        {"container": {"enabled": True, "image": "python:3.12", "exec_user": "dev"}}
    )
    return LaunchSpec(
        session_name="grove-abc",
        cwd=cwd,
        command="claude",
        decoration=("--session-id", "sid-1"),
        env={"FOO": "bar"},
        env_unset=("LEAK",),
        cfg=cfg,
        worktree=worktree,
    )


def test_backend_declares_it_provides_a_pane() -> None:
    assert DockerExecLaunchBackend.provides_pane is True


def test_launch_builds_container_then_hosts_docker_exec_in_tmux(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    driver = _FakeDriver()
    DockerExecLaunchBackend(driver=driver).launch(_spec(tmp_path))

    # Container was ensured up before any pane work.
    assert driver.ups == ["grove-abc"]
    # The pane hosts the `docker exec` prefix, and the agent command follows.
    assert "grove-abc" in fake_tmux.sessions
    session, command = fake_tmux.layouts[0]
    assert session == "grove-abc"
    assert command.startswith("docker exec -i -t -u dev")
    assert "-e FOO=bar" in command
    assert command.endswith("grove-abc claude")
    # The adapter decoration still flows through the SAME layout path unchanged.
    _, decoration = fake_tmux.launch_decorations[0]
    assert decoration == ["--session-id", "sid-1"]
    # Host env is empty — env crossed the boundary via `-e`, not shell exports.
    _, env, env_unset = fake_tmux.launch_envs[0]
    assert env == {} and env_unset == ()


def test_launch_maps_nested_cwd_to_the_container_workdir(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    driver = _FakeDriver()
    DockerExecLaunchBackend(driver=driver).launch(_spec(tmp_path, subpath="services/api"))
    assert driver.exec_argvs[0][driver.exec_argvs[0].index("-w") + 1] == "/workspace/services/api"
