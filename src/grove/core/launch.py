"""LaunchBackend — the swappable seam that starts an assembled agent command.

The manager composes an agent's launch (its command, the adapter's decoration,
the hermetic env) into a :class:`LaunchSpec` — structured data, never pre-typed
keystrokes — and hands it to a :class:`LaunchBackend`. Three ship:
:class:`TmuxLaunchBackend` (the host default, delegating to the existing
``grove.core.tmux`` side-effect module so behavior is byte-identical to the
pre-seam inline calls in ``create`` / ``resume`` / ``respawn``),
:class:`HeadlessLaunchBackend` (paneless), and
:class:`DevcontainerLaunchBackend` (the one and only container runtime).

The point of the seam: a container/headless runtime can implement the
same small Protocol and replace tmux WITHOUT touching ``AgentSpec``, the
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
from pathlib import Path
from typing import ClassVar, Protocol

from loguru import logger

from grove.core import paths, process, tmux
from grove.core.config import AgentKind, GroveConfig
from grove.core.container_agent import ContainerAgentEntry
from grove.core.container_policy import AgentSharePlan
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_shell import ContainerShell
from grove.core.container_tmux import ContainerTmux, TmuxEntry
from grove.core.devcontainer import DevcontainerCli
from grove.core.errors import ContainerError
from grove.core.workspace import TranscriptContext


@dataclass(frozen=True, slots=True)
class AgentExit:
    """The exit status of the agent command Grove started in a pane.

    **Why this exists at all: the agent process is not the pane.** Grove types
    the agent command into an interactive shell, so when the agent dies the
    shell survives and the pane stays alive at a prompt. Nothing about the
    workspace changes — the tmux session is up, the worktree is intact, the
    transcript was never written — so the activity blend reads "no transcript
    yet" as STARTING and settles to IDLE. A dead agent and a quiet one are
    indistinguishable, which lets a workspace sit dead behind `grove create`
    exiting 0.

    **Why it is recorded rather than inferred.** There is nothing to observe at
    launch time: `launch()` returns the moment the keys are typed, and the agent
    dies milliseconds later. So the launch *installs a recorder* — a shell
    suffix that writes `$?` when the command finally exits — and the read side
    reports a recorded fact. The alternative, inferring death from the pane's
    foreground process, yields no exit code and cannot tell a crash from a user
    who quit their agent deliberately.

    **Absence is not failure.** No file means the command has not exited: the
    agent is still running, or still starting. That is what keeps a slow start
    from ever being reported as failed, and it is a property of the shape rather
    than a threshold anyone has to tune.

    **The limit, and why it is acceptable.** Anything that terminates the
    RECORDING SHELL itself leaves no record at all — the `exit` builtin, or a
    signal delivered to that shell rather than to the agent. It does not arise
    today because an agent is always an external command (`claude`, `codex`, a
    wrapper), which the shell survives; and even where it did arise, absence
    already means "no information" rather than "healthy", so the mechanism fails
    in the safe direction. Both halves matter: the first is why nothing is
    broken, the second is why nothing WOULD break if that changed. Verified by
    running a real shell rather than asserting on its text: an `exit 3` test
    case writes nothing, which no text assertion alone would surface.
    """

    code: int

    @staticmethod
    def record_suffix(path: Path) -> str:
        """The shell appended to the agent command so the pane records its exit.

        The producer half of this class, kept beside :meth:`read` so the format
        and its parser cannot drift. Deliberately `;` and not `&&`: the point is
        to record *every* exit, and a failing command is the interesting one.
        """
        return f"; echo $? > {shlex.quote(str(path))}"

    @classmethod
    def read(cls, path: Path) -> AgentExit | None:
        """The recorded exit, or ``None`` when the command has not exited.

        Never raises: this is on the per-tick read path, where an unreadable or
        half-written file must degrade to "no information" rather than break a
        snapshot. A partial read is indistinguishable from no record, which is
        the safe direction — the next tick sees the complete file.
        """
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return None
        try:
            return cls(code=int(raw))
        except ValueError:
            return None

    @staticmethod
    def prepare(path: Path) -> None:
        """Make *path* ready to receive a record. Called at every launch.

        Two things, because both are "the recorder can actually record" and
        splitting them is how one gets forgotten:

        * **Create the directory.** The writer is a shell redirect, and a
          redirect into a missing directory fails — silently as far as Grove is
          concerned, since the message lands in the pane the user is not
          watching: the suffix composes and appends correctly and the pane
          answers ``zsh: no such file or directory``, so the producer looks
          wired and records nothing. A recorder that cannot write is worse than
          no recorder, because absence is the value that means "still running".
        * **Drop any previous record.** Load-bearing for `respawn`, the
          documented remedy for exactly this failure: a stale non-zero exit left
          on disk would make the relaunched agent read as dead the instant it
          started, taking the fix and the way out with one stone.

        Best-effort throughout — failing to prepare must never fail a launch.
        """
        try:
            paths.ensure_dir(path.parent)
        except OSError as exc:
            logger.debug("could not create agent exit directory {}: {}", path.parent, exc)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("could not clear agent exit record {}: {}", path, exc)

    @property
    def failed(self) -> bool:
        """Whether the agent died rather than being closed cleanly.

        A zero exit is a user who quit their agent, which is not an error and
        must not be reported as one.
        """
        return self.code != 0

    @property
    def reason(self) -> str:
        """The human-facing line, so a user can act without capturing a pane."""
        return f"agent exited with status {self.code}"


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
    * ``env`` / ``env_unset`` — the hermetic launch env: clear ``env_unset``
      first, then export ``env`` (a key in both ends up exported).
    * ``cfg`` — the layout inputs ``build_workspace_layout`` reads (shell/agent
      window names, history limit). A headless backend ignores it.
    * ``worktree`` — the worktree ROOT, which is **not** ``cwd`` for a nested
      project (``cwd == worktree / project_subpath``). Keeping the two fields
      separate matters: a container backend derives the agent's in-container
      path from the cwd's offset relative to this root, so if both were set to
      the agent cwd that offset would always be empty and every nested project
      would collapse to the mount root. Host backends root their windows at
      ``cwd``; only a runtime that has to TRANSLATE a path needs the root.
    * ``kind`` — the agent kind being launched. Carried because a backend
      answers ``transcript_context`` itself, and that answer is keyed by kind:
      which env var names the runtime's config root
      (``TranscriptContext.CONFIG_DIR_ENV``), and — for a container backend —
      which config root it has to mount in the first place.
    """

    session_name: str
    cwd: Path
    command: str
    decoration: tuple[str, ...]
    env: Mapping[str, str]
    env_unset: tuple[str, ...]
    cfg: GroveConfig
    worktree: Path
    kind: AgentKind
    container: ContainerRuntimeState | None = None
    """The workspace's provisioned container, for a container runtime.
    ``None`` for every host launch. Carried rather than re-read so the backend
    uses the identity and the ``remoteWorkspaceFolder`` the CLI itself
    reported."""
    share_plan: AgentSharePlan | None = None
    """The agent-config mount table this container launch runs under.
    ``None`` for every host launch. It is the SAME plan whose ``env`` composed
    the config-dir variable in ``env`` above — carried, never re-derived, so the
    directory the agent is pointed at and the mount the backend reads back
    through are one fact. This is the mount table the namespace bridge needs:
    without it a backend can only see the CONTAINER path in ``env``, which is
    precisely the wrong side."""
    exit_record: Path | None = None
    """Where the pane should record the agent command's exit status.
    ``None`` disables recording. A HOST path even for a container launch, and
    correctly so: the pane runs ``devcontainer exec`` *on this machine* and that
    propagates the in-container exit code, so the record lands where the read
    side can see it without any mount. Only a backend that hosts a shell can
    honour this — see :class:`AgentExit`."""


