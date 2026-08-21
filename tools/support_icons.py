"""Generate the README's support tiles from pinned upstream icon assets.

The README strip is a small product-support census, not a second icon library.
It has five fixed members because those are Grove's real provider boundaries:
two agent adapters and three ticket providers. Each tile gets the same canvas,
corner radius, highlight and shadow so logos from two upstream collections read
as one family without redrawing or semantically altering any mark.

Run from the repo root:

    uv run --group dev python -m tools.support_icons

The source icons are pinned by digest below. A regeneration therefore fails
closed if an upstream `main` branch changes instead of silently committing a new
trademark drawing under the old filename.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path
from typing import Final, Literal

from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
OUT_DIR: Final[Path] = REPO_ROOT / "docs" / "logos" / "support"

CANVAS: Final[int] = 192
CORNER_RADIUS: Final[int] = 42
ICON_BOX: Final[int] = 116

LOBE_AVATAR_PACKAGE_URL: Final[str] = (
    "https://registry.npmjs.org/@lobehub/icons-static-avatar/-/icons-static-avatar-1.13.0.tgz"
)
LOBE_PNG_PACKAGE_URL: Final[str] = (
    "https://registry.npmjs.org/@lobehub/icons-static-png/-/icons-static-png-1.95.0.tgz"
)
SELFHST_BASE: Final[str] = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/png"


class IconSource(BaseModel):
    """One pinned upstream source plus the tile treatment it needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["claude-code", "codex", "linear", "github", "gitea"]
    provider: Literal["lobe-avatar", "lobe-png", "selfhst"]
    member: str
    sha256: str
    background: tuple[str, str]
    scale: float = Field(default=1.0, gt=0.0, le=1.2)
    white_mark: bool = False


SOURCES: Final[tuple[IconSource, ...]] = (
    IconSource(
        name="claude-code",
        provider="lobe-avatar",
        member="package/avatars/claudecode.webp",
        sha256="7096eefe0cb2cae91f58f17ef9c53d6b6593648eb7111357d55586da0e8d9ce1",
        background=("#09090b", "#18181b"),
        scale=1.12,
    ),
    IconSource(
        name="codex",
        provider="lobe-png",
        member="package/dark/codex-color.png",
        sha256="fcea9ddbaafdca236a8380cef2ecd3342ecd9914a7b080873873cf45f415686d",
        background=("#f8f9fb", "#e9edf5"),
        scale=1.08,
    ),
    IconSource(
        name="linear",
        provider="selfhst",
        member="linear.png",
        sha256="0affddfb46ec44fe0e6ff0c6aff14af761cc042ad5f8d372ec66687e5425793b",
        background=("#f4f1ff", "#ddd6ff"),
        scale=0.9,
    ),
    IconSource(
        name="github",
        provider="selfhst",
        member="github.png",
        sha256="a7bf8919a48f7c84e9243cd646929c8b4aebbaae3ae83233f2deb90c4a814b2d",
        background=("#24292f", "#0d1117"),
        scale=0.94,
        white_mark=True,
    ),
    IconSource(
        name="gitea",
        provider="selfhst",
        member="gitea.png",
        sha256="dc09c72dc5bf573142dcc5c0b054271753cdf65be5700f77ae382cfa8b44f6c3",
        background=("#f1f8e9", "#dcedc8"),
        scale=1.08,
    ),
)


