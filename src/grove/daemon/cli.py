"""Typer subcommand: ``grove daemon serve``.

Embeds uvicorn programmatically (``uvicorn.Server.serve()`` inside
``asyncio.run()``) so signal handling and lifespan come from uvicorn's
own machinery, not Typer's.
"""

from __future__ import annotations

import asyncio
import os
import sys

import typer
from loguru import logger

app = typer.Typer(help="Grove API daemon")


def _arm_parent_death_signal() -> None:
    """Exit if the process that spawned us is already gone, else die when it dies.

    Gated on ``--print-port`` in ``serve()`` below, not a dedicated flag:
    that flag's own help text already says "used by LocalTransport", so its
    presence already means "I am an ephemeral daemon owned by one caller's
    process lifetime, not the supervised systemd unit" — reusing it is one
    fewer knob for a property that has exactly one true today. This is a
    real coupling, not a coincidence: if `--print-port` ever grows a second
    caller that does NOT want death-linkage, split it into its own flag
    then — don't preempt that here.

    Without this, a SIGKILLed/OOM-killed/terminal-closed parent leaves this
    process running forever, reparented to init, still polling every 2s
    (measured on the reference host: two such orphans at 1.4% CPU each
    after 35 hours).

    Linux-only — ``prctl`` has no equivalent on macOS/Windows; degrades to a
    no-op there, same shape as ``paths.exclusive_lock``'s ``fcntl`` guard.

    Deliberately done HERE, in the exec'd child's own startup, rather than
    via ``Popen(preexec_fn=...)`` in the launcher. ``preexec_fn`` runs
    Python/library code between fork() and exec() *in the launcher's
    process*, and fork() only clones the calling thread — any lock another
    thread held at that instant (e.g. Textual's TUI driver thread, which
    genuinely runs alongside this codepath) can wedge the child forever.
    That hazard is specific to running code pre-exec; fork()+exec() itself
    is safe regardless of how many threads the launcher has. Arming after
    our own exec() sidesteps it entirely, at the cost of a longer window
    between fork and the signal being armed — which is exactly why the
    getppid() re-check below exists: closing that widened race is cheap
    and this is the one caller who actually needs it.
    """
    if sys.platform != "linux":
        return
    import ctypes  # noqa: PLC0415 - Linux-only; no reason to import elsewhere
    import signal  # noqa: PLC0415 - ditto

    parent_pid = os.getppid()
    PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL(None, use_errno=True)
    rc = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    if rc != 0:
        # Silently unarmed is exactly the failure this function exists to
        # prevent — log loudly rather than let a permission/seccomp denial
        # (the realistic causes) pass for "armed". Best-effort: this daemon
        # still has a job to do, so a failed self-safety-net doesn't block
        # startup, it just means the orphan risk below is back.
        logger.warning(
            "prctl(PR_SET_PDEATHSIG) failed (errno={}); this daemon will NOT die with its parent",
            ctypes.get_errno(),
        )
        return
    if os.getppid() != parent_pid:
        # The parent died in the (fork -> exec -> import -> here) window,
        # before the signal was armed — we've already been reparented, so
        # no SIGTERM is coming. Exit now rather than becoming the exact
        # orphan this function exists to prevent.
        os._exit(1)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    port: int = typer.Option(7421, help="Port to listen on (0 = auto-pick)"),
    print_port: bool = typer.Option(
        False,
        "--print-port",
        help="Print picked port to stdout on bind (used by LocalTransport).",
    ),
) -> None:
    """Run the Grove daemon (FastAPI + uvicorn)."""
    # Deferred: this module is imported by `grove.cli` to mount the `daemon`
    # subcommand, so EVERY `grove` invocation — including each shell-completion
    # round trip — paid uvicorn's ~125 ms import to run some other verb. The app
    # is already referenced by string ("grove.daemon._asgi:app"), so `serve` is
    # the only thing here that needs the package at all.
    import uvicorn  # noqa: PLC0415

    if print_port:
        _arm_parent_death_signal()
    config = uvicorn.Config(
        "grove.daemon._asgi:app",
        host=host,
        port=port,
        log_config=None,
        lifespan="on",
    )
    server = uvicorn.Server(config)

    async def _run() -> None:
        if print_port and port == 0:
            # Bind first so we can read the actual picked port, then print
            # it on a single stdout line. Used by the SDK's LocalTransport
            # to know where the spawned daemon is listening.
            #
            # Mirrors uvicorn.Server._serve's preamble: load() materializes
            # the ASGI app, lifespan_class(config) attaches the lifespan
            # protocol the underlying startup() call requires. Calling
            # server.startup() without these raises "Server has no attribute
            # 'lifespan'" — silently swallowed under asyncio.run, so the
            # subprocess exits 0 with no stdout.
            if not config.loaded:
                config.load()
            server.lifespan = config.lifespan_class(config)
            await server.startup()
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            print(actual_port, flush=True)
            await server.main_loop()
            await server.shutdown()
        else:
            if print_port:
                print(port, flush=True)
            await server.serve()

    logger.info("Grove daemon starting on {}:{}", host, port)
    asyncio.run(_run())
