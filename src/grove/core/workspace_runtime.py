"""WorkspaceRuntime — what a workspace's runtime answers AFTER its agent launched.

:class:`~grove.core.launch.LaunchBackend` starts the agent. Everything after
that (is the runtime still there, how do I tear it down, how does a person get
in, how does a command reach the worktree the agent sees) used to be a
``state.runtime is Runtime.CONTAINER`` branch at each manager call site. This
module holds those answers, one class per runtime, and
:meth:`WorkspaceRuntimes.for_state` is the ONE place a :class:`Runtime` value
selects an implementation. A third runtime is a new class plus one line there,
never a manager change.

``freeze`` / ``thaw`` are the idle-suspend hooks #970 fills in; until then they
refuse. They are deliberately NOT :meth:`WorkspaceRuntime.stop`, which is the
``pause`` verb's container stop and already has a caller.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from loguru import logger

from grove.core import tmux
from grove.core.config import EpisodicRuntimeConfig, GroveConfig, RemoteRuntimeConfig
from grove.core.container_agent import ContainerAgentEntry
from grove.core.container_runtime import (
    ContainerLifecycle,
    ContainerLiveness,
    ContainerRuntimeState,
    ContainerState,
)
from grove.core.container_tmux import ContainerTmux
from grove.core.devcontainer import DevcontainerCli
from grove.core.errors import CapabilityUnavailable, ConfigError, ContainerError, GroveError
from grove.core.git import GitRepo
from grove.core.native_owners import WorktreeRead
from grove.core.runtimes import (
    HibernatingDriver,
    RemoteHandle,
    RuntimeState,
    ServerlessDriver,
    WorkspaceSpec,
)
from grove.core.runtimes.factory import DriverFactory, build_driver
from grove.core.tmux import AttachInstruction, ContainerAttach
from grove.core.workspace import Runtime, WorkspaceState

#: Reads one container's live substate by id; ``None`` means unreadable.
type ContainerLivenessReader = Callable[[str], ContainerState | None]

#: Turns an argv meant for the agent's worktree into one that runs where the
#: agent actually sees that worktree.
type WorktreeReader = Callable[[Sequence[str]], list[str]]

#: Runs an argv in a worktree that is NOT on this host and returns what it
#: answered, within the given number of seconds.
type WorktreeRunner = Callable[[WorkspaceState, Sequence[str], float], WorktreeRead]

#: Reads the last N lines of a remote agent's transcript, within the seconds given.
type TranscriptTailer = Callable[[WorkspaceState, int, float], WorktreeRead]


@dataclass(frozen=True, slots=True)
class Residue:
    """What a runtime teardown left behind.

    Attributes:
        failure: Why the teardown did not complete, or ``None`` when nothing
            the runtime owns is left. A non-``None`` value is the one kill
            failure that keeps the workspace record, because the record is the
            only thing that can still name what survived.
    """

    failure: str | None = None


class WorkspaceRuntime(Protocol):
    """The post-launch questions only a workspace's runtime can answer."""

    def liveness(self, state: WorkspaceState) -> bool | None:
        """Is the runtime hosting the agent still up?

        Returns:
            ``True`` up, ``False`` gone (respawn is the remedy), ``None`` when
            this runtime cannot tell or has nothing of its own to check. Never
            a confident answer for "cannot tell".
        """
        ...

    def teardown(self, state: WorkspaceState) -> Residue:
        """Destroy what this runtime created for the workspace. Never raises."""
        ...

    def stop(self, state: WorkspaceState) -> None:
        """Stop the runtime for a ``pause``, keeping its identity for resume.

        Raises:
            ContainerError: The stop command failed; the caller logs it.
        """
        ...

    def attach_instruction(self, state: WorkspaceState) -> AttachInstruction:
        """Where a client attaches to watch or drive this workspace's agent."""
        ...

    def worktree_reader(self, state: WorkspaceState) -> WorktreeReader | None:
        """How a command reaches the worktree as the agent sees it.

        Returns:
            The argv translator, or ``None`` when the runtime cannot currently
            be addressed (a container record with no identity yet).
        """
        ...

    def freeze(self, state: WorkspaceState) -> None:
        """Suspend an idle workspace's runtime in place (#970).

        Freezes the PROCESSES, never the worktree, so a dirty tree is no reason
        to refuse. A runtime with nothing worth suspending does nothing.

        Raises:
            ContainerError: The suspend command failed; the workspace stays as it was.
        """
        ...

    def thaw(self, state: WorkspaceState) -> RemoteHandle | None:
        """Undo :meth:`freeze`; must be safe to call on a runtime that is not frozen.

        Returns:
            The workload handle to persist from now on when the thaw replaced
            the workload, else ``None``.

        Raises:
            ContainerError: The resume command failed.
        """
        ...


