"""Composite the header device mockups by seating screenshots inside real
device shells (MacBook Pro 14, iPhone 15) with ImageMagick.

Device shells are ONLY used for the top-level header on the README and the
landing page: a laptop that swaps between the terminal UI and the web UI every
three seconds (an animated GIF), and a phone showing the web landing page. Every
other doc/README image is a plain screenshot, never framed.

The shells live in ``tools/screenshots/device-shells/`` (committed PNGs from
mockuphone.com): the device body is opaque, the screen and the exterior are
transparent. We seat a screenshot BEHIND the shell, confined to the screen
rectangle, so the bezel/notch/rounded-corners overlay the screenshot edges
(``-compose DstOver``). No headless browser needed.

The screen rectangle of each shell was measured once by flood-filling the
exterior of the alpha mask and trimming to the remaining interior hole:

    convert SHELL -alpha extract -threshold 50% -bordercolor black -border 2 \\
      -fill red -draw 'color 0,0 floodfill' -shave 2x2 -fill white +opaque black \\
      -trim -format '%wx%h%X%Y'

Re-measure with that command if a shell is ever replaced.

Inputs (produced by the capture tools):
- ``docs/img/screenshots/tui-list.svg``            ┐ MacBook GIF →  hero-laptop.gif
- ``docs/img/screenshots/webapp-home-grid.png``    ┘ (3s swap, TUI ↔ web UI)
- ``docs/img/screenshots/webapp-home-mobile.png``    iPhone        →  webapp-phone-mockup.png

Run via:

    make docs-mockups

or directly:

    uv run python -m tools.screenshots.mockups

Requires ImageMagick (``convert`` / ``magick``) for the framing, and a
Chromium binary on PATH to rasterize the Textual SVG faithfully.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOTS = REPO_ROOT / "docs" / "img" / "screenshots"
OUT = REPO_ROOT / "docs" / "img" / "mockups"
SHELLS = Path(__file__).resolve().parent / "device-shells"


def _magick() -> list[str]:
    """ImageMagick invocation prefix (IM7 ``magick`` or IM6 ``convert``)."""
    if shutil.which("magick"):
        return ["magick"]
    if shutil.which("convert"):
        return ["convert"]
    raise RuntimeError("ImageMagick not found on PATH (need `magick` or `convert`)")


@dataclass(frozen=True, slots=True)
class Device:
    """A device shell plus the on-shell screen rectangle to seat a shot into."""

    shell: Path
    # Screen rectangle within the shell, in shell pixels.
    sx: int
    sy: int
    sw: int
    sh: int

    def frame(
        self,
        source: Path,
        out: Path,
        *,
        fit: str = "cover",
        width: int = 1280,
        inset: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> Path:
        """Seat ``source`` behind the shell, confined to the screen rectangle.

        ``fit='cover'`` fills the usable area and crops the overflow;
        ``fit='contain'`` fits the whole shot (bars stay black). ``inset`` is the
        ``(top, right, bottom, left)`` safe-area padding in shell pixels, filled
        black: a phone needs it so the app sits BELOW the Dynamic Island and home
        indicator (an app drawn edge-to-edge under the notch reads as "bleeding").
        ``width`` caps the output so the committed PNG/GIF stays lean.

        Built in two layers: a black screen canvas (so inset/letterbox areas are
        opaque, not see-through), the screenshot composited into the usable
        sub-rect, then that whole screen seated behind the shell with DstOver.

        NOTE: ``-gravity`` is reset to NorthWest before every ``-geometry`` —
        the ``center`` gravity used for the extent leaks past the parentheses and
        would otherwise offset the seat from center instead of the rect's
        top-left. ``+repage`` clears each extent's virtual canvas.
        """
        top, right, bottom, left = inset
        uw, uh = self.sw - left - right, self.sh - top - bottom  # usable sub-rect
        usable = f"{uw}x{uh}"
        screen = f"{self.sw}x{self.sh}"
        subprocess.run(
            [
                *_magick(),
                str(self.shell),
                # Build the screen content: black canvas + the fitted screenshot
                # placed inside the safe-area sub-rect.
                "(",
                "-size",
                screen,
                "xc:black",
                "(",
                str(source),
                "-background",
                "none",
                "-resize",
                f"{usable}^" if fit == "cover" else usable,
                "-gravity",
                "center",
                "-extent",
                usable,
                "+repage",
                ")",
                "-gravity",
                "NorthWest",
                "-geometry",
                f"+{left}+{top}",
                "-compose",
                "over",
                "-composite",
                "+repage",
                ")",
                # Seat the assembled screen behind the shell.
                "-gravity",
                "NorthWest",
                "-geometry",
                f"+{self.sx}+{self.sy}",
                "-compose",
                "DstOver",
                "-composite",
                "+repage",
                "-resize",
                f"{width}x>",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
        return out


MACBOOK = Device(SHELLS / "macbookpro14-front.png", sx=461, sy=300, sw=3022, sh=1964)
IPHONE = Device(SHELLS / "iphone-15-black-portrait.png", sx=120, sy=120, sw=1179, sh=2556)


def _chromium() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no Chromium binary found on PATH")


_SVG_HTML = (
    '<!doctype html><html><head><meta charset="utf-8">'
    "<style>html,body{{margin:0;background:transparent}}"
    "img{{display:block;width:{w}px;height:{h}px}}</style></head>"
    '<body><img src="{src}"></body></html>'
)


def _rasterize_svg(svg: Path) -> Path:
    """Rasterize a Textual SVG capture to a temp PNG via headless Chromium.

    ImageMagick's rsvg delegate renders Textual SVGs poorly (it drops the cell
    backgrounds, the peek rail, and the colors, leaving only stray titles), so
    the faithful path is Chromium, which has full SVG + embedded-font support.
    """
    identify = ["magick", "identify"] if shutil.which("magick") else ["identify"]
    w, h = subprocess.run(
        [*identify, "-format", "%w %h", str(svg)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    work = Path(tempfile.mkdtemp())
    page = work / "page.html"
    src = f"data:image/svg+xml;base64,{base64.b64encode(svg.read_bytes()).decode('ascii')}"
    page.write_text(_SVG_HTML.format(w=w, h=h, src=src))
    png = work / f"{svg.stem}.png"
    subprocess.run(
        [
            _chromium(),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            "--default-background-color=00000000",
            f"--window-size={w},{h}",
            f"--screenshot={png}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return png


def _laptop_swap_gif(frames: list[Path], out: Path, *, seconds: int = 3) -> None:
    """Assemble same-sized laptop frames into a looping GIF, ``seconds`` per frame."""
    subprocess.run(
        [
            *_magick(),
            "-loop",
            "0",
            "-delay",
            str(seconds * 100),  # centiseconds
            *(str(f) for f in frames),
            "-layers",
            "optimize",
            str(out),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    tui_svg = SHOTS / "tui-list.svg"
    home_grid = SHOTS / "webapp-home-grid.png"
    home_mobile = SHOTS / "webapp-home-mobile.png"

    inputs = [tui_svg, home_grid, home_mobile, MACBOOK.shell, IPHONE.shell]
    missing = [p.name for p in inputs if not p.exists()]
    if missing:
        print(f"missing inputs: {missing}; run the capture tools first", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp())
    # Header laptop: a 3s swap between the terminal UI and the web UI, both
    # seated edge-to-edge in the MacBook screen (cover, so each fills it).
    tui_frame = MACBOOK.frame(_rasterize_svg(tui_svg), tmp / "laptop-tui.png", fit="cover")
    web_frame = MACBOOK.frame(home_grid, tmp / "laptop-web.png", fit="cover")
    _laptop_swap_gif([tui_frame, web_frame], OUT / "hero-laptop.gif")

    # Header phone: the web landing page, inset into the iPhone safe area so the
    # app sits below the Dynamic Island and home indicator (top/bottom black
    # bands), not edge-to-edge under the notch.
    IPHONE.frame(
        home_mobile,
        OUT / "webapp-phone-mockup.png",
        fit="contain",
        width=900,
        inset=(150, 0, 90, 0),
    )

    print(f"wrote header device mockups to {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
