"""Typer subcommand: ``grove daemon serve``.

Embeds uvicorn programmatically (``uvicorn.Server.serve()`` inside
``asyncio.run()``) so signal handling and lifespan come from uvicorn's
own machinery, not Typer's.
"""

from __future__ import annotations

import asyncio

import typer
import uvicorn
from loguru import logger

app = typer.Typer(help="Grove API daemon")


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
