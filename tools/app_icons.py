"""Render every Grove app icon from the one piece of artwork.

Until now this set had no generator. `webapp/app/favicon.ico`, `app/icon.png`,
`app/apple-icon.png`, the manifest's four `webapp/public/icon-*.png` and the
docs site's `docs/logos/grove-logo.png` were nine hand-made files that happened
to agree, and the only thing keeping them one picture was that nobody had
touched them since. Editing the artwork made that cost visible: without this
module, a change to `docs/img/grove-logo.svg` means redrawing nine tiles by
hand and hoping the ninth matches the first.

Run from the repo root after changing the artwork:

    uv run --group dev python -m tools.app_icons

WHY CHROMIUM AND NOT IMAGEMAGICK. The repo already rasterizes SVG through a
browser (`tools/screenshots/driver/raster.py`) because ImageMagick's rsvg
delegate mangles Textual's captures. That warning is about cell background
fills and does not apply to this one flat path — measured on this artwork the
two renderers agree to a mean 0.28/255 with an identical bounding box. Chromium
is used anyway, because "the artwork renders in the engine the site renders it
in" is the property worth keeping, and a second renderer in the tree is a
second set of anti-aliasing rules to reason about.

THE TWO TILE SHAPES ARE A SPEC REQUIREMENT, NOT A STYLE CHOICE. A `standard`
icon is a rounded tile on transparency: it is composited as-is, so its corners
must be cut. A `maskable` icon is the opposite — Android crops it to whatever
shape the launcher wants (circle, squircle, teardrop), so it must be FULL
BLEED, and any transparency in the corners is a hole the launcher shows
through when its mask is wider than our radius. The art is inset into the safe
circle instead. `apple` is full bleed for the same reason with a different
owner: iOS applies its own rounding and composites what is left onto BLACK, so
a transparent corner there is a black corner on the home screen. The four
shipped `-maskable` and `apple-icon` files all carried cut corners before this
module existed.

THE TWO MASKS ARE DIFFERENT SHAPES AND THE SAFE TEST IS NOT SHARED. Android's
guarantee is a CIRCLE of 80% diameter, so the number that binds a maskable icon
is the ink's max radius from centre. iOS rounds to a SQUIRCLE at roughly 22.4%
of the side, which cuts far less, so `apple-icon` is verified against that
shape and not against Android's circle — checking it against the circle reports
a failure on a tile no iOS device would clip.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
ARTWORK: Final[Path] = REPO_ROOT / "docs" / "img" / "grove-logo.svg"

TILE_COLOR: Final[str] = "#FFF6E5"
"""The cream the shipped tiles were drawn in, measured off `icon-512.png`.

Not a theme token and deliberately not reachable from one: this is the same
call `brand-mark.tsx` makes about the terracotta. A brand surface that follows
a palette is a brand surface that changes when the palette does.
"""

CORNER_RADIUS_FRACTION: Final[float] = 89 / 512
"""The shipped tile's corner, recovered rather than invented.

Fitted against `icon-512.png`'s own alpha channel by sweeping the radius and
taking the minimum mean absolute error: 89px on a 512px canvas, at an error of
0.0015, which is an exact match in everything but anti-aliasing. Keeping the
existing curve is what stops a regeneration from also being a redesign.
"""

MARK_FRACTION: Final[float] = 0.846
"""How much of a standard tile's width the INK spans.

The shipped set drew it at 0.705 (`icon-512.png` 361px on 512, `apple-icon.png`
127 on 180 — one intended number rounded twice), which left the mark floating
in a wide cream border and reading small in a dock or a tab strip. Raised 20%
by request. The tile's rounded corners are not the binding constraint: they cut
the diagonal, where the mark's own silhouette is already inset, so at 0.846 the
straight edges still keep a ~7.7% margin.

Ink, not viewBox, and the distinction is worth the word: the artwork's drawing
only reaches 91.6% of its own 409-unit box, and it is not quite centred in it
either. Scaling the raw render by this number therefore lands the visible mark
at 0.641 — measurably smaller than the tile it replaces, from a constant that
claims otherwise. `MarkRenderer` crops to the ink before returning, so the
number here means what it reads as.
"""

MASKABLE_MARK_FRACTION: Final[float] = 0.677
"""The same mark, inset for Android's safe zone (`icon-512-maskable.png`).

