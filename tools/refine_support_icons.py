"""Apply the two approved finishing operations to revised support tiles.

This is deliberately NOT a renderer and it never replaces a background. The
input files are the artwork of record. It may only:

1. regularize the outer alpha edge on Codex, Linear and Gitea while preserving
   every RGB pixel inside that edge; and
2. enlarge the clay Claude Code mark against the existing dark shell.

Run from the repository root after revising the three source tiles:

    uv run --group dev python -m tools.refine_support_icons
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ICON_DIR: Final[Path] = REPO_ROOT / "docs" / "logos" / "support"
CANVAS: Final[int] = 192
CORNER_RADIUS: Final[int] = 42
EDGE_SUPERSAMPLE: Final[int] = 4
CLAUDE_ZOOM: Final[float] = 1.12


def _rounded_mask() -> Image.Image:
    """One clean anti-aliased edge at the tile's existing size and radius."""
    scale = EDGE_SUPERSAMPLE
    mask = Image.new("L", (CANVAS * scale, CANVAS * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, CANVAS * scale - 1, CANVAS * scale - 1),
        radius=CORNER_RADIUS * scale,
        fill=255,
    )
    return mask.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def clean_edge(path: Path) -> None:
    """Replace only `path`'s outer alpha edge; leave every RGB pixel intact."""
    with Image.open(path) as handle:
        image = handle.convert("RGBA")
    image.putalpha(_rounded_mask())
    image.save(path, optimize=True)


def zoom_claude(path: Path) -> None:
    """Enlarge only Claude Code's clay mark over its unchanged dark tile."""
    with Image.open(path) as handle:
        image = handle.convert("RGBA")

    # The LobeHub avatar is a two-color composition: a clay mark over a nearly
    # black shell. Select the brand color by warmth instead of by coordinates so
    # anti-aliased edge pixels come along with the mark.
    alpha = Image.new("L", image.size, 0)
    mask = alpha.load()
    pixels = image.load()
    for y in range(CANVAS):
        for x in range(CANVAS):
            red, green, blue, source_alpha = pixels[x, y]
            if source_alpha > 0 and red > 90 and red > green * 1.28 and green > blue * 1.08:
                mask[x, y] = source_alpha

    box = alpha.getbbox()
    if box is None:
        raise ValueError("Claude Code mark not found")
    mark = image.crop(box)
    mark.putalpha(alpha.crop(box))
    zoomed = mark.resize(
        (round(mark.width * CLAUDE_ZOOM), round(mark.height * CLAUDE_ZOOM)),
        Image.Resampling.LANCZOS,
    )

    # Remove the old mark by filling exactly its alpha with a sample of the
    # existing shell at the same row. The shell is a near-black vertical
    # gradient, so row-wise samples preserve it rather than inventing a new one.
    clean = image.copy()
    clean_pixels = clean.load()
    for y in range(box[1], box[3]):
        shell = image.getpixel((12, y))
        for x in range(box[0], box[2]):
            if alpha.getpixel((x, y)):
                clean_pixels[x, y] = shell

    x = (CANVAS - zoomed.width) // 2
    y = (CANVAS - zoomed.height) // 2
    clean.alpha_composite(zoomed, (x, y))
    clean.save(path, optimize=True)


def main() -> None:
    """Apply only the approved edge and Claude zoom operations."""
    zoom_claude(ICON_DIR / "claude-code.png")
    for name in ("codex", "linear", "gitea"):
        clean_edge(ICON_DIR / f"{name}.png")


if __name__ == "__main__":
    main()
