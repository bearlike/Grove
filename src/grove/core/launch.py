"""LaunchBackend — the swappable seam that starts an assembled agent command.

The manager composes an agent's launch (its command, the adapter's decoration,
the hermetic env) into a :class:`LaunchSpec` — structured data, never pre-typed
keystrokes — and hands it to a :class:`LaunchBackend`. Today the only production
backend is :class:`TmuxLaunchBackend`, which delegates to the existing
``grove.core.tmux`` side-effect module so behavior is byte-identical to the
pre-seam inline calls in ``create`` / ``resume`` / ``respawn``.

The point of the seam (#145): a container/headless runtime can implement the
same one-method Protocol and replace tmux WITHOUT touching ``AgentSpec``, the
adapters, or the decoration composition — the backend receives only a
``LaunchSpec``, so nothing upstream of the launch boundary knows or cares how
the command is actually run.

Side-effect discipline: this module holds no I/O of its own. ``TmuxLaunchBackend``
composes the ``tmux`` functions; a future backend routes to its own dedicated
side-effect module. Dependencies flow inward — ``tmux`` never imports this.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar, Protocol

from grove.core import process, tmux
from grove.core.config import GroveConfig
from grove.core.container import ContainerDriver, DockerContainerDriver


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """Everything a backend needs to start one assembled agent command.

    Structured data, not keystrokes: a backend decides HOW to run ``command``
    (with ``decoration`` appended) in ``cwd``, having applied ``env_unset`` then
    ``env`` — a detached tmux pane today, a container ``exec`` tomorrow. The
    fields are the exact inputs the pre-seam call sites threaded into
    ``tmux.create_session`` + ``tmux.build_workspace_layout``:

    * ``session_name`` — the workspace's tmux session name / backend handle.
    * ``cwd`` — where the session is rooted (``WorkspaceState.agent_cwd``).
    * ``command`` — the agent binary/invocation (``AgentSpec.command``).
    * ``decoration`` — the composed launch argv (``--session-id``/``--resume``,
      ``--model``, hook ``--settings``, trailing prompt positional), appended to
      ``command`` and shell-quoted at the boundary. Never composed here.
    * ``env`` / ``env_unset`` — the hermetic launch env (#82): clear ``env_unset``
      first, then export ``env`` (a key in both ends up exported).
    * ``cfg`` / ``worktree`` — the layout inputs ``build_workspace_layout`` reads
      (shell/agent window names, history limit; the window root). A headless
      backend that builds no windows simply ignores them.
    """

    session_name: str
    cwd: Path
    command: str
    decoration: tuple[str, ...]
    env: Mapping[str, str]
    env_unset: tuple[str, ...]
    cfg: GroveConfig
    worktree: Path


class LaunchBackend(Protocol):
    """Starts an assembled agent command in a workspace — the swap point (#145).

    One method, ``launch(spec)``. The default :class:`TmuxLaunchBackend` runs the
    command in a detached tmux session; a container/headless runtime implements
    the same method over its own runtime. It raises on failure (the manager wraps
    the exception into its transactional rollback), returns ``None`` on success.

    ``provides_pane`` (#146) is the runtime-capability sentinel the manager reads
    to gate every tmux-only path: True when the backend hosts a live tmux pane
    (status reconciles from pane activity; send/interrupt/snapshot type into it),
    False for a paneless runtime (status derives from the transcript/adapter
    blend like a remote adapter, and pane-bound ops raise ``CapabilityUnavailable``).
    """

    provides_pane: bool

    def launch(self, spec: LaunchSpec) -> None: ...


class TmuxLaunchBackend:
    """The default backend: a detached tmux session + shell/agent windows.

    Delegates to the ``grove.core.tmux`` side-effect module so behavior is
    byte-identical to the inline ``create_session`` + ``build_workspace_layout``
    calls the manager made before the seam existed.
    """

    provides_pane: ClassVar[bool] = True

    def launch(self, spec: LaunchSpec) -> None:
        tmux.create_session(
            spec.session_name,
            cwd=spec.cwd,
            history_limit=spec.cfg.tmux.history_limit,
        )
        tmux.build_workspace_layout(
            spec.session_name,
            cfg=spec.cfg,
            worktree=spec.worktree,
            command=spec.command,
            decoration=spec.decoration,
            env=spec.env,
            env_unset=spec.env_unset,
        )


class HeadlessLaunchBackend:
    """A paneless backend: the agent runs as a detached OS process, no tmux (#146).

    Spawns the assembled ``command`` + ``decoration`` via
    ``grove.core.process.spawn_detached`` in ``spec.cwd`` with the hermetic env
    applied, then returns — there is deliberately no session, window, or pane.
    ``provides_pane = False`` is the capability sentinel the manager reads to gate
    every tmux-only path: status reconciles from the transcript/adapter blend
    (the remote-adapter precedent — the pane is not authoritative), and pane-bound
    ops (``send_message`` / ``interrupt`` / a pane snapshot) raise
    ``CapabilityUnavailable`` rather than reach for a pane that isn't there.

    Minimal by design: spawn-and-detach only. Liveness probing, stdio wiring, and
    a real interrupt arrive with the native input channel (#182/#172); this
    backend is the runtime they attach to. ``spec.session_name`` /
    ``spec.cfg`` / ``spec.worktree`` — the tmux-layout inputs — are unused here.
    """

    provides_pane: ClassVar[bool] = False

    def launch(self, spec: LaunchSpec) -> None:
        process.spawn_detached(
            spec.command,
            decoration=spec.decoration,
            cwd=spec.cwd,
            env=spec.env,
            env_unset=spec.env_unset,
        )


class DockerExecLaunchBackend:
    """Run the assembled agent command INSIDE a container, hosted in a tmux pane (#65).

    The container variant of the seam: the worktree is bind-mounted into a
    Docker container (built/started via an injected :class:`ContainerDriver`),
    and the agent runs as ``docker exec -it <ctr> <command> <decoration>`` — but
    that ``docker exec`` is itself hosted in the SAME detached tmux session +
    shell/agent layout the tmux backend builds, so peek / attach / steering all
    keep working unchanged; only the process the agent-pane runs moved into the
    container. ``provides_pane`` stays True for exactly that reason (the #146
    classvar convention: headless backends set it False).

    Env crosses the boundary the container way, not the host way: the hermetic
    ``spec.env`` is folded into ``docker exec -e KEY=VAL`` so it reaches the
    in-container process, and the host pane exports NOTHING (``env={}``) — a
    fresh container needs no ``env_unset`` host-leak scrub (nothing crosses in
    unless named). The agent's container workdir is ``workspace_mount`` plus the
    worktree-relative subpath, so a nested-project cwd is honored.
    """

    provides_pane: ClassVar[bool] = True

    def __init__(self, driver: ContainerDriver | None = None) -> None:
        self._driver = driver or DockerContainerDriver()

    def launch(self, spec: LaunchSpec) -> None:
        cfg = spec.cfg.container
        name = spec.session_name
        self._driver.build(cfg, worktree=spec.worktree)
        self._driver.up(cfg, name=name, worktree=spec.worktree)
        workdir = self._container_workdir(cfg.workspace_mount, worktree=spec.worktree, cwd=spec.cwd)
        prefix = self._driver.exec_argv(cfg, name=name, env=spec.env, workdir=workdir, tty=True)
        # The pane runs `docker exec … <ctr> <command>`; the adapter decoration
        # is appended by build_workspace_layout exactly as for a host launch, so
        # `--session-id`/`--resume`/`--model`/the prompt positional flow through
        # unchanged. Host env is empty — env crossed via `-e` above.
        command = " ".join([*(shlex.quote(token) for token in prefix), spec.command])
        tmux.create_session(name, cwd=spec.worktree, history_limit=spec.cfg.tmux.history_limit)
        tmux.build_workspace_layout(
            name,
            cfg=spec.cfg,
            worktree=spec.worktree,
            command=command,
            decoration=spec.decoration,
            env={},
            env_unset=(),
        )

    @staticmethod
    def _container_workdir(workspace_mount: str, *, worktree: Path, cwd: Path) -> str:
        """Map the host agent cwd to its path inside the bind-mounted container.

        The worktree is mounted at ``workspace_mount``, so ``cwd`` (=
        ``worktree/project_subpath``) becomes ``workspace_mount / subpath``. A cwd
        that isn't under the worktree (shouldn't happen) degrades to the mount
        root rather than leaking a host-absolute path into the container.
        """
        try:
            rel = cwd.resolve().relative_to(worktree.resolve())
        except ValueError:
            return workspace_mount
        return str(PurePosixPath(workspace_mount) / PurePosixPath(rel.as_posix()))
