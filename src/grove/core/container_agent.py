"""SEVERAL agents in one container, each in its own in-container tmux session.

One question: *which agents are running inside this workspace's container, and
what is the command that starts, enters or ends one of them?*

The user's ask was narrow and explicit — "multiple Claude Code instances can
also run within the same container if necessary by the person of choice" — so
this is opt-in and user-driven, never something a create does on its own.

**The design decision, and what it rejected.** An extra agent is *not* a second
workspace record and *not* a plural ``agent_session_id``:

* **A second record sharing the container** was rejected because a record IS a
  worktree plus a branch: two records over one worktree makes ``kill`` on either
  destroy the other's working tree, and ``pause``'s dirty check, branch
  ownership and reconciliation all become co-tenancy questions with no honest
  answer. A second worktree is already a supported thing — it is ``grove
  create``.
* **Pluralising ``WorkspaceState.agent_session_id``** was rejected because it is
  a persisted-field change rippling through the status blend, the activity
  fingerprint, adoption, remap and the wire — and it buys nothing that is
  missing. ``ActivityService.sessions_for`` ALREADY surfaces every non-primary
  session it discovers in the workspace's cwd: a second agent of the same kind
  in the same container writes its transcript into the same mounted config dir
  with a birth after ``created_at``, so the existing discovery/adoption path
  adopts it as an extra ``fs_discovered`` session with no code change at all.
  The agent axis was never the gap.

What WAS missing is everything in this module: starting a second agent under a
distinct in-container session name, enumerating what is running, and reaching a
named one to attach, peek, steer or end it. So the persisted state added by this
story is **none**, and the migration cost is **none**: the container's own tmux
server is the source of truth for which agents exist there, exactly as the host
tmux server is already the source of truth for ``has_session``.

Two classes, mirroring the two halves of the question:

* :class:`ContainerAgent` — one named agent as a fact (and the naming rules,
  since a slot name is a tmux session name and therefore has a grammar).
* :class:`ContainerAgentEntry` — the ``devcontainer exec … -- tmux
  new-session …`` argv that starts or enters one.

Dependencies flow inward: this imports ``config`` / ``container_runtime`` /
``container_tmux`` / ``devcontainer`` / ``tmux``; ``launch`` and ``manager``
import it, never the reverse.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from grove.core import tmux as tmux_mod
from grove.core.config import GroveConfig
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_tmux import TmuxEntry
from grove.core.devcontainer import DevcontainerCli
from grove.core.errors import GroveError


@dataclass(frozen=True, slots=True)
class ContainerAgent:
    """One agent running inside a workspace's container, as a live fact.

    Built from what the container's tmux server reports, never from a stored
    list — see the module docstring for why that is the design rather than a
    shortcut. ``primary`` marks the workspace's OWN agent (the one the status
    blend, the activity axis and every existing lifecycle verb speak about);
    every other row is an additional agent a user asked for.
    """

    name: str
    primary: bool
    attached: bool
    idle_seconds: int | None

    #: What a slot name may be. A slot name IS a tmux session name, and tmux
    #: gives ``:`` and ``.`` meaning inside a target spec (``session:window.pane``),
    #: so a name carrying either would address something other than itself the
    #: first time it reached ``-t``. The same class of value-becomes-syntax bug
    #: the branch-name guard closed, answered the same way: validate at
    #: the one place names enter, rather than quoting at each use.
    NAME_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    @classmethod
    def validate_name(cls, raw: str) -> str:
        """A user-supplied slot name, or a typed refusal naming the rule."""
        name = raw.strip()
        if not name or "." in name or ":" in name or not cls.NAME_PATTERN.match(name):
            raise GroveError(
                f"invalid agent name {raw!r}: an agent name is a tmux session name, so it "
                "must start with a letter or digit and hold only letters, digits, '_' or "
                "'-' (':' and '.' are tmux target syntax)"
            )
        return name

    @classmethod
    def mint_name(cls, requested: str | None, *, base: str, taken: frozenset[str]) -> str:
        """The name a new agent gets: the user's, else the next free ``<base>-N``.

        The base comes from ``container.tmux.session`` — config, so the shape of
        a Grove agent session name is the operator's to set — and only the
        numeric suffix is mechanism. A requested name that is already running is
        refused rather than silently reattached: ``-A`` would attach to the
        existing agent and report success, which reads as "a second agent
        started" and is not.
        """
        if requested is not None:
            name = cls.validate_name(requested)
            if name in taken:
                raise GroveError(
                    f"agent {name!r} is already running in this container — "
                    "attach to it, or pick another name"
                )
            return name
        index = 2
        while f"{base}-{index}" in taken:
            index += 1
        return cls.validate_name(f"{base}-{index}")

    @classmethod
    def roster(
        cls,
        reports: Sequence[tmux_mod.SessionReport],
        *,
        primary: str,
        shell: str,
    ) -> tuple[ContainerAgent, ...]:
        """Every agent among the container's tmux sessions, primary first.

        The *shell* session is excluded because it is not an agent — it is the
        human's own ``grove shell``, and listing it as one would invite a user to
        steer text into their own prompt. Everything else on that server is an
        agent Grove or its user started, which is honest for the same reason the
        enumeration is a read: something a person started by hand in there IS
        running in there.
        """
        agents = [
            cls(
                name=report.name,
                primary=report.name == primary,
                attached=report.attached,
                idle_seconds=report.activity_seconds_ago(),
            )
            for report in reports
            if report.name != shell
        ]
        return tuple(sorted(agents, key=lambda agent: (not agent.primary, agent.name)))


@dataclass(frozen=True, slots=True)
class ContainerAgentEntry:
    """The argv that starts or enters ONE named agent session in a container.

    :class:`~grove.core.container_shell.ContainerShell`'s sibling, and split from
    it for the reason that module's own docstring gives for existing: the two
    answer different questions (*a shell for a human* vs *an agent process*) and
    share exactly what should be shared — :class:`TmuxEntry`, which is where the
    ``new-session -A`` semantics and the measured TERM-fallback retry live.

    Pure composition over facts already recorded, so the whole thing is testable
    without a container; :meth:`start` is the one impure method and it delegates
    the actual side effect to :class:`DevcontainerCli`, which is where process
    spawning belongs.

    **Why the devcontainer CLI road and not ``docker exec``.** Reads and steers
    take the docker road because they run per poll and 484 ms each is a
    different product (see :class:`~grove.core.container_tmux.ContainerTmux`).
    A launch is user-initiated and happens once, and it is the one road that
    answers for the REMOTE user's ``PATH`` and applies the container's own
    ``remoteEnv`` — including the ``TERMINFO_DIRS`` Grove's mounted tmux bundle
    needs. Paying half a second once to launch an agent exactly the way the
    primary agent was launched is the right trade.
    """

    container: ContainerRuntimeState
    cfg: GroveConfig
    worktree: Path
    """The worktree ROOT — what ``devcontainer exec --workspace-folder`` needs,
    and the root :meth:`ContainerRuntimeState.workdir` measures ``cwd`` against.
    Never the agent cwd: conflating the two silently computes a zero offset
    for a nested project."""

    cwd: Path
    """Where the agent should start — ``WorkspaceState.agent_cwd``."""

    session: str
    """The in-container tmux session name — this agent's reattach identity."""

    command: str = ""
    """The agent binary (``AgentSpec.command``). Empty composes a bare attach."""

    decoration: tuple[str, ...] = ()
    """The composed launch argv (``--session-id`` / ``--settings`` / ``--model``
    / the trailing prompt positional), forwarded to the agent through the same
    ``"$@"`` trick the primary launch uses."""

    env: Mapping[str, str] = field(default_factory=dict)
    """The hermetic launch env, crossing the boundary as ``--remote-env``."""

    cli: DevcontainerCli = field(default_factory=DevcontainerCli)
    """The exec boundary, injected so tests supply a scripted stand-in."""

    def argv(self, *, detached: bool) -> list[str]:
        """The full ``devcontainer exec … -- …`` argv.

        ``detached=True`` is a start (``-A -d``: the agent runs, nobody is
        dropped into it); ``detached=False`` is an attach, and the caller is
        expected to have established the session exists — see
        :meth:`TmuxEntry.tokens` for what happens if it raced away.
        """
        prefix = self.cli.exec_argv(
            self.worktree,
            id_labels=self.container.id_labels,
            override_config=self.container.override_config,
            remote_env=self.env,
        )
        return [*prefix, *self._entry(detached=detached).tokens(self._target)]

    def start(self) -> tuple[int, str]:
        """Start this agent detached; return the CLI's ``(exit code, stdout)``.

        Not raising on a non-zero inner exit is deliberate and matches
        ``DevcontainerCli.exec``'s own split: only a failure to invoke the CLI is
        an error, and what the inner command said is an answer the caller has to
        phrase for a human.
        """
        return self.cli.exec(
            self.worktree,
            self._entry(detached=True).tokens(self._target),
            id_labels=self.container.id_labels,
            override_config=self.container.override_config,
            remote_env=self.env,
        )

    @property
    def _target(self) -> tuple[str, ...]:
        """What the session runs — nothing at all for a bare attach."""
        if not self.command:
            return ()
        script = self.script_for(
            self.container, worktree=self.worktree, cwd=self.cwd, command=self.command
        )
        if self.container.tmux_command:
            script = self.remain_on_exit(self.container.tmux_command) + script
        return ("sh", "-c", script, "grove", *self.decoration)

    @staticmethod
    def remain_on_exit(tmux_command: str) -> str:
        """Shell that makes the agent's death survivable to look at.

        Prefixed to the in-container launch script, so it runs INSIDE the pane
        tmux just created — where ``$TMUX`` is set and a bare ``set-option -w``
        therefore lands on this window with no target to get wrong. That is what
        makes this a one-line composition rather than the rework it would have
        been on the host: there, the agent is typed into an interactive shell
        that survives it (so tmux sees no exit at all) and the hermetic env is
        sent as keystrokes into that same shell; here the agent is already the
        pane's own process and its env crossed the boundary as ``--remote-env``.

        What it buys, measured on a real container: the session outlives the
        agent carrying ``#{pane_dead_status}`` — 1 for a config error, 127 for a
        missing binary — which is the exit code the host recorder stopped
        seeing the moment a tmux client became what the host pane runs. The
        pane's earlier output is kept too, so a peek shows what the agent said
        before it died; an agent that dies within milliseconds of the pane
        being created leaves only the status, which is still the fact that
        matters. It is the ONLY exit signal a container workspace has, the
        host pane that used to record one being gone.

        It lives here rather than on the launch backend because every session
        this class starts wants it for the same reason — an ADDITIONAL agent's
        death is as worth inspecting as the primary one's — and a copy on the
        backend is how the two would have come to differ.

        Failure is swallowed on purpose: a tmux too old for the option, or any
        other refusal, must not take the agent launch down with it — the exit
        signal is worth less than the agent.
        """
        return f"{shlex.quote(tmux_command)} set-option -w remain-on-exit on >/dev/null 2>&1; "

    def _entry(self, *, detached: bool) -> TmuxEntry:
        tmux = self.cfg.container.tmux
        return TmuxEntry(
            command=self.container.tmux_command,
            session=self.session,
            # A detached start has no client, so the fallback is inert there
            # anyway; passing it keeps the two modes one composition.
            term_fallback=tmux.term_fallback,
            detached=detached,
            conf=self.container.tmux_conf,
        )

    @staticmethod
    def script_for(
        container: ContainerRuntimeState, *, worktree: Path, cwd: Path, command: str
    ) -> str:
        """``sh`` that runs *command* at the agent's own cwd, forwarding ``"$@"``.

        The one definition of "how an agent command runs inside a container",
        shared with :class:`~grove.core.launch.DevcontainerLaunchBackend` so the
        primary agent and any additional one cannot drift on the two things that
        are easy to get subtly different: the nested-project ``cd`` (an exec
        lands in the configuration's own ``workspaceFolder``, and the CLI has
        no ``--workdir``) and the ``"$@"`` forwarding that carries the
        adapter decoration through with spaces intact.
        """
        workdir = container.workdir(worktree=worktree, cwd=cwd)
        script = f'exec {command} "$@"'
        if workdir and workdir != container.remote_workspace_folder:
            script = f"cd {shlex.quote(workdir)} && {script}"
        return script


__all__ = ["ContainerAgent", "ContainerAgentEntry"]
