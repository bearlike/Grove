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

import os
import shlex
import subprocess
import sys
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
_RUNTIME_KINDS: Final[dict[str, str]] = {"claude": "claude_code", "codex": "codex"}


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