class ContainerRuntime:
    """A workspace whose agent runs inside a devcontainer."""

    def __init__(self, cfg: GroveConfig, *, cli: Callable[[], DevcontainerCli]) -> None:
        """Bind the container answers to one config cascade.

        Args:
            cfg: The repo's resolved config; its ``container`` section names the
                docker binary and the agent's in-container session.
            cli: Supplies the devcontainer CLI on first use, so a host-only
                install never builds one and tests inject a fake.
        """
        self._cfg = cfg
        self._cli = cli
        self._bound_liveness: ContainerLivenessReader | None = None
        self._liveness: ContainerLiveness | None = None

    def bind_liveness(self, reader: ContainerLivenessReader) -> None:
        """Answer liveness from the daemon's maintained facts instead of probing."""
        self._bound_liveness = reader

    def state(self, state: WorkspaceState) -> ContainerState | None:
        """The container's live substate, memoized; ``None`` if unreadable or absent.

        Memoized at the reader because reconcile runs per workspace per poll
        (see :class:`ContainerLiveness` for the cost).
        """
        if state.container is None:
            return None
        if self._bound_liveness is not None:
            return self._bound_liveness(state.container.container_id)
        if self._liveness is None:
            self._liveness = ContainerLiveness(docker_bin=self._cfg.container.docker_bin)
        return self._liveness.state_of(state.container)

    def liveness(self, state: WorkspaceState) -> bool | None:
        """Every substate but RUNNING is a container the agent cannot be alive in.

        UNPROVISIONED counts as gone: it is running but its lifecycle hooks (the
        egress firewall) did not report for this start, and respawn is the
        remedy for it as for ABSENT, STOPPED and MISSING_IMAGE.
        """
        found = self.state(state)
        return None if found is None else found is ContainerState.RUNNING

    def lifecycle(self, container: ContainerRuntimeState) -> ContainerLifecycle:
        """The pause/teardown verbs for one container identity.

        The agent's session name is cascaded config, so the graceful shutdown
        signals exactly the session the launch created.
        """
        return ContainerLifecycle(
            container,
            docker_bin=self._cfg.container.docker_bin,
            agent_session=self._cfg.container.tmux.session,
        )

    def teardown(self, state: WorkspaceState) -> Residue:
        if state.container is None:
            return Residue()
        try:
            self.lifecycle(state.container).teardown()
        except Exception as exc:
            return Residue(failure=str(exc))
        return Residue()

    def stop(self, state: WorkspaceState) -> None:
        if state.container is not None:
            self.lifecycle(state.container).pause()

    def entry(
        self, state: WorkspaceState, *, session: str, read_only: bool = False
    ) -> ContainerAgentEntry | None:
        """The way into one in-container agent session, or ``None`` with no tmux there.

        Args:
            state: The workspace whose container is entered.
            session: The in-container tmux session, the agent's reattach identity.
            read_only: Enter as a viewer (a native workspace's pane).

        Returns:
            ``None`` for a container whose image has no reachable tmux, so the
            caller degrades to the host pane.
        """
        if state.container is None:
            return None
        if ContainerTmux.for_container(state.container, cfg=self._cfg.container) is None:
            return None
        return ContainerAgentEntry(
            container=state.container,
            cfg=self._cfg,
            worktree=Path(state.worktree_path),
            cwd=state.agent_cwd,
            session=session,
            cli=self._cli(),
            read_only=read_only,
        )

    def attach_instruction(self, state: WorkspaceState) -> AttachInstruction:
        """Exec straight into the container's own tmux; the host pane if it has none.

        No host session is targeted when the container has tmux: a host pane
        running ``tmux attach`` inside would be a shadow client clamping every
        later client's size.
        """
        entry = self.entry(state, session=self._cfg.container.tmux.session, read_only=state.native)
        if entry is None:
            return HostRuntime.host_attach(state)
        return ContainerAttach(argv=tuple(entry.argv(detached=False)), read_only=state.native)

    def worktree_reader(self, state: WorkspaceState) -> WorktreeReader | None:
        container = state.container
        if container is None:
            return None
        return container.exec_argv

    def freeze(self, state: WorkspaceState) -> None:
        # `docker pause`, never stop: an idle workspace keeps its memory and
        # thaws instantly, and a stop would need Grove's own start path to put
        # the egress firewall back. Stop stays the human's `pause` verb.
        if state.container is not None:
            self.lifecycle(state.container).freeze("pause")

    def thaw(self, state: WorkspaceState) -> RemoteHandle | None:
        if state.container is not None:
            self.lifecycle(state.container).freeze("unpause")
        return None