def _exit_suffix(spec: LaunchSpec) -> str:
    """The exit-recording shell for *spec*, or ``""`` when it records nothing.

    Free function rather than a method because both pane-hosting backends need
    the identical answer and neither owns it — the alternative was the same two
    lines in two `launch` implementations, which is how the two would drift.
    """
    return "" if spec.exit_record is None else AgentExit.record_suffix(spec.exit_record)


class LaunchBackend(Protocol):
    """Starts an assembled agent command in a workspace — the swap point.

    The default :class:`TmuxLaunchBackend` runs the command in a detached tmux
    session; a container/headless runtime implements the same members over its
    own runtime. ``launch`` raises on failure (the manager wraps the exception
    into its transactional rollback), returns ``None`` on success.

    ``provides_pane`` is the runtime-capability sentinel the manager reads
    to gate every tmux-only path: True when the backend hosts a live tmux pane
    (status reconciles from pane activity; send/interrupt/snapshot type into it),
    False for a paneless runtime (status derives from the transcript/adapter
    blend like a remote adapter, and pane-bound ops raise ``CapabilityUnavailable``).

    The other two members are the **namespace bridge**: only the backend
    knows how the namespace it launched into maps onto this host's filesystem, so
    only the backend can answer them. They replace a plain ``host_namespace``
    boolean, which could say "my paths are meaningless to you" but never *what
    they mean* — leaving the manager computing a pair it then had to throw away.

    * ``transcript_context`` — the :class:`~grove.core.workspace.TranscriptContext`
      to persist for this launch, or ``None`` for nothing to record. Host backends
      return ``TranscriptContext.for_launch(...)``; a container backend returns
      the host side of its agent-config mount paired with the container cwd.
      ``None`` stays the right answer for a genuinely unreachable transcript: the
      read side *acts* on what is stored, so a wrong context is worse than none.
    * ``control_path`` — a Grove-written control file (``--settings`` /
      ``--channels`` / ``--mcp-config``) as the launched agent sees it, or
      ``None`` when this backend cannot reach it at all. Identity for host
      backends. One method rather than three because one translation covers
      every control file.

      **``None`` is the load-bearing half of the return type.** The
      caller must omit the flag entirely rather than fall back to the host
      path: Claude Code treats a missing ``--settings`` file as FATAL, so an
      untranslated path is not a graceful degradation, it is the agent process
      exiting before it prints anything — and doing so *after* ``create`` has
      already reported success. Losing the hook sidecar costs the status axis;
      emitting an unopenable path costs the whole workspace. A backend that
      cannot express "unreachable" can only pick the second.
    """

    # ClassVar, not a plain attribute: every implementation states its
    # capability on the class, and a protocol member declared as a mutable
    # instance attribute is NOT satisfied by a ClassVar — so a manager that
    # returns "some backend" would fail to type-check against its own
    # backends. Declaring the intent here is what makes the selection seam
    # (`_backend_for`) expressible at all.
    provides_pane: ClassVar[bool]

    def launch(self, spec: LaunchSpec) -> None: ...

    def transcript_context(self, spec: LaunchSpec) -> TranscriptContext | None: ...

    def control_path(
        self, host_path: Path, *, share_plan: AgentSharePlan | None = None
    ) -> str | None: ...


