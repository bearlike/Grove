"""``grove web`` — serve the web dashboard that ships inside the wheel.

The dashboard is a Next.js server, not static files: its route handlers are the
BFF that holds the daemon's bearer token server-side, and pairing state lives in
a file only a server process can read. So it needs a Node runtime, and Grove
brings its own: ``nodejs-wheel`` is a base dependency and installs ``node``
beside Grove's own Python, so a fresh ``uv tool install grove-factory`` serves
the dashboard on a machine with no Node at all, and a host whose nvm default is
too old is never consulted. A Node on PATH is only the fallback for an install
that somehow lacks it.

A source checkout has no bundle until ``make webapp-bundle`` stages one into
``src/grove/_webapp`` — which an editable install serves directly — so until then
the verb says how to get one instead of failing on a missing file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from grove.core import GroveError
from grove.tui.cli_workspace import clean_exit

# Next 16's floor; the webapp's own `engines.node` says the same.
_MIN_NODE_MAJOR = 22
_BUNDLE_DIR = Path(__file__).resolve().parent.parent / "_webapp"


def bundled_node() -> str | None:
    """The Node binary that ships with Grove, or ``None`` when this install lacks it.

    The binary inside the ``nodejs_wheel`` package, not the ``node`` console
    script beside the interpreter: that script is a Python launcher, so a
    service running it would keep a Python parent alive for the dashboard's
    whole life and hand systemd's stop signal to Python instead of Node.
    """
    try:
        from nodejs_wheel import executable  # noqa: PLC0415 — optional, see module docstring
    except ImportError:
        return None
    root = Path(executable.ROOT_DIR)
    node = root / "node.exe" if os.name == "nt" else root / "bin" / "node"
    return str(node) if node.is_file() else None


class WebappLauncher:
    """Resolve the bundled server and a new-enough Node, then run one on the other."""

    def __init__(self, bundle_dir: Path = _BUNDLE_DIR, node: str | None = None) -> None:
        self.bundle_dir = bundle_dir
        self.node = node

    def server_script(self) -> Path:
        script = self.bundle_dir / "server.js"
        if not script.is_file():
            raise GroveError(
                f"no bundled web dashboard at {self.bundle_dir}. Install the published "
                "package (`uv tool install grove-factory`), or from a source checkout "
                "run `make webapp-bundle`."
            )
        return script

    def node_binary(self) -> str:
        node = self.node or bundled_node() or shutil.which("node")
        if node is None:
            raise GroveError(
                "no Node.js found. Grove ships its own through `nodejs-wheel`; "
                "reinstall with `uv tool install --reinstall grove-factory`, or put "
                f"Node.js {_MIN_NODE_MAJOR}+ on PATH."
            )
        major = self.node_major(node)
        if major is not None and major < _MIN_NODE_MAJOR:
            raise GroveError(
                f"the web dashboard needs Node.js {_MIN_NODE_MAJOR} or newer; "
                f"{node} is version {major}"
            )
        return node

    @staticmethod
    def node_major(node: str) -> int | None:
        """The runtime's major version, or ``None`` when it cannot be probed.

        Asks the runtime itself (``process.versions.node``) rather than parsing
        ``node -v``, which is the same number here but is the habit that lies
        when an npm shim is involved.
        """
        try:
            out = subprocess.run(
                [node, "-p", "process.versions.node.split('.')[0]"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return int(out) if out.isdigit() else None

    def env(self, host: str, port: int, daemon_url: str) -> dict[str, str]:
        return {
            **os.environ,
            "HOSTNAME": host,
            "PORT": str(port),
            "GROVE_DAEMON_URL": daemon_url,
            "NODE_ENV": "production",
        }


def serve_web(
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    port: int = typer.Option(3000, help="Port to listen on"),
    daemon_url: str = typer.Option(
        "http://127.0.0.1:7421",
        envvar="GROVE_DAEMON_URL",
        help="The Grove daemon the dashboard talks to (`grove daemon serve`).",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Resolve the dashboard and its Node, print them, and exit without serving.",
    ),
) -> None:
    """Serve the web dashboard (needs a running daemon).

    Binds loopback by default; pass `--host 0.0.0.0` to reach it from another
    device, then approve that browser with `grove auth approve`.

    \b
      grove daemon serve &
      grove web
    """
    launcher = WebappLauncher()
    with clean_exit():
        script = launcher.server_script()
        node = launcher.node_binary()
    if check:
        typer.echo(f"dashboard: {script.parent}\nnode:      {node}")
        return
    typer.echo(f"Grove web dashboard on http://{host}:{port} (daemon {daemon_url})")
    os.execvpe(node, [node, str(script)], launcher.env(host, port, daemon_url))


def register(app: typer.Typer) -> None:
    """Graft ``grove web`` onto the top-level app, flat like the other verbs."""
    app.command("web")(serve_web)


__all__ = ["WebappLauncher", "bundled_node", "register"]