class HostRuntime:
    """A workspace whose agent runs on this host, in its own tmux server."""

    def __init__(self, containers: ContainerRuntime) -> None:
        """Hold the container verbs for the one host record that can carry a container.

        A failed host-to-container promotion on respawn grafts the identity it
        left onto a record whose runtime is still HOST, so ``kill`` and
        ``pause`` must still reach that container or it is stranded.
        """
        self._containers = containers

    def liveness(self, state: WorkspaceState) -> bool | None:  # noqa: ARG002
        # The host tmux session is checked by the manager for every runtime.
        return None

    def teardown(self, state: WorkspaceState) -> Residue:
        return self._containers.teardown(state)

    def stop(self, state: WorkspaceState) -> None:
        self._containers.stop(state)

    @staticmethod
    def host_attach(state: WorkspaceState) -> AttachInstruction:
        """Attach to the workspace's session on this host's tmux server."""
        return tmux.attach_instruction(
            state.tmux_session,
            read_only=state.native,
            server=tmux.TmuxServer(socket=state.host_tmux_socket),
        )

    def attach_instruction(self, state: WorkspaceState) -> AttachInstruction:
        return self.host_attach(state)

    def worktree_reader(self, state: WorkspaceState) -> WorktreeReader | None:  # noqa: ARG002
        return list

    def freeze(self, state: WorkspaceState) -> None:
        # A host agent at rest costs a tmux server and an idle child; there is
        # nothing to suspend that would pay for the thaw.
        del state

    def thaw(self, state: WorkspaceState) -> RemoteHandle | None:
        del state
        return None


