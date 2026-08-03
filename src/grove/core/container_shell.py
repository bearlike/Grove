"""How a HUMAN gets an interactive shell inside a workspace's container.

One question: *what is the command that drops someone into this workspace's
container, at the agent's own cwd, in a shell that is still there next time?*

The gap this closes was a scoping error rather than an implementation defect.
Shipping only a VS Code attach was justified with "a shell in the container is
available for free" — and there was none. ``tmux.build_workspace_layout`` made
window 0 a **host** shell rooted at the worktree, so a user who attached and
switched windows was silently on the host with a worktree the container may
not even see the same way; only the agent window ever crossed the namespace
boundary. The single sanctioned route in was ``grove code``, which hands off
to a proprietary VS Code extension that is not on Open VSX — structurally
excluding Cursor and VSCodium users from their own container.

Two consumers, one definition, which is the reason this is a module and not two
call sites: the ``grove shell`` verb ``execvp``s :meth:`ContainerShell.argv`,
and :class:`~grove.core.launch.DevcontainerLaunchBackend` starts the same
session at launch — detached inside the container (:meth:`ContainerShell.start`)
where there is a tmux in there, or typed into host window 0
(:attr:`ContainerShell.command`) on the degraded no-tmux path. They must be the
same shell session or "the shell window" and "the shell verb" become two
different shells with two different histories.

Dependencies flow inward: this imports ``config`` / ``container_runtime`` /
``container_tmux`` / ``devcontainer``; ``launch`` imports it, never the reverse.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from grove.core.config import GroveConfig
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.container_tmux import TmuxEntry
from grove.core.devcontainer import DevcontainerCli


@dataclass(frozen=True, slots=True)
class ContainerShell:
    """The interactive-shell entry into ONE provisioned container.

    Pure composition over facts that are already recorded — the container's
    identity labels, the override config ``up`` used, the tmux the provisioner
    probed — so the whole thing is testable without a container, and nothing
    here decides *whether* a workspace has a container (that precondition
    belongs to the caller, which is the only layer that can phrase it usefully).

    The shell runs under the in-container tmux whenever one is reachable, for
    the same reason the agent does: the exec's PTY dies with the host client,
    but a tmux session inside the namespace outlives it, so leaving and coming
    back finds the same shell with its history, its environment and whatever it
    was still running. ``container.tmux.shell_session`` is what ``-A`` keys on,
    so every visit reattaches rather than stacking a fresh shell per visit.
    """

    container: ContainerRuntimeState
    cfg: GroveConfig
    worktree: Path
    """The worktree ROOT — what ``devcontainer exec --workspace-folder`` needs,
    and the root :meth:`ContainerRuntimeState.workdir` measures ``cwd`` against.
    Never the agent cwd: conflating the two silently computes a zero offset
    for a nested project."""

    cwd: Path
    """Where the shell should start — ``WorkspaceState.agent_cwd``, so a nested
    project's shell lands in the project rather than at the mount root."""

    cli: DevcontainerCli = field(default_factory=DevcontainerCli)
    """The exec-argv boundary, injected so a caller that already holds one
    (the launch backend) reuses it and tests supply a scripted stand-in."""

    def argv(self) -> list[str]:
        """The full ``devcontainer exec … -- …`` argv, ready for ``execvp``.

        No ``--remote-env``: everything an interactive shell needs inside the
        container is already ``remoteEnv`` on the container itself — including
        ``TERMINFO_DIRS`` for Grove's mounted tmux bundle, which rides there
        precisely because it has to apply to EVERY exec rather than the agent's.
        """
        prefix = self.cli.exec_argv(
            self.worktree,
            id_labels=self.container.id_labels,
            override_config=self.container.override_config,
        )
        return [*prefix, *self._entry(detached=False).tokens(("sh", "-c", self.script))]

    def start(self) -> tuple[int, str]:
        """Start the shell session DETACHED; return the CLI's ``(exit code, stdout)``.

        :meth:`ContainerAgentEntry.start`'s counterpart, and used for the same
        reason: with no host session to type the shell into, the launch
        puts it in the container's own tmux server so a user who attaches finds
        it already there. ``-A`` still creates it on demand for anyone who never
        went through a launch, so this is a convenience rather than a
        precondition — which is why the caller treats a failure as best-effort.
        """
        return self.cli.exec(
            self.worktree,
            self._entry(detached=True).tokens(("sh", "-c", self.script)),
            id_labels=self.container.id_labels,
            override_config=self.container.override_config,
        )

    @property
    def command(self) -> str:
        """The same entry as one shell-quoted line, for a HOST tmux pane to run.

        Only the degraded arm reaches this — a container with a tmux in it has
        no host session for a pane to live in.

        Quoted here rather than by the pane: ``build_workspace_layout`` takes a
        command STRING (it types it into a shell), so the quoting is part of
        composing the command, not part of running it.
        """
        return " ".join(shlex.quote(token) for token in self.argv())

    @property
    def script(self) -> str:
        """``sh`` that cds to the project and execs the first shell that exists.

        Resolution is in-container by construction: which of
        ``container.shell`` an image actually ships is a fact only the image
        can answer, and ``command -v`` answers it for free in the same process
        that is about to exec. A miss on the whole chain is loud and non-zero
        rather than a silent substitution — falling back to some hard-coded
        shell would put a name in code that the config exists to own, and would
        hide a misconfiguration behind a working-looking prompt.
        """
        workdir = self.container.workdir(worktree=self.worktree, cwd=self.cwd)
        chain = self.cfg.container.shell
        names = " ".join(shlex.quote(name) for name in chain)
        lines = []
        if workdir and workdir != self.container.remote_workspace_folder:
            # Only for a nested project: an exec already lands in the
            # configuration's own workspaceFolder, and the CLI has no --workdir.
            lines.append(f"cd {shlex.quote(workdir)} || exit 1")
        lines += [
            f"for __grove_shell in {names}; do",
            '  command -v "$__grove_shell" >/dev/null 2>&1 && exec "$__grove_shell"',
            "done",
            f'echo "grove: none of the shells in container.shell ({names}) exist in this '
            'container" >&2',
            "exit 127",
        ]
        return "\n".join(lines)

    def _entry(self, *, detached: bool) -> TmuxEntry:
        tmux = self.cfg.container.tmux
        return TmuxEntry(
            command=self.container.tmux_command,
            session=tmux.shell_session,
            term_fallback=tmux.term_fallback,
            detached=detached,
            conf=self.container.tmux_conf,
        )


__all__ = ["ContainerShell"]