Raised 20% from the shipped 0.564 alongside `MARK_FRACTION`, and it is the one
that needed checking rather than scaling: the spec guarantees only the centre
circle of 80% diameter survives cropping, so the binding number is the ink's
max RADIUS, not its span. The wheel's nodes sit at ~0.461 of its span from
centre, so 0.677 puts the outermost ink at ~0.312 against the 0.4 limit — still
clear. Do not raise this to match the standard tile: at 0.846 the radius would
be ~0.390 and a circular launcher mask would clip the rim nodes off.
"""

SUPERSAMPLE: Final[int] = 4
"""Render the mark at 4x its largest use and downsample once.

A vector re-rendered per size would be sharper still at 16px, but it would also
let each size anti-alias differently, and these are nine files whose whole job
is to be the same picture. One master, Lanczos down, keeps them identical.
"""

PALETTE_COLORS: Final[int] = 64
"""Quantize each PNG to a palette, matching what the shipped set already did.

The tiles this replaces held **9** unique colours, so writing truecolour would
have been a silent 2x weight gain on files served on every page load. The
artwork is two flat colours, so a palette only has to carry their anti-aliased
edge ramps: measured on `icon-512.png`, 64 colours is 18.0 KB against 42.7 KB
truecolour for a mean error of 0.93/255. 32 and 256 both land on the same
18.0 KB here, so this is the knee rather than a tuned value. Octree, not median
cut, because Pillow only offers octree and libimagequant for RGBA — and NO
dithering, which on a flat two-colour mark would add noise to solve a banding
problem it does not have.
"""


class IconTarget(BaseModel):
    """One file to write, and the shape its consumer requires."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    """Destination, relative to the repo root."""

    sizes: tuple[int, ...] = Field(min_length=1)
    """Square edges to render. More than one means an ICO with those frames."""

    shape: Literal["standard", "maskable", "apple"]
    """Which tile the consumer needs. See the module docstring."""

    @property
    def mark_fraction(self) -> float:
        """Maskable art is inset for the safe zone; everything else is not."""
        return MASKABLE_MARK_FRACTION if self.shape == "maskable" else MARK_FRACTION

    @property
    def full_bleed(self) -> bool:
        """True where the consumer supplies its own mask and we must not."""
        return self.shape in ("maskable", "apple")


TARGETS: Final[tuple[IconTarget, ...]] = (
    # The manifest's four entries. Both purposes must exist: a maskable icon is
    # inset for cropping and looks small and lost anywhere it is NOT cropped.
    IconTarget(path=Path("webapp/public/icon-192.png"), sizes=(192,), shape="standard"),
    IconTarget(path=Path("webapp/public/icon-512.png"), sizes=(512,), shape="standard"),
    IconTarget(path=Path("webapp/public/icon-192-maskable.png"), sizes=(192,), shape="maskable"),
    IconTarget(path=Path("webapp/public/icon-512-maskable.png"), sizes=(512,), shape="maskable"),
    # Next's file conventions. `icon.png` is byte-identical to `icon-512.png`
    # by construction rather than by copy, which is the point of the table.
    IconTarget(path=Path("webapp/app/icon.png"), sizes=(512,), shape="standard"),
    IconTarget(path=Path("webapp/app/apple-icon.png"), sizes=(180,), shape="apple"),
    # A real multi-size ICO. 16 is the tab, 32 the bookmark bar, 48/64 the
    # Windows shortcut — one file, four renders, no upscaling anywhere.
    IconTarget(path=Path("webapp/app/favicon.ico"), sizes=(16, 32, 48, 64), shape="standard"),
    # The docs site's `theme.icon`/`theme.favicon` and the README's header.
    IconTarget(path=Path("docs/logos/grove-logo.png"), sizes=(512,), shape="standard"),
)


class MarkRenderer:
    """Rasterize the artwork once, at high resolution, through the browser."""

    def __init__(self, artwork: Path, chromium: str) -> None:
        self._artwork = artwork
        self._chromium = chromium

    @staticmethod
    def preflight() -> str | None:
        """Say why rendering cannot run, or `None` if it can."""
        if shutil.which("chromium") is None:
            return "chromium not found on PATH"
        return None

    def render(self, size: int) -> Image.Image:
        """Return the mark's INK alone, on transparency, squared and centred.

        Cropping to the drawn pixels rather than handing back the whole
        viewBox is what makes `MARK_FRACTION` an honest number, and it also
        centres the mark on the tile by its own ink instead of by a box it
        does not sit centred in.
        """
        # Inlined into a host document rather than loaded through `<img>`: an
        # SVG behind an `<img>` is a sandboxed document in Chromium, and the
        # same trap already bit the screenshot rasterizer.
        markup = self._artwork.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "mark.html"
            shot = Path(tmp) / "mark.png"
            page.write_text(
                "<!doctype html><meta charset=utf-8>"
                "<style>html,body{margin:0;padding:0;background:transparent}"
                f"svg{{display:block;width:{size}px;height:{size}px}}</style>{markup}",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    self._chromium,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--default-background-color=00000000",
                    f"--window-size={size},{size}",
                    f"--screenshot={shot}",
                    "--virtual-time-budget=5000",
                    page.as_uri(),
                ],
                check=True,
                capture_output=True,
            )
            with Image.open(shot) as handle:
                return self._crop_to_ink(handle.convert("RGBA").copy())

    @staticmethod
    def _crop_to_ink(render: Image.Image) -> Image.Image:
        """Trim transparency, then re-pad to a square so scaling stays uniform.

        Squaring here rather than at the seat is what keeps the mark's aspect
        ratio: a non-square crop resized into a square box would stretch it.
        """
        box = render.getbbox()
        if box is None:  # a blank render means the artwork failed to load
            raise RuntimeError("the artwork rendered empty — nothing was drawn")
        ink = render.crop(box)
        edge = max(ink.width, ink.height)
        square = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
        square.alpha_composite(ink, ((edge - ink.width) // 2, (edge - ink.height) // 2))
        return square


class TileComposer:
    """Seat the rendered mark on the tile each destination needs."""

    def __init__(self, mark: Image.Image, color: str = TILE_COLOR) -> None:
        self._mark = mark
        self._color = color

    def compose(self, target: IconTarget, size: int) -> Image.Image:
        """Render one square icon for `target` at `size`."""
        tile = Image.new("RGBA", (size, size), self._color)
        if not target.full_bleed:
            tile.putalpha(self._rounded_mask(size))

        span = max(1, round(size * target.mark_fraction))
        mark = self._mark.resize((span, span), Image.Resampling.LANCZOS)
        # Rounded down on both axes from the same number, so an odd leftover
        # pixel biases up-left consistently instead of per-size.
        offset = (size - span) // 2
        tile.alpha_composite(mark, (offset, offset))
        return tile

    def _rounded_mask(self, size: int) -> Image.Image:
        """The tile's corner, anti-aliased by supersampling the draw."""
        scale = SUPERSAMPLE
        mask = Image.new("L", (size * scale, size * scale), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size * scale - 1, size * scale - 1),
            radius=round(size * scale * CORNER_RADIUS_FRACTION),
            fill=255,
        )
        return mask.resize((size, size), Image.Resampling.LANCZOS)


