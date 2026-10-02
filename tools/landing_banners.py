"""Render the GitHub banners from the landing page.

Two images in ``docs/img/banners/``, both the real landing page with its chrome
hidden, so a redesign of the page is a re-run here rather than a hand edit:

* ``github-social.png``: the 1280x640 social preview (GitHub's required size;
  set it under the repository's settings, "Social preview").
* ``readme-banner.png``: the README's header strip, replacing the logo, the
  title and the row of supported-tool marks.

The compositions live in ``landing_banners.mjs``. This module builds the docs
site into a scratch directory, patches the scene's entry so the canvas exposes
its scene (the shipped bundle exposes nothing), serves it on loopback, and runs
the browser against it. Run from the repository root:

    uv run --group docs python -m tools.landing_banners

It needs ``node``, the webapp's Playwright install (``npm ci`` in ``webapp/``)
and esbuild (the same one ``tools.landing_bundle`` uses).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Final, Iterator

from PIL import Image

from tools.landing_bundle import LandingBundle

ROOT: Final = Path(__file__).resolve().parent.parent
OUT: Final = ROOT / "docs" / "img" / "banners"
SCRIPT: Final = Path(__file__).with_name("landing_banners.mjs")
#: The README strip is shot at 2x of 1600x400 for crisp edges, then brought to
#: this width: still 1.5x on a README column, at under half the weight.
README_WIDTH: Final = 2400
#: The entry module's construction line, and the same line keeping the scene.
ENTRY_LINE: Final = "  new FactoryScene(scene,"
EXPOSED_LINE: Final = '  scene.querySelector("canvas").__scene = new FactoryScene(scene,'


def build_site(scratch: Path) -> Path:
    """Build the docs into ``scratch`` with a scene bundle that exposes itself."""
    site = scratch / "site"
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "-q", "-d", str(site)],
        check=True,
        cwd=ROOT,
    )
    sources = scratch / "javascripts"
    shutil.copytree(ROOT / "docs" / "javascripts", sources)
    entry = sources / "grove-factory.js"
    patched = entry.read_text().replace(ENTRY_LINE, EXPOSED_LINE, 1)
    if patched == entry.read_text():
        raise SystemExit(f"{entry} no longer constructs the scene on the line this tool patches")
    entry.write_text(patched)
    subprocess.run(
        [
            *LandingBundle.esbuild(ROOT),
            str(entry),
            "--bundle",
            "--minify",
            "--format=esm",
            "--target=es2022",
            "--log-level=error",
            f"--outfile={site / 'javascripts' / 'grove-factory.min.js'}",
        ],
        check=True,
    )
    return site


@contextmanager
def serve(site: Path) -> Iterator[str]:
    """Serve ``site`` on a loopback port chosen by the OS."""

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *a, **k: Quiet(*a, directory=str(site), **k)
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node not found on PATH")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grove-banners-") as scratch:
        site = build_site(Path(scratch))
        with serve(site) as url:
            subprocess.run(
                [node, str(SCRIPT)],
                check=True,
                cwd=ROOT,
                env={**os.environ, "SITE": url, "OUT": str(OUT)},
            )
    strip = OUT / "readme-banner.png"
    with Image.open(strip) as shot:
        scale = README_WIDTH / shot.width
        resized = shot.convert("RGB").resize(
            (README_WIDTH, round(shot.height * scale)), Image.Resampling.LANCZOS
        )
    resized.save(strip, optimize=True)


if __name__ == "__main__":
    main()