def _download(url: str) -> bytes:
    """Read one upstream asset with a bounded request."""
    request = urllib.request.Request(url, headers={"User-Agent": "Grove support-icon builder"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _verify(source: IconSource, payload: bytes) -> bytes:
    """Return `payload` only when it matches the pinned digest."""
    actual = hashlib.sha256(payload).hexdigest()
    if actual != source.sha256:
        raise ValueError(f"{source.name}: sha256 changed: expected {source.sha256}, got {actual}")
    return payload


def _package_payloads(url: str, provider: str) -> dict[str, bytes]:
    """Extract one provider's pinned files from one npm tarball download."""
    payload = _download(url)
    wanted = {source.member for source in SOURCES if source.provider == provider}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        return {
            member: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in wanted
        }


def _gradient(colors: tuple[str, str]) -> Image.Image:
    """Render a quiet top-to-bottom tile background."""
    top = Image.new("RGB", (1, 1), colors[0]).getpixel((0, 0))
    bottom = Image.new("RGB", (1, 1), colors[1]).getpixel((0, 0))
    column = Image.new("RGB", (1, CANVAS))
    pixels = column.load()
    for y in range(CANVAS):
        t = y / (CANVAS - 1)
        pixels[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
    return column.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def _rounded_mask() -> Image.Image:
    """The shared App-Store-like tile silhouette."""
    scale = 4
    mask = Image.new("L", (CANVAS * scale, CANVAS * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, CANVAS * scale - 1, CANVAS * scale - 1),
        radius=CORNER_RADIUS * scale,
        fill=255,
    )
    return mask.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def _prepare_logo(source: IconSource, payload: bytes) -> Image.Image:
    """Contain one upstream mark in the common optical box.

    Claude Code's avatar is already a finished square tile, so it is scaled as
    a whole. Codex deliberately uses LobeHub's standalone transparent mark —
    using its Avatar here nests one rounded square inside another. The selfh.st
    files are transparent logo canvases too. Those are cropped to visible
    artwork before scaling, which keeps a wide Gitea cup and a circular GitHub
    mark optically comparable instead of giving both the same invisible box.
    """
    with Image.open(io.BytesIO(payload)) as handle:
        logo = handle.convert("RGBA")
    if source.provider != "lobe-avatar":
        bbox = logo.getchannel("A").getbbox()
        if bbox is None:
            raise ValueError(f"{source.name}: source icon is fully transparent")
        logo = logo.crop(bbox)
    if source.white_mark:
        # Preserve the upstream silhouette and anti-aliased alpha exactly; only
        # the solid brand fill changes. GitHub's near-black octocat otherwise
        # disappears into the dark tile made for it.
        alpha = logo.getchannel("A")
        logo = Image.new("RGBA", logo.size, (255, 255, 255, 0))
        logo.putalpha(alpha)
    box = round(ICON_BOX * source.scale)
    logo.thumbnail((box, box), Image.Resampling.LANCZOS)
    return logo


def _tile(source: IconSource, payload: bytes) -> Image.Image:
    """Composite one unmodified upstream logo into the shared tile treatment."""
    mask = _rounded_mask()
    backdrop = _gradient(source.background).convert("RGBA")

    # A small inner highlight and lower shadow are shared across all five tiles.
    # They model the tile, not the logo: no brand path is redrawn or recoloured.
    highlight = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 0))
    ImageDraw.Draw(highlight).rounded_rectangle(
        (1, 1, CANVAS - 2, CANVAS - 2),
        radius=CORNER_RADIUS - 1,
        outline=(255, 255, 255, 55),
        width=2,
    )
    backdrop.alpha_composite(highlight)

    shade = Image.new("L", (1, CANVAS), 0)
    shade.putdata([round(max(0, (y / CANVAS - 0.58)) * 78) for y in range(CANVAS)])
    shade = shade.resize((CANVAS, CANVAS))
    black = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 255))
    backdrop = Image.composite(black, backdrop, shade)

    logo = _prepare_logo(source, payload)
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    x = (CANVAS - logo.width) // 2
    y = (CANVAS - logo.height) // 2
    alpha = logo.getchannel("A")
    shadow_alpha = Image.new("L", (CANVAS, CANVAS), 0)
    shadow_alpha.paste(alpha, (x, y + 5))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(8)).point(lambda value: value // 2)
    shadow.putalpha(shadow_alpha)
    backdrop.alpha_composite(shadow)
    backdrop.alpha_composite(logo, (x, y))

    output = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    output.paste(backdrop, mask=mask)
    return output


def main() -> None:
    """Fetch, verify and render the five support icons."""
    packages = {
        "lobe-avatar": _package_payloads(LOBE_AVATAR_PACKAGE_URL, "lobe-avatar"),
        "lobe-png": _package_payloads(LOBE_PNG_PACKAGE_URL, "lobe-png"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for source in SOURCES:
        payload = (
            _download(f"{SELFHST_BASE}/{source.member}")
            if source.provider == "selfhst"
            else packages[source.provider][source.member]
        )
        image = _tile(source, _verify(source, payload))
        out = OUT_DIR / f"{source.name}.png"
        image.save(out, optimize=True)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