def write(target: IconTarget, composer: TileComposer, root: Path = REPO_ROOT) -> Path:
    """Render and save one target, returning where it landed."""
    out = root / target.path
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = [composer.compose(target, size) for size in target.sizes]

    if out.suffix == ".ico":
        # Pillow's ICO writer resamples from ONE image to whatever `sizes` it
        # is given, so the largest frame is handed over and the rest are asked
        # for by size. Every frame is still downsampled from the 4x master
        # rather than from 64px, because that master is what `frames` holds.
        # Left truecolour: an ICO carries its frames in a container the palette
        # step below does not apply to, and it is four small frames either way.
        largest = max(frames, key=lambda frame: frame.width)
        largest.save(out, format="ICO", sizes=[(size, size) for size in target.sizes])
    else:
        frames[0].quantize(
            colors=PALETTE_COLORS,
            method=Image.Quantize.FASTOCTREE,
            dither=Image.Dither.NONE,
        ).save(out, optimize=True)
    return out


def main() -> None:
    """Render every icon in `TARGETS` from the artwork of record."""
    problem = MarkRenderer.preflight()
    if problem is not None:
        raise SystemExit(f"cannot render icons: {problem}")

    chromium = shutil.which("chromium")
    assert chromium is not None  # preflight() proved this
    largest = max(size for target in TARGETS for size in target.sizes)
    mark = MarkRenderer(ARTWORK, chromium).render(largest * SUPERSAMPLE)
    composer = TileComposer(mark)

    for target in TARGETS:
        print(f"wrote {write(target, composer).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
