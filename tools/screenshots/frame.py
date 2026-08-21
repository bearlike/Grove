"""The window-on-backdrop composite: pure geometry and pure pixels.

Nothing here touches the filesystem beyond one explicit file per CLI
invocation. `FrameStyle` turns canvas dimensions into placement numbers;
`WindowFramer` turns those numbers plus one source image into one framed
image: a rounded window, softly shadowed, centred on a generated backdrop.
Both are injectable, so a test can assert geometry without decoding a PNG.

Ported from a working reference (bearlike/Assistant's demo framer). Three
backdrop kinds are offered as overridable fields rather than baked into the
algorithm: a cover-fitted photograph (`ImageBackdrop`, the default and the
one the published shots use), a flat colour (`SolidBackdrop`), and a
two-colour gradient (`GradientBackdrop`). The two generated kinds exist
because a backdrop is only ever visible as a thin ring around the window,
and a run that has no wallpaper to hand should still produce a frame rather
than fail.

Two capture-side conventions this module assumes but does not enforce,
recorded here for whoever wires the Playwright driver that produces the
source PNGs: the page should freeze the system clock and disable CSS
transitions, scrollbar rendering and the text caret before each shot, and
the driver's own acceptance check should confirm two captures of the same
page are byte-identical. Neither belongs in a pure compositor — they are
capture-time concerns, not framing-time ones.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Annotated, Final, Literal

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Pillow >= 10 moved the resampling filters onto an enum; the module-level
# aliases are deprecated and this package is new enough to skip them.
_LANCZOS = Image.Resampling.LANCZOS

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

# A gradient backdrop is generated at this resolution (longest side, in
# pixels) and upscaled to the real canvas size. The backdrop is only ever
# visible in the thin padding ring around the framed window, so a smooth
# low-resolution gradient upscaled with Lanczos is indistinguishable from
# one computed at full size and orders of magnitude cheaper: the radial
# variant is O(w*h) in pure Python, and 2400x1350 done directly is slow
# enough to matter across a batch of screenshots.
_GRADIENT_LOW_RES_MAX = 220

DEFAULT_BACKDROP_IMAGE = Path(__file__).resolve().parent / "assets" / "sand-dunes.jpg"
"""The wallpaper every published screenshot sits on.

