"""``grove federation check`` — validate the federation block without starting anything.

The daemon disables an invalid block rather than refusing to start, so a typo
would otherwise surface only as a quiet ``disabled (<reason>)`` on ``/whoami``.
This runs the same cascade and the same validator, and exits non-zero when the
block is enabled but cannot run.
"""

from __future__ import annotations

import json

import typer

from grove.core.config import load_config

federation_app = typer.Typer(
    help="Federate several Grove daemons behind one hub. Back: grove --help.",
    no_args_is_help=True,
)


@federation_app.command("check")
def check() -> None:
    """Resolve the federation block from your config and report whether it would run."""
    federation = load_config(repo_root=None).federation
    typer.echo(
        json.dumps(
            {
                "status": federation.status,
                "config": federation.model_dump(mode="json"),
            },
            indent=2,
        )
    )
    if federation.disabled_reason is not None:
        raise typer.Exit(1)
