"""Geometry and pixel tests for the screenshot compositor.

No network: every source image is generated in-memory with Pillow. Most
backdrops here are the two pure pixel generators, which keeps the geometry
tests independent of any file; `ImageBackdrop` is exercised against a
wallpaper written into `tmp_path`, plus one check that the committed default
really is on disk where the module says it is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError
from tools.screenshots.frame import (
    DEFAULT_BACKDROP_IMAGE,
    FrameStyle,
    GradientBackdrop,
    ImageBackdrop,
    SolidBackdrop,
    WindowFramer,
)


def _rgb_image(width: int, height: int, color: tuple[int, int, int] = (10, 20, 30)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


class TestPlace:
    """`FrameStyle.place` contains and centres a source without cropping."""

    def test_square_source_is_centred_and_within_content_box(self) -> None:
        style = FrameStyle()
        x, y, width, height = style.place((1000, 1000))
        box_w, box_h = style.content_box()

        assert x >= 0
        assert y >= 0
        assert x + width <= style.canvas_width
        assert y + height <= style.canvas_height
        assert width <= box_w
        assert height <= box_h
        # Centred: equal margin on both sides, to within one rounding pixel.
        assert abs(x - (style.canvas_width - width - x)) <= 1
        assert abs(y - (style.canvas_height - height - y)) <= 1

    def test_aspect_ratio_is_preserved(self) -> None:
        style = FrameStyle()
        source_size = (1920, 1080)
        _, _, width, height = style.place(source_size)

        source_ratio = source_size[0] / source_size[1]
        placed_ratio = width / height
        assert placed_ratio == pytest.approx(source_ratio, rel=0.01)

    def test_portrait_source_lands_inside_content_box(self) -> None:
        style = FrameStyle()
        box_w, box_h = style.content_box()
        x, y, width, height = style.place((600, 1200))

        assert width <= box_w
        assert height <= box_h
        assert x >= 0 and y >= 0
        assert x + width <= style.canvas_width
        assert y + height <= style.canvas_height
        # A portrait source is height-bound: it should not fill the full
        # content width, or the fit would have cropped it.
        assert width < box_w

    def test_landscape_source_lands_inside_content_box(self) -> None:
        style = FrameStyle()
        box_w, box_h = style.content_box()
        x, y, width, height = style.place((2400, 900))

        assert width <= box_w
        assert height <= box_h
        assert x >= 0 and y >= 0
        assert x + width <= style.canvas_width
        assert y + height <= style.canvas_height

    def test_never_crops_a_source_wider_than_the_canvas_aspect(self) -> None:
        """A very wide source is width-bound; height must shrink to fit, never crop."""
        style = FrameStyle()
        box_w, box_h = style.content_box()
        _, _, width, height = style.place((5000, 500))

        assert width <= box_w
        assert height <= box_h
        # Width-bound: it should be constrained by the box width, not fill
        # the whole height either.
        assert height < box_h


class TestFrameStyleValidation:
    """The padding validator rejects a style that leaves no content box."""

    def test_default_style_is_valid(self) -> None:
        style = FrameStyle()
        box_w, box_h = style.content_box()
        assert box_w >= 1
        assert box_h >= 1

    def test_degenerate_padding_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="no content box"):
            FrameStyle(canvas_width=16, canvas_height=9, padding_x_min_ratio=0.49)


class TestBackdrops:
    """`SolidBackdrop` and `GradientBackdrop` generate pixels with no file I/O."""

    def test_solid_backdrop_fills_exact_color(self) -> None:
        backdrop = SolidBackdrop(color="#123456")
        image = backdrop.prepare((40, 20))

        assert image.size == (40, 20)
        assert image.getpixel((0, 0)) == (0x12, 0x34, 0x56)
        assert image.getpixel((39, 19)) == (0x12, 0x34, 0x56)

    def test_solid_backdrop_rejects_a_malformed_color(self) -> None:
        with pytest.raises(ValidationError):
            SolidBackdrop(color="not-a-color")

    def test_linear_gradient_runs_from_start_to_end_top_to_bottom(self) -> None:
        backdrop = GradientBackdrop(start="#000000", end="#ffffff", direction="linear")
        image = backdrop.prepare((10, 100))

        assert image.size == (10, 100)
        top = image.getpixel((5, 0))
        bottom = image.getpixel((5, 99))
        middle = image.getpixel((5, 50))
        assert top < bottom  # darker at the top, lighter at the bottom
        assert top < middle < bottom

    def test_radial_gradient_is_start_colored_at_center(self) -> None:
        backdrop = GradientBackdrop(start="#ffffff", end="#000000", direction="radial")
        image = backdrop.prepare((200, 120))

        center = image.getpixel((100, 60))
        corner = image.getpixel((0, 0))
        # Centre stays close to `start` (white); the corner falls toward
        # `end` (black) as it is furthest from the centre.
        assert sum(center) > sum(corner)


class TestImageBackdrop:
    """`ImageBackdrop` covers the canvas without letterboxing or stretching."""

    @staticmethod
    def _wallpaper(tmp_path: Path, width: int, height: int) -> Path:
        """A wallpaper whose left half is red and right half is blue.

        The seam is what makes a crop observable: a cover fit that centred
        wrongly, or contained instead of covered, moves it.
        """
        image = Image.new("RGB", (width, height), (255, 0, 0))
        image.paste(Image.new("RGB", (width // 2, height), (0, 0, 255)), (width // 2, 0))
        path = tmp_path / "wallpaper.png"
        image.save(path)
        return path

    def test_covers_the_canvas_from_a_wider_source(self, tmp_path: Path) -> None:
        backdrop = ImageBackdrop(path=self._wallpaper(tmp_path, 400, 100))
        image = backdrop.prepare((200, 200))

        assert image.size == (200, 200)
        # Covered, so the 4:1 source is scaled by height and cropped
        # horizontally about its centre — the red/blue seam stays mid-canvas.
        assert image.getpixel((0, 100)) == (255, 0, 0)
        assert image.getpixel((199, 100)) == (0, 0, 255)

    def test_covers_the_canvas_from_a_taller_source(self, tmp_path: Path) -> None:
        backdrop = ImageBackdrop(path=self._wallpaper(tmp_path, 100, 400))
        image = backdrop.prepare((300, 100))

        # Every pixel is painted: a contain fit would leave bars of the
        # canvas's own zeroed background at the sides.
        assert image.size == (300, 100)
        assert image.getpixel((0, 0)) != (0, 0, 0)
        assert image.getpixel((299, 99)) != (0, 0, 0)

    def test_the_committed_default_wallpaper_is_present(self) -> None:
        # The default is resolved off the module's path, so a missing or
        # moved asset must fail here rather than in a screenshot run.
        assert DEFAULT_BACKDROP_IMAGE.is_file()
        assert ImageBackdrop().prepare((80, 45)).size == (80, 45)


class TestWindowFramer:
    """End-to-end compositing: output size and non-degenerate placement."""

    def test_frame_output_matches_canvas_size(self) -> None:
        style = FrameStyle(
            canvas_width=240,
            canvas_height=135,
            backdrop=SolidBackdrop(color="#000000"),
        )
        framer = WindowFramer.build(style)
        source = _rgb_image(400, 300)

        framed = framer.frame(source)
        assert framed.size == style.canvas_size

    def test_frame_handles_portrait_and_landscape_sources(self) -> None:
        style = FrameStyle(
            canvas_width=240,
            canvas_height=135,
            backdrop=SolidBackdrop(color="#000000"),
        )
        framer = WindowFramer.build(style)

        for source_size in [(400, 300), (150, 400), (800, 200)]:
            framed = framer.frame(_rgb_image(*source_size))
            assert framed.size == style.canvas_size

    def test_build_uses_default_style_when_none_given(self) -> None:
        framer = WindowFramer.build()
        assert framer.style.canvas_size == (2400, 1350)
        assert framer.backdrop.size == (2400, 1350)
