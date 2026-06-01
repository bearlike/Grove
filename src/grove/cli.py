"""Top-level Grove CLI.

Adopts the existing TUI Typer app as the top-level entry (back-compat
with the historical ``grove`` script: ``grove ls`` / ``grove version``
/ ``grove config show`` keep working unchanged) and grows the ``daemon``
subcommand when its extra is installed. A missing extra silently skips
the mount, so a bare TUI install keeps the historical behavior.

This is the project's "mechanism, not policy" surface (CLAUDE.md):
the user composes their install with ``grove[daemon|client|all]``,
the CLI grows the corresponding subcommands, and a single ``grove``
binary stays the deterministic entry point.

Why we adopt ``tui_app`` rather than ``add_typer(tui_app)``: ``add_typer``
mounts the sub-app under its own ``name`` ("grove"), which would nest
every existing TUI command (``grove ls`` → ``grove grove ls``). Adopting
the existing app and grafting the daemon onto it preserves the
historical surface byte-for-byte while keeping the compose seam.
"""

from __future__ import annotations

from grove.tui.cli import app

try:
    from grove.daemon.cli import app as daemon_app

    app.add_typer(daemon_app, name="daemon", help="Grove API daemon")
except ImportError:  # daemon extras not installed
    pass

__all__ = ["app"]


# Make ``python -m grove.cli`` actually run the Typer app. ``LocalTransport``
# (grove.client) spawns the daemon via this exact invocation; without this
# entrypoint, the module is imported and nothing executes — the subprocess
# exits 0 with no stdout, and the SDK can't read the picked port.
if __name__ == "__main__":
    app()