class RemoteRuntime:
    """A workspace whose agent runs on a serverless platform and dials in.

    Liveness and teardown delegate to the platform's driver, picked by the
    ``runtimes.<name>`` the record's handle names. Every verb that ends or
    suspends the workload pushes the workspace branch through the worker first
    (F5), and a push that fails leaves the workload running: the branch is the
    only state that outlives it.
    """

    def __init__(
        self,
        runtimes: Mapping[str, RemoteRuntimeConfig],
        *,
        factory: DriverFactory = build_driver,
        runner: WorktreeRunner | None = None,
        reachable: Callable[[WorkspaceState], bool] | None = None,
        tailer: TranscriptTailer | None = None,
        boot_seconds: float | None = None,
    ) -> None:
        """Bind to the configured runtimes; drivers are built on first use.

        Args:
            runtimes: The resolved ``runtimes`` map, by name.
            factory: Builds a driver for one block; tests inject fakes here.
            runner: Runs a command in the worker's own worktree over its
                channel, or ``None`` where no daemon channel exists (a CLI
                process), in which case worktree commands refuse.
            reachable: Whether the workspace's worker is on the channel right
                now, or ``None`` where that cannot be asked (liveness then
                rests on the platform alone).
            tailer: Reads the worker's own transcript tail, or ``None`` where
                no daemon channel exists.
            boot_seconds: How long after a create, thaw or wake a worker may
                take to dial in before it reads as unreachable; ``None`` gives
                no grace.
        """
        self._runtimes = runtimes
        self._factory = factory
        self._runner = runner
        self._reachable = reachable
        self._tailer = tailer
        self._boot_seconds = boot_seconds
        self._drivers: dict[str, ServerlessDriver] = {}

    def driver(self, handle: RemoteHandle) -> ServerlessDriver:
        """The driver for the runtime this handle was started on.

        Raises:
            ConfigError: No ``runtimes.<name>`` block names it any more, so the
                workload cannot be reached until one does.
        """
        found = self._drivers.get(handle.runtime)
        if found is not None:
            return found
        cfg = self._runtimes.get(handle.runtime)
        if cfg is None:
            raise ConfigError(
                f"no runtimes.{handle.runtime} is configured, so workload {handle.ref} "
                "cannot be reached"
            )
        if isinstance(cfg, EpisodicRuntimeConfig):
            raise ConfigError(
                f"runtimes.{handle.runtime} runs one turn per request and holds no "
                f"workload, so {handle.ref} cannot be one of its"
            )
        built = self._drivers[handle.runtime] = self._factory(handle.runtime, cfg)
        return built

    def liveness(self, state: WorkspaceState) -> bool | None:
        """Up while starting or running; ``None`` when the platform cannot be read."""
        if state.remote is None:
            return None
        try:
            found = self.driver(state.remote).status(state.remote)
        except ConfigError:
            return None
        if found is None:
            return None
        # The platform runs the workload but its worker is not on the channel:
        # every read would fail, so the honest answer is gone (OFFLINE), never
        # a dashboard of confident zeros. STARTING is exempt, since a booting
        # worker has not dialled in yet, and so is a workload Grove started or
        # woke within the boot bound: the platform says RUNNING long before
        # its worker dials in, and OFFLINE there sent a just-woken workspace
        # to respawn, discarding the session the wake restored (#1076).
        unreachable = (
            found is RuntimeState.RUNNING
            and self._reachable is not None
            and not self._booting(state)
            and not self._reachable(state)
        )
        if unreachable:
            logger.info("remote workspace {} runs but its worker is not connected", state.id)
            return False
        return found in (RuntimeState.STARTING, RuntimeState.RUNNING)

    def _booting(self, state: WorkspaceState) -> bool:
        """Whether Grove started or woke ``state``'s workload within the boot bound.

        Args:
            state: A remote workspace.

        Returns:
            ``True`` while less than ``boot_seconds`` has passed since the record
            was created or last thawed or woken; ``False`` after that, or when no
            bound is configured.
        """
        if self._boot_seconds is None:
            return False
        started = max(state.created_at, state.thawed_at or state.created_at)
        return datetime.now(UTC) - started < timedelta(seconds=self._boot_seconds)

    def teardown(self, state: WorkspaceState) -> Residue:
        """Remove the workload and every snapshot the record still names.

        A wake can have started a replacement workload while its source snapshot
        waits for worker registration. Kill discards that recovery path, so both
        identities must be removed before the record can safely disappear.
        """
        failures: list[str] = []
        if state.remote is not None:
            try:
                if state.hibernated_at is not None:
                    self.hibernating(state.remote).forget(state.remote)
                else:
                    self.driver(state.remote).rm(state.remote)
            except Exception as exc:
                failures.append(str(exc))
        pending = state.pending_snapshot
        if pending is not None and pending != state.remote:
            try:
                self.hibernating(pending).forget(pending)
            except Exception as exc:
                failures.append(str(exc))
        return Residue(failure="; ".join(failures) or None)

    def launch(self, runtime: str, spec: WorkspaceSpec) -> RemoteHandle:
        """Start a workload for ``spec`` on the named ``runtimes.<name>`` block.

        The spec is rendered first, so a platform limit it breaks refuses before
        anything is started.

        Raises:
            ProvisionRefused: The platform, or its rendered spec, refused it.
            ContainerError: The workload could not be started.
        """
        driver = self.driver(RemoteHandle(runtime=runtime, ref=""))
        driver.emit_spec(spec)
        return replace(driver.up(spec), runtime=runtime)

    def push(self, state: WorkspaceState) -> None:
        """Push the workspace branch from the worker's worktree (F5).

        Commits only: work the agent never committed is not on the branch.

        Raises:
            ContainerError: The worker is unreachable or the push failed; the
                caller must not end the workload.
        """
        try:
            answered = self.run_in_worktree(
                state,
                ["git", "push", "-q", "origin", f"HEAD:refs/heads/{state.branch}"],
                timeout=GitRepo.TIMEOUT_SECONDS,
            )
        except GroveError as exc:
            raise ContainerError(f"workspace {state.id}'s branch was not pushed: {exc}") from exc
        if answered.returncode != 0:
            raise ContainerError(
                f"workspace {state.id}'s branch was not pushed: {answered.output.strip()}"
            )

    def stop(self, state: WorkspaceState) -> None:
        """Push the branch, then stop the workload."""
        if state.remote is not None:
            self.push(state)
            self.driver(state.remote).stop(state.remote)

    def attach_instruction(self, state: WorkspaceState) -> AttachInstruction:
        """Refuse: there is no pane on this host to attach to."""
        raise _needs_worker(state, "attach")

    def worktree_reader(self, state: WorkspaceState) -> WorktreeReader | None:
        """Refuse: no local argv reaches the worktree; use :meth:`run_in_worktree`."""
        raise _needs_worker(state, "read the worktree")

    def transcript_tail(self, state: WorkspaceState, lines: int, *, timeout: float) -> WorktreeRead:
        """The last ``lines`` of the remote agent's transcript, read by its worker.

        Raises:
            CapabilityUnavailable: This process has no channel to the worker.
            PaneNotFound: The worker is not connected, or did not answer in time.
        """
        if self._tailer is None:
            raise _needs_worker(state, "read its transcript")
        return self._tailer(state, lines, timeout)

    def run_in_worktree(
        self, state: WorkspaceState, argv: Sequence[str], *, timeout: float
    ) -> WorktreeRead:
        """Run ``argv`` in the worker's own worktree and return what it answered.

        Raises:
            CapabilityUnavailable: This process has no channel to the worker.
            PaneNotFound: The worker is not connected, or did not answer in time.
        """
        if self._runner is None:
            raise _needs_worker(state, "read the worktree")
        return self._runner(state, argv, timeout)

    def freeze(self, state: WorkspaceState) -> None:
        """Push the branch, then suspend (or end) the workload."""
        if state.remote is not None:
            self.push(state)
            self.driver(state.remote).freeze(state.remote)

    def hibernating(self, handle: RemoteHandle) -> HibernatingDriver:
        """The handle's driver as a :class:`HibernatingDriver`.

        Raises:
            CapabilityUnavailable: The platform cannot hibernate, so the
                workspace stays frozen.
        """
        driver = self.driver(handle)
        if not isinstance(driver, HibernatingDriver):
            raise CapabilityUnavailable(f"runtimes.{handle.runtime} cannot hibernate a workload")
        return driver

    def hibernate(self, state: WorkspaceState) -> RemoteHandle:
        """Release a frozen workload to a stored snapshot; the branch was pushed at freeze.

        Raises:
            CapabilityUnavailable: The platform cannot hibernate.
            ContainerError: A step failed; the workload is still frozen.
        """
        if state.remote is None:
            raise CapabilityUnavailable(f"workspace {state.id} has no workload to hibernate")
        return self.hibernating(state.remote).hibernate(state.remote)

    def wake(self, state: WorkspaceState, spec: WorkspaceSpec) -> RemoteHandle:
        """Start a new workload from a hibernated workspace's snapshot."""
        if state.remote is None:
            raise CapabilityUnavailable(f"workspace {state.id} has no snapshot to wake")
        return replace(
            self.hibernating(state.remote).wake(state.remote, spec), runtime=state.remote.runtime
        )

    def thaw(self, state: WorkspaceState) -> RemoteHandle | None:
        """Bring the workload back; a platform with no suspend starts a new one."""
        if state.remote is None:
            return None
        return self.driver(state.remote).thaw(state.remote)