Resolved off this module's own path rather than the working directory, because
the framer is invoked from a Makefile target, from a test, and by hand, and
only one of those three reliably runs at the repo root.
"""


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Decode a validated `#rrggbb` string to an 8-bit RGB triple."""
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _lerp_rgb(
    start: tuple[int, int, int], end: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    """Linear-interpolate two RGB triples at `t` in [0, 1]."""
    return (
        round(start[0] + (end[0] - start[0]) * t),
        round(start[1] + (end[1] - start[1]) * t),
        round(start[2] + (end[2] - start[2]) * t),
    )


class SolidBackdrop(BaseModel):
    """Fill the canvas with one flat colour.

    For a source that is an element crop rather than a window: a card lifted
    out of the app reads as a card, so it belongs on the surface it was
    lifted off, not on a backdrop it never sat on.

    Cost class: O(canvas area), paid once per canvas by `WindowFramer.build`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["solid"] = "solid"

    color: str = Field(pattern=_HEX_COLOR)
    """The matte, as `#rrggbb`. Validated at definition, because a malformed
    colour must be a `ValidationError` naming the field rather than a
    `ValueError` from deep inside Pillow halfway through a render."""

    def prepare(self, size: tuple[int, int]) -> Image.Image:
        """Fill `size` with `color`."""
        return Image.new("RGB", size, _hex_to_rgb(self.color))


class GradientBackdrop(BaseModel):
    """Fill the canvas with a smooth gradient between two colours.

    Default is Grove's own dark palette, not a third-party asset: `start`
    is the exact hard-coded fill of `BrandMark`
    (`webapp/components/grove/brand-mark.tsx`) — Grove's brand clay, the one
    colour in the app that is deliberately not a theme token. `end` is
    `--surface-sunken` in dark mode (`webapp/app/globals.css`,
    `oklch(0.1 0.005 286)`), the darkest rung of the web app's own surface
    ladder — converted to sRGB hex since Pillow has no OKLCH support.
    Direction defaults to `radial`, clay at the centre fading to that
    near-black at the edge: the framed window covers most of the canvas, so
    only a thin ring near the edges ever shows, and at that distance the
    gradient has already fallen almost all the way to near-black — a calm,
    on-brand backdrop rather than a loud one, with the clay only readable as
    a soft glow right behind the window's own shadow.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["gradient"] = "gradient"

    start: str = Field(default="#c86e45", pattern=_HEX_COLOR)
    end: str = Field(default="#030304", pattern=_HEX_COLOR)
    direction: Literal["linear", "radial"] = "radial"

    def prepare(self, size: tuple[int, int]) -> Image.Image:
        """Render the gradient at `size`."""
        if self.direction == "radial":
            return self._radial(size)
        return self._linear(size)

    def _linear(self, size: tuple[int, int]) -> Image.Image:
        """A vertical gradient: `start` at the top, `end` at the bottom.

        O(height): built as a single pixel-wide column, then stretched to
        the full width, so this needs no low-resolution shortcut.
        """
        _width, height = size
        start_rgb = _hex_to_rgb(self.start)
        end_rgb = _hex_to_rgb(self.end)
        column = Image.new("RGB", (1, height))
        pixels = column.load()
        denom = max(1, height - 1)
        for y in range(height):
            pixels[0, y] = _lerp_rgb(start_rgb, end_rgb, y / denom)
        return column.resize(size, _LANCZOS)

    def _radial(self, size: tuple[int, int]) -> Image.Image:
        """A radial gradient: `start` at the centre, `end` at the corners.

        Generated at `_GRADIENT_LOW_RES_MAX` and upscaled — see that
        constant's comment for why full-resolution generation is skipped.
        """
        width, height = size
        scale = min(1.0, _GRADIENT_LOW_RES_MAX / max(width, height))
        low_w = max(2, round(width * scale))
        low_h = max(2, round(height * scale))
        start_rgb = _hex_to_rgb(self.start)
        end_rgb = _hex_to_rgb(self.end)
        cx, cy = (low_w - 1) / 2, (low_h - 1) / 2
        max_dist = math.hypot(cx, cy) or 1.0
        low = Image.new("RGB", (low_w, low_h))
        pixels = low.load()
        for y in range(low_h):
            for x in range(low_w):
                t = min(1.0, math.hypot(x - cx, y - cy) / max_dist)
                pixels[x, y] = _lerp_rgb(start_rgb, end_rgb, t)
        return low.resize(size, _LANCZOS)


class ImageBackdrop(BaseModel):
    """Cover-fit a photograph onto the canvas.

    Cover rather than contain: a backdrop that letterboxes is not a backdrop.
    The image is scaled by whichever axis needs the larger factor and the
    overflow is cropped away symmetrically, so the canvas is always fully
    painted and the subject stays centred whatever the source aspect ratio.

    Cost class: O(source area) for the decode plus O(canvas area) for the
    resample, paid once per canvas by `WindowFramer.build` — never per file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["image"] = "image"

    path: Path = DEFAULT_BACKDROP_IMAGE

    def prepare(self, size: tuple[int, int]) -> Image.Image:
        """Decode `path` and cover-fit it to `size`."""
        with Image.open(self.path) as handle:
            source = handle.convert("RGB")
        width, height = size
        src_w, src_h = source.size
        scale = max(width / src_w, height / src_h)
        # Clamped upward so a rounding-down on either axis can never leave a
        # one-pixel unpainted seam at the canvas edge.
        scaled = source.resize(
            (max(width, round(src_w * scale)), max(height, round(src_h * scale))),
            _LANCZOS,
        )
        left = (scaled.width - width) // 2
        top = (scaled.height - height) // 2
        return scaled.crop((left, top, left + width, top + height))


Backdrop = Annotated[
    ImageBackdrop | SolidBackdrop | GradientBackdrop,
    Field(discriminator="kind"),
]


class FrameStyle(BaseModel):
    """Every visual parameter of the frame, plus the geometry it implies.

    Ratios rather than pixels throughout, so one style renders identically at
    any canvas size. The padding, radius and shadow numbers are carried over
    from the reference implementation's own measured values, not re-derived:
    a window inset about 5.1% vertically and centred, a small corner radius,
    and a large soft shadow displaced slightly downward. The canvas is the
    one default that was re-measured against this theme — see `canvas_width`.

    Cost class: O(1). Every method is arithmetic over the canvas dimensions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    canvas_width: int = Field(default=1920, ge=16)
    canvas_height: int = Field(default=1080, ge=9)
    """Full HD, 16:9. Every capture feeding this is shot at twice its published
    size (a 1600x900 viewport at `deviceScaleFactor: 2`, a 1629x928 terminal
    rasterized at 2x), so the window is always DOWNsampled into the frame, which
    is the direction that stays sharp. The canvas was 2400x1350 and that bought
    nothing a reader could see: the theme caps content at 960px, and the extra
    56% of area cost roughly a megabyte per screenshot."""

    backdrop: Backdrop = ImageBackdrop()
    """What fills the canvas behind the window. The committed wallpaper by
    default; the generated kinds are there for a caller with none."""

    padding_y_ratio: float = Field(default=0.051, ge=0.0, lt=0.5)
    """Vertical breathing room, as a fraction of canvas height."""

    padding_x_min_ratio: float = Field(default=0.060, ge=0.0, lt=0.5)
    """Horizontal floor. It only binds for a source wider than the padded box;
    for anything narrower the side margin falls out of the contain fit."""

    radius_ratio: float = Field(default=0.0104, ge=0.0, le=0.25)
    shadow_blur_ratio: float = Field(default=0.0292, ge=0.0, le=0.25)
    shadow_spread_ratio: float = Field(default=0.0042, ge=0.0, le=0.25)
    shadow_offset_y_ratio: float = Field(default=0.0100, ge=-0.25, le=0.25)
    shadow_opacity: float = Field(default=0.55, ge=0.0, le=1.0)

    ambient_blur_scale: float = Field(default=0.5, ge=0.0, le=2.0)
    """A second, tighter shadow at zero offset. One cast shadow alone reads as
    a sticker hovering over the backdrop; the contact layer seats it."""

    ambient_opacity_scale: float = Field(default=0.35, ge=0.0, le=2.0)

    supersample: int = Field(default=4, ge=1, le=8)
    """Corner masks are drawn at this multiple and downsampled, because
    Pillow's `rounded_rectangle` is aliased and a corner staircase is the most
    visible tell that a composite was not done by a browser."""

    @model_validator(mode="after")
    def _padding_must_leave_a_content_box(self) -> FrameStyle:
        """Reject a style whose padding consumes the whole canvas."""
        box_w, box_h = self.content_box()
        if box_w < 1 or box_h < 1:
            raise ValueError(
                f"padding leaves no content box: {box_w}x{box_h} on a "
                f"{self.canvas_width}x{self.canvas_height} canvas"
            )
        return self

    @property
    def canvas_size(self) -> tuple[int, int]:
        """The output raster size."""
        return self.canvas_width, self.canvas_height

    def content_box(self) -> tuple[int, int]:
        """The box a source image is contained within, in pixels."""
        pad_x = round(self.canvas_width * self.padding_x_min_ratio)
        pad_y = round(self.canvas_height * self.padding_y_ratio)
        return self.canvas_width - 2 * pad_x, self.canvas_height - 2 * pad_y

    def place(self, source_size: tuple[int, int]) -> tuple[int, int, int, int]:
        """Contain `source_size` in the content box and centre it.

        Returns `(x, y, width, height)`. Aspect ratio is always preserved: a
        source is never cropped and never stretched, so a portrait capture
        simply shows more backdrop at its sides than a landscape one does.
        """
        box_w, box_h = self.content_box()
        src_w, src_h = source_size
        scale = min(box_w / src_w, box_h / src_h)
        width = max(1, round(src_w * scale))
        height = max(1, round(src_h * scale))
        return (
            (self.canvas_width - width) // 2,
            (self.canvas_height - height) // 2,
            width,
            height,
        )

    def radius_px(self) -> int:
        """Corner radius of the composited window."""
        return round(self.canvas_width * self.radius_ratio)

    def shadow_blur_px(self) -> float:
        """Gaussian sigma of the cast shadow."""
        return self.canvas_width * self.shadow_blur_ratio

    def shadow_spread_px(self) -> int:
        """How far the shadow silhouette grows beyond the window itself."""
        return round(self.canvas_width * self.shadow_spread_ratio)

    def shadow_offset_y_px(self) -> int:
        """Downward displacement of the cast shadow."""
        return round(self.canvas_height * self.shadow_offset_y_ratio)


class WindowFramer(BaseModel):
    """Composites one source image onto one backdrop, per a `FrameStyle`.

    Collaborators are injected: the style and the already prepared backdrop
    are fields, so framing many artifacts reuses one prepared background
    instead of re-generating it per file.

    Cost class: O(canvas area) per `frame` call, independent of how many
    artifacts a run covers.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    style: FrameStyle
    backdrop: Image.Image
    """Already prepared to `style.canvas_size` by `build`, per
    `style.backdrop`. Whether that meant decoding a wallpaper, a solid fill
    or a gradient is settled before `frame` runs, so the composite itself
    never branches — and a batch decodes its wallpaper exactly once."""

    @classmethod
    def build(cls, style: FrameStyle | None = None) -> WindowFramer:
        """Build a framer, preparing its backdrop once up front.

        This is the only place the backdrop's own I/O can happen, which is
        what keeps `frame` pure and makes the per-file cost independent of
        the backdrop kind.
        """
        resolved = style or FrameStyle()
        return cls(style=resolved, backdrop=resolved.backdrop.prepare(resolved.canvas_size))

    def window_mask(self, size: tuple[int, int]) -> Image.Image:
        """An anti-aliased rounded-rectangle alpha mask at `size`."""
        width, height = size
        factor = self.style.supersample
        big = Image.new("L", (width * factor, height * factor), 0)
        ImageDraw.Draw(big).rounded_rectangle(
            (0, 0, width * factor - 1, height * factor - 1),
            radius=self.style.radius_px() * factor,
            fill=255,
        )
        return big.resize((width, height), _LANCZOS)

    def _blurred_silhouette(
        self, silhouette: Image.Image, origin: tuple[int, int], sigma: float, alpha: float
    ) -> Image.Image:
        """Paste `silhouette` at `origin` on a canvas-sized mask, blur, scale alpha."""
        layer = Image.new("L", self.style.canvas_size, 0)
        layer.paste(silhouette, origin)
        blurred = layer.filter(ImageFilter.GaussianBlur(sigma))
        return blurred.point(lambda value: int(value * alpha))

    def _shadow_mask(self, placement: tuple[int, int, int, int]) -> Image.Image:
        """The combined cast and contact shadow, as one alpha mask.

        Both layers blur the window's own rounded silhouette rather than its
        bounding box. That is what `filter: drop-shadow()` does in a browser
        and what `box-shadow` does not: a box shadow leaks square corners out
        from behind a rounded window.
        """
        style = self.style
        x, y, width, height = placement
        spread = style.shadow_spread_px()
        silhouette = self.window_mask((width, height)).resize(
            (width + 2 * spread, height + 2 * spread), _LANCZOS
        )
        corner = (x - spread, y - spread)

        cast = self._blurred_silhouette(
            silhouette,
            (corner[0], corner[1] + style.shadow_offset_y_px()),
            style.shadow_blur_px(),
            style.shadow_opacity,
        )
        contact = self._blurred_silhouette(
            silhouette,
            corner,
            style.shadow_blur_px() * style.ambient_blur_scale,
            style.shadow_opacity * style.ambient_opacity_scale,
        )
        # `cast over contact`: cast + contact * (1 - cast), in 8-bit space.
        under = ImageChops.multiply(contact, ImageChops.invert(cast))
        return ImageChops.add(cast, under)

    def plate(self, placement: tuple[int, int, int, int]) -> Image.Image:
        """The backdrop with `placement`'s shadow composited, and no window yet."""
        black = Image.new("RGB", self.style.canvas_size, (0, 0, 0))
        return Image.composite(black, self.backdrop, self._shadow_mask(placement))

    def frame(self, source: Image.Image) -> Image.Image:
        """Render `source` as a rounded window on the prepared backdrop.

        The source is contained, never cropped, so this is safe for any input
        aspect ratio; a portrait capture simply occupies less of the canvas.
        """
        placement = self.style.place(source.size)
        x, y, width, height = placement
        inner = source.convert("RGB").resize((width, height), _LANCZOS)

        canvas = self.plate(placement)
        canvas.paste(inner, (x, y), self.window_mask((width, height)))
        return canvas


PALETTE_COLORS: Final[int] = 256
"""Palette size for a published frame.

A framed shot is a photograph (the wallpaper) wrapped around flat UI, which is
the worst case for PNG: it cannot exploit the photo's smoothness the way JPEG
would, and it pays full truecolour for the UI's handful of colours. Measured on
`tui-list` at 1920x1080: 1066 KB truecolour, 347 KB at 256 colours with
Floyd-Steinberg dithering, for a mean absolute difference of 0.85/255 and no
banding visible in the sky, which is the region that would show it first.

Dithering is what makes that true, so do not drop it to save the noise: the
gradient bands immediately without it. `FASTOCTREE` measured smaller again
(232 KB) and is not worth the quality it gives back.
"""


def save_framed(image: Image.Image, path: Path) -> None:
    """Write a framed image as a dithered, palette-compressed PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image.quantize(
        colors=PALETTE_COLORS,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    ).save(path, optimize=True)


def _frame_file(framer: WindowFramer, source_path: Path, out_path: Path) -> None:
    """Frame one PNG on disk, creating `out_path`'s parent directory if needed."""
    with Image.open(source_path) as handle:
        source = handle.convert("RGB")
    save_framed(framer.frame(source), out_path)


def main(argv: list[str] | None = None) -> int:
    """Frame one or more screenshots onto a consistent 16:9 canvas.

    `python -m tools.screenshots.frame docs/img/screenshots/webapp-*.png`
    frames each file in place; `--out DIR` writes the framed copies there
    instead, under their original filenames.
    """
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="PNG files to frame")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: frame each file in place)",
    )
    args = parser.parse_args(argv)

    missing = [str(p) for p in args.inputs if not p.exists()]
    if missing:
        print(f"missing input(s): {missing}", file=sys.stderr)
        return 2

    framer = WindowFramer.build()
    for source_path in args.inputs:
        out_path = (args.out / source_path.name) if args.out else source_path
        _frame_file(framer, source_path, out_path)
        print(f"framed {source_path} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
