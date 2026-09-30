"""Bundle the landing page's factory scene into one minified module.

The readable scene lives in ``docs/javascripts/factory/`` plus its entry
``grove-factory.js``, and imports the vendored, unminified Three.js build.
Served as is, that is ~2.2 MB of JavaScript over about a dozen module
requests, most of it renderer code the scene never calls. esbuild tree-shakes
and minifies the same graph into one file of ~630 KB (~160 KB gzipped).

The bundle is committed, because the docs build has no JavaScript toolchain.
Its first line records a digest of every input file, so a test can prove it
is current without running Node: edit a source file, forget to rebuild, and
``test_landing_bundle_is_built_from_the_current_sources`` fails.

Run from the repository root after changing the scene or the vendored runtime:

    uv run --group dev python -m tools.landing_bundle

It uses the esbuild the webapp already installs (``npm ci`` in ``webapp/``).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Final

SCRIPTS: Final = Path("docs/javascripts")
ENTRY: Final = SCRIPTS / "grove-factory.js"
OUTPUT: Final = SCRIPTS / "grove-factory.min.js"
# Every file the bundle can be built from. The vendored runtime is included,
# because refreshing Three.js changes the bundle just as a scene edit does.
INPUTS: Final = ("grove-factory.js", "factory/*.js", "vendor/**/*.js")
HEADER: Final = "/* grove-factory bundle; sources sha256:"


class LandingBundle:
    """Build the minified scene bundle and describe what it was built from."""

    @staticmethod
    def sources(root: Path = Path()) -> list[Path]:
        scripts = root / SCRIPTS
        found = {path for pattern in INPUTS for path in scripts.glob(pattern)}
        return sorted(found, key=lambda path: path.relative_to(scripts).as_posix())

    @classmethod
    def digest(cls, root: Path = Path()) -> str:
        """Hash of every input's path and bytes, stable across checkouts."""
        scripts = root / SCRIPTS
        hasher = hashlib.sha256()
        for path in cls.sources(root):
            hasher.update(path.relative_to(scripts).as_posix().encode())
            hasher.update(b"\0")
            # Normalise line endings so a Windows checkout agrees with Linux.
            hasher.update(path.read_bytes().replace(b"\r\n", b"\n"))
            hasher.update(b"\0")
        return hasher.hexdigest()

    @staticmethod
    def recorded_digest(root: Path = Path()) -> str | None:
        """The digest the committed bundle claims, or None if it has no header."""
        first = (root / OUTPUT).read_text().split("\n", 1)[0]
        if not first.startswith(HEADER):
            return None
        return first.removeprefix(HEADER).removesuffix("*/").strip()

    @staticmethod
    def esbuild(root: Path = Path()) -> list[str]:
        local = root / "webapp" / "node_modules" / ".bin" / "esbuild"
        if local.exists():
            return [str(local)]
        found = shutil.which("esbuild")
        if found:
            return [found]
        raise SystemExit("esbuild not found: run `npm ci` in webapp/ first")

    @classmethod
    def build(cls, root: Path = Path()) -> Path:
        output = root / OUTPUT
        subprocess.run(
            [
                *cls.esbuild(root),
                str(root / ENTRY),
                "--bundle",
                "--minify",
                "--format=esm",
                # import.meta.url still resolves against the bundle, which
                # sits beside the entry, so asset URLs are unchanged.
                "--target=es2022",
                "--legal-comments=eof",
                f"--outfile={output}",
                "--log-level=warning",
            ],
            check=True,
        )
        body = output.read_text()
        output.write_text(f"{HEADER} {cls.digest(root)} */\n{body}")
        return output


def main() -> None:
    output = LandingBundle.build()
    print(f"wrote {output} ({output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
