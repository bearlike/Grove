"""Container-runtime side effects — the `docker` CLI, argv-only, `shell=False`.

Sibling of ``git.py`` / ``tmux.py``: the ONE place Grove shells out to a
container engine. ``ContainerDriver`` is the protocol a runtime implements;
``DockerContainerDriver`` is the default over the ``docker`` CLI. A future
Podman driver is a NEW class behind the same protocol (YAGNI — not built now),
never a branch inside this one.

Read / mutate split mirrors ``tmux.py``: lifecycle ops (``build`` / ``up`` /
``stop`` / ``down`` / ``exec``) raise :class:`ContainerError` on a failed
subprocess so the launch fork's rollback handles one type; the best-effort read
(``inspect``) never raises — it returns a status the caller reasons over.

``exec_argv`` is the ONE place the ``docker exec`` invocation is assembled, so
the side-effect ``exec`` and the tmux-hosted launch (``DockerExecLaunchBackend``
in ``launch.py``) can't drift on how env / workdir / user / tty map to flags.
Dependencies flow inward: this module is imported by ``launch.py``; it never
imports ``launch`` (cycle) nor any presentation layer.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger

from grove.core.config import ContainerConfig
from grove.core.errors import ContainerError

# Shared read-only empty env default: keeps the mutable-default (B006) out of
# every signature while `Mapping` guarantees callees never mutate it.
_NO_ENV: Mapping[str, str] = {}


@dataclass(frozen=True, slots=True)
class ContainerStatus:
    """A best-effort snapshot of one container's existence + run state.

    Returned by :meth:`ContainerDriver.inspect`; ``exists=False`` is the sentinel
    for "no such container OR the read failed" — a read never raises, so callers
    treat an unreadable engine the same as a missing container (recreate it).
    """

    exists: bool
    running: bool
    status: str = ""


class ContainerDriver(Protocol):
    """Build / run / exec / tear down a workspace's container.

    One protocol, one concrete default (:class:`DockerContainerDriver`); a
    non-Docker runtime implements the same methods over its own CLI. Lifecycle
    methods raise :class:`ContainerError` on failure; :meth:`inspect` is
    best-effort and never raises.
    """

    def build(self, cfg: ContainerConfig, *, worktree: Path) -> None: ...

    def up(self, cfg: ContainerConfig, *, name: str, worktree: Path) -> None: ...

    def exec(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        argv: Sequence[str],
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
    ) -> int: ...

    def exec_argv(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
        tty: bool = False,
    ) -> list[str]: ...

    def stop(self, cfg: ContainerConfig, *, name: str) -> None: ...

    def down(self, cfg: ContainerConfig, *, name: str) -> None: ...

    def inspect(self, cfg: ContainerConfig, *, name: str) -> ContainerStatus: ...


class DockerContainerDriver:
    """The default :class:`ContainerDriver` over the ``docker`` CLI.

    Every invocation is a list-argv ``subprocess.run(shell=False)`` — no shell,
    no injection surface, cross-platform. ``cfg.docker_bin`` names the binary so
    a drop-in shim needs no code change (still a *docker-compatible* CLI, not the
    Podman-driver seam).
    """

    def build(self, cfg: ContainerConfig, *, worktree: Path) -> None:
        """Build ``cfg.image`` from ``cfg.dockerfile`` in ``worktree``; no-op if
        no Dockerfile is configured (the image is pulled as-is by ``up``)."""
        if not cfg.dockerfile:
            return
        if not cfg.image:
            raise ContainerError(
                "container.dockerfile is set but container.image (the build tag) is empty"
            )
        dockerfile = (worktree / cfg.dockerfile).resolve()
        context = (worktree / cfg.build_context).resolve()
        self._run(
            cfg,
            ["build", "-t", cfg.image, "-f", str(dockerfile), str(context)],
            action=f"build image {cfg.image}",
        )

    def up(self, cfg: ContainerConfig, *, name: str, worktree: Path) -> None:
        """Ensure a container ``name`` is running, idempotently.

        Running → no-op. Existing-but-stopped → ``docker start``. Absent →
        ``docker run -d`` with the worktree bind-mounted at ``workspace_mount``
        plus any extra ``mounts`` / ``run_args``, kept alive by ``sleep infinity``
        so the agent can be ``exec``'d into it. The image itself is never
        Grove-built here — ``build`` (or a manual/registry pull) owns that.
        """
        if not cfg.image:
            raise ContainerError("container.enabled is set but container.image is empty")
        status = self.inspect(cfg, name=name)
        if status.running:
            return
        if status.exists:
            self._run(cfg, ["start", name], action=f"start container {name}")
            return
        argv = [
            "run",
            "-d",
            "--name",
            name,
            "-w",
            cfg.workspace_mount,
            "-v",
            f"{worktree}:{cfg.workspace_mount}",
        ]
        for spec in cfg.mounts:
            argv += ["-v", spec]
        if cfg.network:
            argv += ["--network", cfg.network]
        argv += list(cfg.run_args)
        # Keep the container alive with no foreground process of its own; the
        # agent runs as a separate `docker exec`, so the entrypoint just idles.
        argv += [cfg.image, "sleep", "infinity"]
        self._run(cfg, argv, action=f"run container {name}")

    def exec(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        argv: Sequence[str],
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
    ) -> int:
        """Run ``argv`` inside container ``name`` and return its exit code.

        Blocking, non-tty — for one-shot setup/health commands. The interactive
        agent is NOT launched this way: it rides a tmux-hosted ``exec_argv``
        (``DockerExecLaunchBackend``) so the pane stays attachable. Raises
        :class:`ContainerError` only if the ``docker`` invocation itself fails;
        the inner command's non-zero exit is returned for the caller to judge.
        """
        full = self.exec_argv(cfg, name=name, env=env, workdir=workdir) + list(argv)
        try:
            result = subprocess.run(full, check=False, shell=False)
        except (subprocess.SubprocessError, OSError) as exc:
            raise ContainerError(f"docker exec in {name} failed: {exc}") from exc
        return result.returncode

    def exec_argv(
        self,
        cfg: ContainerConfig,
        *,
        name: str,
        env: Mapping[str, str] = _NO_ENV,
        workdir: str = "",
        tty: bool = False,
    ) -> list[str]:
        """Assemble the ``docker exec`` prefix argv (up to and incl. the name).

        The single seam that maps env → ``-e``, workdir → ``-w``, exec-user →
        ``-u``, tty → ``-t`` onto flags, so the side-effect ``exec`` and the
        tmux-hosted launch stay byte-consistent. ``-i`` is always set (stdin
        stays open); ``-t`` only when the caller runs inside a real tty (a tmux
        pane), never for a headless one-shot ``exec``.
        """
        argv = [cfg.docker_bin, "exec", "-i"]
        if tty:
            argv.append("-t")
        if cfg.exec_user:
            argv += ["-u", cfg.exec_user]
        if workdir:
            argv += ["-w", workdir]
        for key, value in env.items():
            argv += ["-e", f"{key}={value}"]
        argv.append(name)
        return argv

    def stop(self, cfg: ContainerConfig, *, name: str) -> None:
        """Stop container ``name`` if it is running; no-op otherwise."""
        if not self.inspect(cfg, name=name).running:
            return
        self._run(cfg, ["stop", name], action=f"stop container {name}")

    def down(self, cfg: ContainerConfig, *, name: str) -> None:
        """Remove container ``name`` (force, so a running one is torn down too);
        no-op if it doesn't exist. The bind-mounted worktree is untouched —
        removal drops only the container, exactly as ``kill`` never touches the
        user's branch."""
        if not self.inspect(cfg, name=name).exists:
            return
        self._run(cfg, ["rm", "-f", name], action=f"remove container {name}")

    def inspect(self, cfg: ContainerConfig, *, name: str) -> ContainerStatus:
        """Best-effort run-state read for container ``name`` — never raises.

        A missing container, a missing/erroring ``docker``, or unparseable
        output all collapse to ``ContainerStatus(exists=False)`` so ``up`` treats
        an unreadable engine the same as absence (it will try to run, and a real
        engine failure surfaces loudly there).
        """
        try:
            result = subprocess.run(
                [cfg.docker_bin, "inspect", "-f", "{{.State.Running}} {{.State.Status}}", name],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("container inspect({}) failed: {}", name, exc)
            return ContainerStatus(exists=False, running=False)
        if result.returncode != 0:
            return ContainerStatus(exists=False, running=False)
        parts = result.stdout.split()
        running = bool(parts) and parts[0] == "true"
        status = parts[1] if len(parts) > 1 else ""
        return ContainerStatus(exists=True, running=running, status=status)

    def _run(self, cfg: ContainerConfig, args: Sequence[str], *, action: str) -> None:
        """Run one ``docker`` lifecycle subprocess; raise ``ContainerError`` on failure.

        The single mutating-invocation seam — argv shape and error narrowing live
        here so build/up/start/stop/down can't drift, the ``git.py`` ``_run``
        pattern applied to the container CLI.
        """
        argv = [cfg.docker_bin, *args]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ContainerError(f"failed to {action}: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ContainerError(f"failed to {action}: {detail}")
