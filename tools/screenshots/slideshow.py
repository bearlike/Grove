"""Build one looping GIF tour out of the already-framed screenshots.

The README cannot inline-play an mp4 (see `docs/CLAUDE.md`), so the one moving
image it can carry is a GIF. This assembles that GIF from the SAME framed PNGs
the docs site publishes, rather than from a separate recording, which is what
keeps the two surfaces telling one story: if a shot is re-captured, the tour
re-renders from it and cannot drift.

WHY THE FRAMED SHOTS AND NOT RAW CAPTURES. Every framed shot sits on the same
wallpaper with the same window placement, so the backdrop is byte-identical
across all of them — and, because a crossfade between two identical backdrops is
that backdrop, it stays identical through the transitions too. GIF encodes each
frame as a delta against the last, so the entire wallpaper costs nothing after
frame one and only the window interior is ever paid for. A tour built from
unframed captures would have no such shared region.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Final

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

_LANCZOS = Image.Resampling.LANCZOS

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHOTS_DIR: Final[Path] = REPO_ROOT / "docs" / "img" / "screenshots"

TOUR: Final[tuple[str, ...]] = (
    "webapp-composer",
    "webapp-sessions",
    "webapp-workspace",
    "webapp-usage",
    "webapp-home",
)
"""The tour, in narrative order: start a workspace, find a session, watch one
work, see what it spent, then the whole fleet. `webapp-home` is the fleet
dashboard and it lands last so the loop returns to the composer, which is the
page a reader would actually open first."""


class SlideshowStyle(BaseModel):
    """Timing and size for the tour. Ratios of a second, not frame counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(default=960, ge=160)
    """Published width. The framed sources are 1920 wide; a GIF at that size
    would be several megabytes for no gain, since this is decoration in a README
    column rather than something a reader zooms into."""

    hold_ms: int = Field(default=800, ge=10)
    fade_ms: int = Field(default=320, ge=0)
    fade_steps: int = Field(default=8, ge=0)
    """Eight steps over 320ms is 40ms a step, which is about as slow as a GIF
    frame can be before the fade reads as a stutter rather than a blend."""

    colors: int = Field(default=256, ge=2, le=256)

    dither: bool = False
    """Whether to dither each frame against the shared palette.

    OFF, AND THE REASON IS THE FILE SIZE RATHER THAN THE LOOK. Error diffusion
    propagates left-to-right and top-to-bottom across the whole frame, so a
    change confined to the window interior perturbs the dither pattern in the
    wallpaper AFTER it — which destroys exactly the frame-to-frame identity this
    module is built on. Measured over a five-slide tour: 7840 KB dithered
    against 4205 KB not. The banding it costs is far less visible here than in a
    full-resolution still, because the tour is published at 960px and every
    frame is on screen for well under a second.
    """

    @property
    def fade_frame_ms(self) -> int:
        """Duration of one fade step, floored at the GIF's practical minimum."""
        if self.fade_steps == 0:
            return 0
        return max(20, round(self.fade_ms / self.fade_steps))


class Slideshow(BaseModel):
    """Turn an ordered list of stills into a looping, crossfading GIF."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    style: SlideshowStyle = SlideshowStyle()

    def _load(self, path: Path) -> Image.Image:
        """Decode one still and scale it to the published width."""
        with Image.open(path) as handle:
            source = handle.convert("RGB")
        height = round(source.height * self.style.width / source.width)
        return source.resize((self.style.width, height), _LANCZOS)

    def _timeline(self, stills: list[Image.Image]) -> tuple[list[Image.Image], list[int]]:
        """Expand stills into (frames, per-frame durations).

        Each still is ONE frame held for `hold_ms` rather than many frames of
        the same picture: GIF carries a per-frame delay, so a hold costs one
        frame however long it lasts.

        The fade after the LAST still targets the first, because the GIF loops —
        without it the tour snaps back to the beginning, which is the one
        transition a reader is guaranteed to see twice.
        """
        frames: list[Image.Image] = []
        durations: list[int] = []
        for index, still in enumerate(stills):
            frames.append(still)
            durations.append(self.style.hold_ms)
            nxt = stills[(index + 1) % len(stills)]
            for step in range(1, self.style.fade_steps + 1):
                frames.append(Image.blend(still, nxt, step / (self.style.fade_steps + 1)))
                durations.append(self.style.fade_frame_ms)
        return frames, durations

    def _palette(self, frames: list[Image.Image]) -> Image.Image:
        """One adaptive palette for the whole tour.

        Per-frame palettes make the backdrop resolve to slightly different
        colours from frame to frame, which both destroys the delta encoding this
        module is built around and shows up as a faint shimmer in flat areas.
        The palette is derived from a coarse tile of every frame so no single
        still dominates it.
        """
        cell = max(1, self.style.width // 8)
        columns = min(len(frames), 8)
        rows = (len(frames) + columns - 1) // columns
        ratio = frames[0].height / frames[0].width
        tile_h = max(1, round(cell * ratio))
        sheet = Image.new("RGB", (cell * columns, tile_h * rows))
        for index, frame in enumerate(frames):
            sheet.paste(
                frame.resize((cell, tile_h), _LANCZOS),
                ((index % columns) * cell, (index // columns) * tile_h),
            )
        return sheet.quantize(colors=self.style.colors, method=Image.Quantize.MEDIANCUT)

    def render(self, sources: list[Path], out_path: Path) -> int:
        """Write the looping GIF, returning its size in bytes."""
        stills = [self._load(path) for path in sources]
        frames, durations = self._timeline(stills)
        palette = self._palette(frames)
        dither = Image.Dither.FLOYDSTEINBERG if self.style.dither else Image.Dither.NONE
        mapped = [frame.quantize(palette=palette, dither=dither) for frame in frames]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        mapped[0].save(
            out_path,
            save_all=True,
            append_images=mapped[1:],
            duration=durations,
            loop=0,
            optimize=True,
            # Leave each frame in place for the next to draw over. The frames are
            # opaque and same-sized, so nothing needs restoring between them, and
            # `disposal=2` would blank the canvas and forfeit the delta encoding.
            disposal=1,
        )
        return out_path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    """Render the web dashboard tour GIF from the published framed shots."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=SHOTS_DIR / "webapp-tour.gif",
        help="output GIF path",
    )
    args = parser.parse_args(argv)

    sources = [SHOTS_DIR / f"{name}.png" for name in TOUR]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        print(f"missing framed shot(s): {missing}", file=sys.stderr)
        print("run `make docs-webapp-screenshots` first", file=sys.stderr)
        return 2

    size = Slideshow().render(sources, args.out)
    print(f"wrote {args.out} ({size / 1024:.0f} KB, {len(sources)} slides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
