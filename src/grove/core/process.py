"""Detached local-process side effects — the headless runtime's spawn surface.

The counterpart of ``grove.core.tmux`` for a runtime that has no terminal: it
starts an assembled agent command as a background OS process, detached from
Grove's own process group, and returns. All ``subprocess`` I/O for the headless
backend lives here, so ``launch.py`` stays pure orchestration (the same
discipline that keeps tmux calls out of the manager). Dependencies flow inward —
this module knows nothing of ``launch`` or the manager.

Deliberately minimal (#146): spawn-and-detach only. Real lifecycle — liveness
probing, stream-json stdio wiring, interrupt — arrives with the native input
channel issues (#182/#172); this is the foundation they build on.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from loguru import logger

from grove.core.errors import ProcessError


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
    the pane hermetic (#82): drop ``env_unset`` from a copy of the ambient env
    first, then overlay ``env`` — so a key present in both ends up set from
    ``env``. ``start_new_session=True`` detaches the child into its own session /
    process group so it outlives Grove and never receives Grove's terminal
    signals; stdio goes to ``/dev/null`` (a headless agent records its transcript
    on the filesystem, not via stdout — full stdio wiring is #182/#172). Raises
    :class:`ProcessError` if the binary is missing or the OS refuses the spawn.
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