def _needs_worker(state: WorkspaceState, verb: str) -> CapabilityUnavailable:
    # A remote worktree has no local pane or path; only the worker's channel
    # reaches it, and a process with no daemon has none.
    return CapabilityUnavailable(f"workspace {state.id} runs remotely and cannot {verb} here")


class WorkspaceRuntimes:
    """The one place a workspace's :class:`Runtime` selects its implementation."""

    def __init__(
        self,
        cfg: GroveConfig,
        *,
        cli: Callable[[], DevcontainerCli],
        remote_runner: WorktreeRunner | None = None,
        remote_reachable: Callable[[WorkspaceState], bool] | None = None,
        remote_tailer: TranscriptTailer | None = None,
    ) -> None:
        """Build every runtime over one config cascade.

        Args:
            cfg: The repo's resolved config.
            cli: Supplies the devcontainer CLI lazily.
            remote_runner: The daemon's road into a remote worker's worktree.
            remote_reachable: Whether a remote worker is connected right now.
            remote_tailer: The daemon's road to a remote worker's transcript.
        """
        self.container = ContainerRuntime(cfg, cli=cli)
        self.host = HostRuntime(self.container)
        self.remote = RemoteRuntime(
            cfg.runtimes,
            runner=remote_runner,
            reachable=remote_reachable,
            tailer=remote_tailer,
            # The longest boot any agent here is granted, so no workspace's own
            # agent is ever read OFFLINE inside its launch bound.
            boot_seconds=max((a.native_timeout_seconds for a in cfg.agents), default=None),
        )

    def for_state(self, state: WorkspaceState) -> WorkspaceRuntime:
        """The runtime this workspace's PERSISTED runtime names, never config's."""
        if state.runtime is Runtime.CONTAINER:
            return self.container
        if state.runtime is Runtime.REMOTE:
            return self.remote
        return self.host


__all__ = [
    "ContainerLivenessReader",
    "ContainerRuntime",
    "HostRuntime",
    "RemoteRuntime",
    "Residue",
    "TranscriptTailer",
    "WorkspaceRuntime",
    "WorkspaceRuntimes",
    "WorktreeReader",
    "WorktreeRunner",
]