class HostNamespaceBackend:
    """Shared namespace-bridge answers for a backend that runs on THIS host.

    The bridge is the identity map: the launched process sees the same
    filesystem this process does and gets ``spec.env`` applied directly, so a
    path recorded from that env resolves identically for the agent and for
    whoever reads its transcript later, and a control file needs no rewriting.
    Inherited rather than copied so the two host backends cannot drift — the
    only thing they genuinely differ on is ``launch`` itself.
    """

    def transcript_context(self, spec: LaunchSpec) -> TranscriptContext | None:
        return TranscriptContext.for_launch(kind=spec.kind, env=spec.env, agent_cwd=spec.cwd)

    def control_path(
        self,
        host_path: Path,
        *,
        share_plan: AgentSharePlan | None = None,  # noqa: ARG002
    ) -> str | None:
        """Identity, and never ``None`` — same filesystem, so always reachable.

        ``share_plan`` is accepted and ignored: a host launch carries none
        (the manager only builds one for a container workspace), and the
        signature is the protocol's.
        """
        return str(host_path)


class TmuxLaunchBackend(HostNamespaceBackend):
    """The default backend: a detached tmux session + shell/agent windows.

    Delegates to the ``grove.core.tmux`` side-effect module so behavior is
    byte-identical to the inline ``create_session`` + ``build_workspace_layout``
    calls the manager made before the seam existed. Host-namespace, so the
    bridge is the identity map (see :class:`HostNamespaceBackend`).
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
            # The window root is the AGENT cwd, not the worktree root — the two
            # differ for a nested project. The spec's `worktree` and `cwd`
            # fields are split apart precisely for the container backend's
            # path translation; this host backend always roots at `cwd`.
            worktree=spec.cwd,
            command=spec.command,
            decoration=spec.decoration,
            env=spec.env,
            env_unset=spec.env_unset,
            exit_suffix=_exit_suffix(spec),
        )


class HeadlessLaunchBackend(HostNamespaceBackend):
    """A paneless backend: the agent runs as a detached OS process, no tmux.

    Spawns the assembled ``command`` + ``decoration`` via
    ``grove.core.process.spawn_detached`` in ``spec.cwd`` with the hermetic env
    applied, then returns — there is deliberately no session, window, or pane.
    ``provides_pane = False`` is the capability sentinel the manager reads to gate
    every tmux-only path: status reconciles from the transcript/adapter blend
    (the remote-adapter precedent — the pane is not authoritative), and pane-bound
    ops (``send_message`` / ``interrupt`` / a pane snapshot) raise
    ``CapabilityUnavailable`` rather than reach for a pane that isn't there.

    Minimal by design: spawn-and-detach only. Liveness probing, stdio wiring, and
    a real interrupt arrive with the native input channel; this backend is the
    runtime they attach to. ``spec.session_name`` / ``spec.cfg`` /
    ``spec.worktree`` — the tmux-layout inputs — are unused here.

    Paneless, but still a process on THIS machine with ``spec.env`` applied
    directly, so it inherits the identity namespace bridge: no pane does not
    mean no shared filesystem.
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


