"""Detached local-process side effects — the headless runtime's spawn surface.

The counterpart of ``grove.core.tmux`` for a runtime that has no terminal: it
starts an assembled agent command as a background OS process, detached from
Grove's own process group, and returns. All ``subprocess`` I/O for the headless
backend lives here, so ``launch.py`` stays pure orchestration (the same
discipline that keeps tmux calls out of the manager). Dependencies flow inward —
this module knows nothing of ``launch`` or the manager.

Deliberately minimal: spawn-and-detach only. Real lifecycle — liveness
probing, stream-json stdio wiring, interrupt — arrives with the native input
channels; this is the foundation they build on.

``list_agent_runtimes`` (the Session Catalog epic) is the read-side sibling of
``spawn_detached``: a bounded ``/proc`` scan for OTHER already-running
claude/codex processes Grove never spawned — the liveness signal a host-wide
session catalog needs and no code anywhere in Grove reads today.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

from grove.core.errors import ProcessError

# The only two filesystem-transcript adapters run as a local host process at
# all (mewbo is remote, generic has no known binary) — this maps the real
# `/proc/<pid>/comm` value (verified on-host: a running Claude Code CLI's comm
# is literally ``claude``) to the matching `AgentAdapter.kind` discriminator.
_RUNTIME_KINDS: Final[dict[str, str]] = {
    "claude": "claude_code",
    "codex": "codex",
    "opencode": "opencode",
}


def spawn_detached(
    command: str,
    *,
    decoration: Sequence[str] = (),
    cwd: Path,
    env: Mapping[str, str] | None = None,
    env_unset: Sequence[str] = (),
) -> int:
    """Start ``command`` (+ ``decoration`` argv) as a detached process in ``cwd``.

    Returns the child PID. ``command`` is parsed with ``shlex.split`` and run
    with ``shell=False``, so ``decoration`` tokens pass verbatim with no quoting
    round-trip — unlike the tmux pane, which types into a shell (hence
    ``build_workspace_layout`` shell-quotes there and this does not).

    The child env is made hermetic exactly as ``build_workspace_layout`` makes
    the pane hermetic: drop ``env_unset`` from a copy of the ambient env
    first, then overlay ``env`` — so a key present in both ends up set from
    ``env``. ``start_new_session=True`` detaches the child into its own session /
    process group so it outlives Grove and never receives Grove's terminal
    signals; stdio goes to ``/dev/null`` (a headless agent records its transcript
    on the filesystem, not via stdout — full stdio wiring is future work).
    Raises :class:`ProcessError` if the binary is missing or the OS refuses
    the spawn.
    """
    argv = [*shlex.split(command), *decoration]
    dropped = set(env_unset)
    child_env = {k: v for k, v in os.environ.items() if k not in dropped}
    child_env.update(env or {})
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProcessError(f"could not spawn headless agent {argv!r}: {exc}") from exc
    logger.info("spawned headless agent pid={} in {}: {}", proc.pid, cwd, " ".join(argv))
    return proc.pid


def die_with_parent() -> None:
    """``preexec_fn`` asking the kernel to signal this child when Grove dies.

    The inverse of ``spawn_detached``'s contract, and the distinction is the
    whole point. An AGENT must outlive the daemon — that is why
    ``KillMode=process`` exists and why a reinstall does not end a session. An
    OBSERVER must not: a ``tmux -C attach-session`` reader is meaningless once
    the process that would consume its frames is gone, and tmux's server is
    single-threaded, so one control client that nobody reads from stops the
    server servicing its accept loop. Every later connection is then accepted
    and dropped, which tmux's client reports as the actively misleading
    ``server exited unexpectedly`` — measured on this host as a wedge that
    outlived the daemon by 22 hours and failed every workspace create.

    ``start_new_session=True`` is what makes this necessary rather than
    automatic: it detaches the child into its own session so no terminal or
    process-group signal reaches it, and systemd's ``KillMode=process`` signals
    only the daemon's main PID, so an observer is reparented to ``systemd
    --user`` and simply stays. ``PR_SET_PDEATHSIG`` is the one mechanism that
    still fires, because it is keyed on the parent DYING rather than on any
    signal being delivered.

    Best-effort and Linux-only by construction: the ``prctl`` is absent on
    macOS, and an observer that merely fails to self-reap is the status quo, so
    a failure here must never take the spawn down with it.
    """
    if sys.platform != "linux":  # pragma: no cover - PR_SET_PDEATHSIG is Linux-only
        return
    # Never fail a spawn over a reaping hint: an observer that does not
    # self-reap is the status quo this fixes, not a new failure.
    with contextlib.suppress(Exception):
        # 1 is PR_SET_PDEATHSIG; hard-coded because ctypes exposes no prctl
        # constants and the value is ABI-stable in <linux/prctl.h>.
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0)


@dataclass(slots=True, frozen=True)
class ProcessTree:
    """Snapshot a pane's process identities before its parent/session disappears."""

    identities: tuple[tuple[int, str], ...]
    proc_root: Path = Path("/proc")

    @staticmethod
    def _identity(path: Path) -> tuple[int, str] | None:
        try:
            # comm may contain spaces or parentheses; stat's final ')' ends it.
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return int(fields[1]), fields[19]
        except (FileNotFoundError, ProcessLookupError):
            return None
        except (OSError, IndexError, ValueError) as exc:
            raise ProcessError(f"cannot verify process identity at {path}") from exc

    @classmethod
    def capture(
        cls, roots: Sequence[int], *, proc_root: Path = Path("/proc"), include_roots: bool = True
    ) -> ProcessTree:
        if sys.platform != "linux":
            raise ProcessError("verified native runtime recovery requires Linux /proc")
        try:
            entries = tuple(proc_root.iterdir())
        except OSError as exc:
            raise ProcessError("cannot inspect native runtime processes") from exc
        rows = {
            int(entry.name): identity
            for entry in entries
            if entry.name.isdigit() and (identity := cls._identity(entry)) is not None
        }
        selected = set(roots) & rows.keys()
        ordered = sorted(selected) if include_roots else []
        while descendants := (
            {pid for pid, (parent, _) in rows.items() if parent in selected} - selected
        ):
            selected.update(descendants)
            ordered.extend(sorted(descendants))
        return cls(tuple((pid, rows[pid][1]) for pid in reversed(ordered)), proc_root)

    def kill_and_wait(self, *, timeout: float = 5.0) -> None:
        """SIGKILL the captured incarnations. The backstop for a declined SIGTERM.

        Reserved for survivors that already ignored both tmux's SIGHUP and a
        SIGTERM -- a shell wedged in a startup loop on an unlinked cwd never
        reaches a point where it handles a catchable signal, so nothing short
        of SIGKILL reclaims the core it is spinning. Identity-guarded on the
        captured start time exactly as `terminate_and_wait`, so a recycled pid
        is never signalled.
        """
        self._signal_and_wait(signal.SIGKILL, timeout=timeout)

    def terminate_and_wait(self, *, timeout: float = 5.0) -> None:
        """Signal only the captured incarnations, then refuse overlap on timeout."""
        self._signal_and_wait(signal.SIGTERM, timeout=timeout)

    def _signal_and_wait(self, sig: signal.Signals, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        # Children exit before their ancestors, so a refusal leaves the pane's
        # ancestry available to inspect again rather than orphaning a provider.
        for pid, start in self.identities:
            identity = self._identity(self.proc_root / str(pid))
            if identity is None or identity[1] != start:
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                continue
            except OSError as exc:
                raise ProcessError(f"cannot stop native runtime process {pid}") from exc
            while True:
                identity = self._identity(self.proc_root / str(pid))
                if identity is None or identity[1] != start:
                    break
                if time.monotonic() >= deadline:
                    raise ProcessError(
                        "native runtime did not stop; refusing to launch another owner"
                    )
                time.sleep(0.05)


def reap_cwd_holders(
    root: Path, *, proc_root: Path = Path("/proc"), timeout: float = 2.0
) -> tuple[int, ...]:
    """Signal every process whose cwd is inside ``root``. Returns the pids hit.

    The teardown counterpart to :func:`list_agent_runtimes`: that one answers
    "which agents are alive", this one answers "who is standing in the
    directory I am about to unlink". Unlinking it out from under them is what
    strands a shell on a dead cwd, and a git/node-aware prompt then spins a
    full core re-globbing a path that no longer resolves (measured on the
    reference host 2026-09-18: nine survivors, ~7.6 of 8 cores, 9.5 CPU-days).

    Scans ALL pids rather than a pane subtree on purpose -- the survivors
    reparent to ``systemd --user`` the moment their tmux session dies, so by
    teardown they are no longer descendants of anything Grove can name. Reads
    only the calling user's processes: a foreign-uid ``/proc/<pid>/cwd`` raises
    ``PermissionError`` and is skipped.

    Never raises -- it runs on a teardown path and a failure to enumerate must
    not turn a good pause into an error.
    """
    if sys.platform != "linux":
        return ()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return ()
    root = root.resolve() if root.exists() else root
    pids: list[int] = []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            # A deleted cwd reads back as "/path (deleted)"; strip the marker so
            # a worktree already half-removed still matches.
            cwd = os.readlink(entry / "cwd").removesuffix(" (deleted)")
        except OSError:
            continue  # vanished mid-scan, or another user's process
        if cwd == str(root) or cwd.startswith(f"{root}/"):
            pids.append(int(entry.name))
    if not pids:
        return ()
    try:
        tree = ProcessTree.capture(tuple(pids), proc_root=proc_root, include_roots=True)
    except ProcessError:
        return ()
    try:
        tree.terminate_and_wait(timeout=timeout)
    except ProcessError:
        # Already declined SIGTERM; a shell wedged mid-rc never handles one.
        try:
            tree.kill_and_wait(timeout=timeout)
        except ProcessError as exc:
            logger.warning("could not clear processes holding {}: {}", root, exc)
    return tuple(pids)


@dataclass(slots=True, frozen=True)
class LiveRuntime:
    """One running local agent process, resolved only to its cwd — the
    Session Catalog's liveness unit.

    Deliberately NOT a pid-to-session binding. Verified on-host: several
    agents routinely share one cwd (20+ concurrent ``claude`` processes
    observed on one host), and the two obvious finer-grained signals are
    both dead ends — a running ``claude`` holds ZERO open fds on its
    transcript (it appends and closes), and its argv carries 700-1300
    tokens with no session id. So ``cwd`` is the only supportable
    correlation; folding this against catalog rows is a separate pure
    operation (``SessionCatalog.fold_liveness``) that must never invent a
    1:1 pid-to-session pairing.
    """

    pid: int
    kind: str
    cwd: Path
    started_at: datetime


def list_agent_runtimes(*, proc_root: Path = Path("/proc")) -> tuple[LiveRuntime, ...]:
    """Every running ``claude``/``codex`` process, cwd-resolved.

    ONE bounded ``/proc`` scan per catalog request — never per row, never on
    the 2 s activity poll. Reads only ``/proc/<pid>/comm`` (to recognize a
    known binary; verified on-host: a running Claude Code CLI's comm is
    literally ``claude``) and ``/proc/<pid>/cwd``, for the CALLING user's own
    processes only — a foreign-uid pid's ``/proc/<pid>/cwd`` raises
    ``PermissionError`` and is silently skipped, so Grove never reads
    another user's processes. ``started_at`` is the ``/proc/<pid>`` entry's
    own ctime (it appears at process creation) — close enough for a
    freshness join, and avoids parsing ``/proc/<pid>/stat`` jiffies against
    the boot clock for no added precision this seam needs.

    ``proc_root`` defaults to the real ``/proc`` and exists only as a test
    seam (a fake tree, never a monkeypatched internal) — production never
    passes it.

    No new dependency (``psutil`` is not warranted for reading ``/proc``).
    Degrades to ``()`` on any non-Linux platform (a typed no-op, guarded on
    ``sys.platform`` per the cross-platform discipline) and on any
    unexpected error enumerating ``/proc`` itself — never raises, the same
    best-effort contract as ``peek()``.
    """
    if sys.platform != "linux":
        return ()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return ()
    runtimes: list[LiveRuntime] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
            kind = _RUNTIME_KINDS.get(comm)
            if kind is None:
                continue
            cwd = os.readlink(entry / "cwd")
            started_at = datetime.fromtimestamp(entry.stat().st_ctime, tz=UTC)
        except OSError:
            continue  # vanished mid-scan, or another user's process — skip
        runtimes.append(
            LiveRuntime(pid=int(entry.name), kind=kind, cwd=Path(cwd), started_at=started_at)
        )
    return tuple(runtimes)
