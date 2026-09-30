"""Render the landing page's header aura, a tiny colour field the GPU upscales.

The aura is not a CSS gradient because the look it matches is not one: a hue
ramp from amber through red into near-black burgundy, fading to the page
floor faster on the right than on the left. Radial gradients approximating
that read as flat tan once blended. So the field is synthesized from three
profiles measured on the approved reference: the hue ramp across its empty
sky band, and the falloff down its text-free left and right margins. Between
the margins the falloff is interpolated. Sharp edges are impossible at this
size, so the 96x40 PNG is the whole asset, about 2 KB.

Run from the repository root after changing a profile:

    uv run --group dev python -m tools.landing_aura
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PIL import Image, ImageFilter

OUTPUT: Final = Path("docs/img/landing/aura.png")
SIZE: Final = (96, 40)
# The page floor under the aura, the value every column fades into.
FLOOR: Final = (0x05, 0x05, 0x05)
# Left to right across the band, top of the hero. A solar ramp: the sun's
# white-gold core, solar yellow, amber, sun orange, sun red, deep red,
# burgundy, then the floor. Every stop is darker than the one before it, so
# the field never flickers back toward yellow the way the old band did at
# its fifth stop. The hot half is compressed into the first third so the
# glow keeps a tight radius instead of spanning the header.
BAND: Final = [
    "ffd98a",
    "ffc75a",
    "fbb238",
    "f59a1e",
    "ee8214",
    "e46a10",
    "d4520c",
    "c03c0a",
    "a82a08",
    "8e1c07",
    "741207",
    "5c0a07",
    "4a0608",
    "3d0409",
    "34030a",
    "2d030a",
    "280309",
    "240309",
    "210309",
    "1e0309",
    "1c0309",
    "1a0309",
    "190209",
]
# Top to bottom down each margin; the left burns deeper than the right, but
# both reach the floor by the upper half so the aura stays a corner glow.
LEFT: Final = [
    "ffd07a",
    "f8b040",
    "ec9020",
    "da6e10",
    "c2540e",
    "a03e0c",
    "7c2c0d",
    "5c1f0e",
    "42180f",
    "2f140f",
    "1f100c",
    "150d0a",
    "0d0907",
    "080606",
    "050505",
    "050505",
    "050505",
]
RIGHT: Final = [
    "4c100c",
    "42100c",
    "3a100c",
    "33100c",
    "2d100d",
    "28100d",
    "24100d",
    "20100d",
    "1b0e0c",
    "160c0a",
    "110a08",
    "0d0807",
    "090606",
    "060505",
    "050505",
    "050505",
    "050505",
]

Rgb = tuple[float, float, float]


class AuraField:
    """The measured profiles, sampled into one colour per pixel."""

    @staticmethod
    def _rgb(hex_colour: str) -> Rgb:
        return (int(hex_colour[0:2], 16), int(hex_colour[2:4], 16), int(hex_colour[4:6], 16))

    @classmethod
    def hue(cls, hex_colour: str) -> float:
        """Hue in degrees, so a test can count how many distinct hues a ramp visits."""
        red, green, blue = (channel / 255 for channel in cls._rgb(hex_colour))
        high, low = max(red, green, blue), min(red, green, blue)
        if high == low:
            return 0.0
        if high == red:
            return (60 * ((green - blue) / (high - low))) % 360
        if high == green:
            return 60 * ((blue - red) / (high - low)) + 120
        return 60 * ((red - green) / (high - low)) + 240

    @classmethod
    def sample(cls, stops: list[str], t: float) -> Rgb:
        colours = [cls._rgb(stop) for stop in stops]
        position = t * (len(colours) - 1)
        index = min(int(position), len(colours) - 2)
        fraction = position - index
        start, end = colours[index], colours[index + 1]
        return (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
            start[2] + (end[2] - start[2]) * fraction,
        )

    @classmethod
    def strength(cls, column: list[str], t: float) -> float:
        """How far a margin sits from the floor at depth t, 1.0 at the top."""
        top, here = cls.sample(column, 0), cls.sample(column, t)
        span = sum(abs(a - b) for a, b in zip(top, FLOOR, strict=True)) or 1
        return sum(abs(a - b) for a, b in zip(here, FLOOR, strict=True)) / span

    @classmethod
    def render(cls) -> Image.Image:
        width, height = SIZE
        image = Image.new("RGB", SIZE)
        for y in range(height):
            depth = y / (height - 1)
            left, right = cls.strength(LEFT, depth), cls.strength(RIGHT, depth)
            for x in range(width):
                across = x / (width - 1)
                hue = cls.sample(BAND, across)
                mix = left + (right - left) * across
                image.putpixel(
                    (x, y),
                    tuple(round(f + (h - f) * mix) for h, f in zip(hue, FLOOR, strict=True)),  # type: ignore[arg-type]
                )
        return image.filter(ImageFilter.GaussianBlur(1.2))


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AuraField.render().save(OUTPUT, optimize=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