class DevcontainerLaunchBackend:
    """Run the agent inside the workspace's DEVCONTAINER, hosted in a tmux pane.

    The ONLY container backend, and the reason `runtime` exists as a persisted
    field: the manager picks this over :class:`TmuxLaunchBackend` for a
    workspace whose `WorkspaceState.runtime` is CONTAINER.

    **The normal arm creates no host tmux session at all.** The agent
    and the shell are started DETACHED inside the container (see
    :meth:`_launch_under_container_tmux`); a host session would only hold panes
    running `devcontainer exec … tmux attach` into sessions that already exist
    in there, and such a pane clamps a later human client's terminal size to its
    own. Only the degraded no-tmux-in-the-image arm still lays out a host
    session, which is where the agent genuinely runs.

    `provides_pane` stays True regardless, and it is not a lie: it is the
    manager's "this runtime has a pane to capture and steer", and `_agent_pane`
    resolves that pane INSIDE the container. The one thing it no longer
    implies is that the pane is on this host.

    Keep it the only one. A second container backend means two answers to "what
    is this workspace running in", which is what the deleted `docker run`-based
    predecessor became once `_backend_for` started routing every
    `Runtime.CONTAINER` here: the project's committed devcontainer
    configuration defines the container, not Grove's own image/mount knobs.

    The container is NOT created here. Provisioning is a create-path transaction
    (`grove.core.runtime.ContainerProvisioner`) whose failure must roll the
    workspace back while KEEPING the container for diagnosis — a launch backend
    that provisioned lazily would have neither the transaction nor the rollback,
    and a `launch` raising mid-pane would leave an unrecorded container. So this
    backend only ever EXECS into what `spec.container` already identifies.

    Env crosses the boundary the container way: the hermetic `spec.env` is
    folded into `--remote-env KEY=VAL` so it reaches the in-container process,
    and the host pane exports nothing (a fresh container needs no `env_unset`
    host-leak scrub — nothing crosses in unless named).

    **The exec normally runs the agent under a tmux INSIDE the
    container** (see `_entry_tokens`), which is what makes the agent survive its
    host client and be reattachable. It remains ONE backend and one code path:
    the difference is which tokens follow `--`, decided by a fact the
    provisioner probed and recorded, not by a second class.

    One consequence worth knowing, because it narrows what `exit_record`
    means: it captures the exit of what the PANE ran, which under tmux is the
    tmux *client*, not the agent. A failure to reach the container or to start
    tmux still records non-zero, but an agent that dies inside a live tmux
    session no longer does — its death now leaves an inspectable session
    instead of nothing, and reading/steering through the in-container tmux is
    what turns that back into a signal.
    """

    provides_pane: ClassVar[bool] = True

    def __init__(self, cli: DevcontainerCli | None = None) -> None:
        self._cli = cli if cli is not None else DevcontainerCli()

    def launch(self, spec: LaunchSpec) -> None:
        container = spec.container
        if container is None:
            # Never provision from here (see the class docstring): a
            # container-less spec is a manager bug, and failing loudly beats
            # silently launching the agent on the host — which is exactly the
            # silent downgrade the runtime contract forbids.
            #
            # This deliberately does not re-check `container.provisioned`.
            # Provisioning failure is fatal at ONE site,
            # `WorkspaceManager._provision_container`, which every launch verb
            # passes through before it composes a spec — so a second copy of
            # that decision here could not fire, and a guard that cannot fire
            # reads as protection in review while protecting nothing. The real
            # refusal for a container that came up unprovisioned is upstream and
            # persisted: the record reconciles to OFFLINE, which `attach` and
            # `steer` refuse outright.
            raise ContainerError(
                f"workspace session {spec.session_name} has runtime=container but no "
                "container to exec into; respawn it to provision one"
            )
        self._clear_dead_agent_session(container, spec)
        if container.tmux_command:
            self._launch_under_container_tmux(container, spec)
            return
        prefix = self._cli.exec_argv(
            spec.worktree,
            id_labels=container.id_labels,
            override_config=container.override_config,
            remote_env=spec.env,
        )
        command = " ".join(
            [
                *(shlex.quote(token) for token in prefix),
                *self._entry_tokens(container, spec),
            ]
        )
        tmux.create_session(
            spec.session_name, cwd=spec.cwd, history_limit=spec.cfg.tmux.history_limit
        )
        # The pane runs `devcontainer exec … -- <command>`; the adapter
        # decoration is appended by build_workspace_layout exactly as for a host
        # launch, so `--session-id`/`--resume`/`--model`/the prompt positional
        # flow through unchanged. Host env is empty — env crossed via
        # `--remote-env` above.
        tmux.build_workspace_layout(
            spec.session_name,
            cfg=spec.cfg,
            worktree=spec.cwd,
            command=command,
            decoration=spec.decoration,
            env={},
            env_unset=(),
            exit_suffix=_exit_suffix(spec),
            # Window 0 crosses the boundary too. A HOST shell rooted at the
            # worktree here would be a real inconsistency: attach, switch
            # window, and be silently on the host beside a container you
            # thought you were in. Same in-container tmux session `grove
            # shell` attaches to, so the shell window and the shell verb are
            # one persistent shell rather than two.
            shell_command=ContainerShell(
                container=container,
                cfg=spec.cfg,
                worktree=spec.worktree,
                cwd=spec.cwd,
                cli=self._cli,
            ).command,
        )

    def _launch_under_container_tmux(
        self, container: ContainerRuntimeState, spec: LaunchSpec
    ) -> None:
        """Start the agent DETACHED inside the container, with no host session.

        The arm taken whenever the container can run a tmux, which is every
        container workspace but the degraded one below. The in-container tmux
        owns the agent's lifetime, so a host session adds nothing a client
        wants and takes something away: its pane stays attached as a
        persistent client, and tmux sizes a window to its SMALLEST attached
        client — measured, a human asking for 200x50 got the shadow pane's
        161x41. `attach` execs into the container's tmux directly instead.

        The shell session is started here too, detached and best-effort, so
        `grove shell` (and the shell a user switches to after attaching) is
        sitting in the same in-container server rather than being minted by
        whoever asks first. Its failure must not fail a launch: the agent is
        the workspace, and `-A` would create the shell on demand anyway.

        Nothing records an exit here, and that is not a regression: `exit_record`
        captures what the PANE ran, which under tmux is the tmux client rather
        than the agent (the narrowing this class already documents). The
        agent's real exit is carried by `remain-on-exit` INSIDE the container
        and read back through `ContainerTmux`.
        """
        entry = ContainerAgentEntry(
            container=container,
            cfg=spec.cfg,
            worktree=spec.worktree,
            cwd=spec.cwd,
            session=spec.cfg.container.tmux.session,
            command=spec.command,
            decoration=spec.decoration,
            env=spec.env,
            cli=self._cli,
        )
        rc, out = entry.start()
        if rc != 0:
            raise ContainerError(
                f"could not start the agent inside workspace session {spec.session_name}'s "
                f"container (exit {rc}): {out.strip() or 'no output'}"
            )
        shell = ContainerShell(
            container=container,
            cfg=spec.cfg,
            worktree=spec.worktree,
            cwd=spec.cwd,
            cli=self._cli,
        )
        try:
            shell_rc, shell_out = shell.start()
        except ContainerError as exc:
            logger.warning("container shell: could not pre-start the shell session: {}", exc)
            return
        if shell_rc != 0:
            logger.warning(
                "container shell: the shell session did not start (exit {}): {}",
                shell_rc,
                shell_out.strip() or "no output",
            )

    def transcript_context(self, spec: LaunchSpec) -> TranscriptContext | None:
        """The host side of the agent-config mount, paired with the CONTAINER cwd.

        The asymmetry only a backend can honor, and the reason it is
        computed here rather than in the manager:

        * ``config_dir`` is a HOST path — where the agent-config mount lands on
          this filesystem (:attr:`AgentSharePlan.transcript_host_dir`), because
          the read side scopes its own `CLAUDE_CONFIG_DIR`/`CODEX_HOME` to it
          and must therefore be aimed at a directory that exists HERE. Never
          `spec.env`'s value: that is the container-internal path, the exact
          wrong side, and pointing the reader at it is worse than recording
          nothing.
        * ``agent_cwd`` stays the CONTAINER string, untranslated — it is matched
          opaquely against the `cwd` each transcript record wrote for itself, so
          a host path there would never match anything.

        ``None`` where nothing is genuinely reachable: an unprovisioned launch
        (no container to have a cwd inside), a kind with no config-dir concept,
        or a plan whose config root is unbound.
        """
        container = spec.container
        plan = spec.share_plan
        if container is None or plan is None:
            return None
        host_dir = plan.transcript_host_dir
        agent_cwd = container.workdir(worktree=spec.worktree, cwd=spec.cwd)
        if host_dir is None or not agent_cwd:
            return None
        return TranscriptContext(config_dir=str(host_dir), agent_cwd=agent_cwd)

    def control_path(
        self, host_path: Path, *, share_plan: AgentSharePlan | None = None
    ) -> str | None:
        """Where the container sees this control file, or ``None`` if nowhere.

        The rewrite the placeholder here promised, now that
        :class:`~grove.core.container_policy.AgentSharePlan` binds Grove's
        control files into the container. The answer comes off that plan's mount
        table — the same table handed to ``devcontainer up`` — so "reachable"
        means *actually mounted*, not "the kind of file we usually mount".

        The old identity answer reasoned that a host path "at least resolves
        whenever the operator bind-mounted that directory". It does, and that is
        beside the point: in every other case Claude Code treats a missing
        ``--settings`` as fatal, so the placeholder was not a degradation but a
        silent outage of the whole workspace — the pane showed
        ``Error: Settings file not found`` and a shell prompt while ``grove
        create`` had already exited 0.

        ``None`` for a plan-less spec (an unprovisioned launch, or a kind with
        no plan at all) and for any path outside the table, because for this
        backend an untranslated host path is never better than no flag.
        """
        if share_plan is None:
            return None
        target = share_plan.container_control_path(host_path)
        return str(target) if target is not None else None

    @staticmethod
    def _clear_dead_agent_session(container: ContainerRuntimeState, spec: LaunchSpec) -> None:
        """Drop an in-container session whose agent already exited, so `-A` creates.

        The cost of making the agent's death INSPECTABLE (`remain-on-exit`):
        a dead pane keeps its session alive, and `tmux new-session -A`
        against it neither attaches usefully nor relaunches — measured, it
        exits 1 and starts nothing. Without this, `resume` and `respawn`
        would report success while the workspace kept staring at the corpse
        of the previous agent, defeating the whole point of a reattach
        design.

        Deliberately conditional on the pane being DEAD, never unconditional: a
        session with a LIVE agent is exactly what `-A` must attach to, and
        killing it here would make every resume/respawn destroy the running
        agent. Best-effort throughout: an unreadable docker leaves the session
        alone and the launch proceeds, so the worst case is the pre-existing
        behaviour.
        """
        container_tmux = ContainerTmux.for_container(container, cfg=spec.cfg.container)
        if container_tmux is None:
            return
        reading = container_tmux.read()
        if reading is None or reading.report is None or not reading.report.dead:
            return
        logger.info(
            "container tmux: the previous agent in session {} exited (status {}); "
            "clearing the dead session so this launch starts a new one",
            spec.cfg.container.tmux.session,
            reading.report.exit_status,
        )
        container_tmux.end_session()

    @staticmethod
    def _entry_tokens(container: ContainerRuntimeState, spec: LaunchSpec) -> list[str]:
        """The tokens appended after `--` for a container with NO reachable tmux.

        The ONLY thing a host pane ever runs for a container workspace: where a
        tmux IS reachable the agent starts detached inside the container and no
        host session is created at all (see :meth:`_launch_under_container_tmux`),
        so the tmux shape this used to also compose has exactly one owner now,
        :class:`~grove.core.container_agent.ContainerAgentEntry`.

        The agent runs and the workspace works; it just dies with its client.

        A NESTED project still needs the one ``cd``, which is what the
        ``sh -c`` wrapper is for — and it has to end somewhere that FORWARDS
        extra argv to the agent, because ``build_workspace_layout`` appends the
        decoration as ordinary shell-quoted argv AFTER whatever this returns and
        ``"$@"`` inside the script is what hands it on with spaces intact. A
        flat workspace appends the bare command and stays byte-comparable with a
        host launch.

        :class:`~grove.core.container_tmux.TmuxEntry` with an empty ``command``
        is the honest spelling of "there is no tmux": it returns the target
        untouched. Composing through it rather than around it keeps one
        definition of the entry for both arms.
        """
        workdir = container.workdir(worktree=spec.worktree, cwd=spec.cwd)
        nested = bool(workdir) and workdir != container.remote_workspace_folder
        if not nested:
            return [spec.command]
        # The cd + `"$@"`-forwarding script is shared with the in-container
        # launch and with the ADDITIONAL agents a user may start, so the arms
        # cannot drift on the nested-cwd correction or the decoration
        # hand-off.
        script = ContainerAgentEntry.script_for(
            container, worktree=spec.worktree, cwd=spec.cwd, command=spec.command
        )
        entry = TmuxEntry(
            command=container.tmux_command,
            session=spec.cfg.container.tmux.session,
            term_fallback=spec.cfg.container.tmux.term_fallback,
            conf=container.tmux_conf,
        )
        return [shlex.quote(token) for token in entry.tokens(("sh", "-c", script, "grove"))]
