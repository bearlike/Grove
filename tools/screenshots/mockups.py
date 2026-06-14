"""Composite the landing-page device mockups from the latest screenshots.

The landing page (`docs/index.md`) shows Grove twice: the TUI on a laptop
and the web dashboard on a phone. This tool frames the freshest captures
in lightweight CSS device frames and rasterizes them with headless
Chromium, so the mockups always carry the current synthetic fleet (and
never leak a real repo or profile name).

Inputs (produced by the other two capture tools):
- ``docs/img/screenshots/tui-list.svg``         → the laptop screen
- ``docs/img/screenshots/webapp-home-mobile.png`` → the phone screen

Outputs:
- ``docs/img/mockups/tui-laptop-mockup.png``
- ``docs/img/mockups/webapp-phone-mockup.png``

Run via:

    make docs-mockups

or directly:

    uv run python -m tools.screenshots.mockups

Requires a Chromium binary on PATH (``chromium`` / ``chromium-browser`` /
``google-chrome``).
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOTS = REPO_ROOT / "docs" / "img" / "screenshots"
OUT = REPO_ROOT / "docs" / "img" / "mockups"


def _data_uri(path: Path) -> str:
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    raw = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{raw}"


def _chromium() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no Chromium binary found on PATH")


_LAPTOP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;background:transparent}}
  .stage{{width:920px;height:560px;display:flex;align-items:center;justify-content:center}}
  .laptop{{display:flex;flex-direction:column;align-items:center;
    filter:drop-shadow(0 22px 40px rgba(0,0,0,.32))}}
  .screen{{width:792px;padding:14px 14px 16px;background:#0d0d0f;border-radius:18px 18px 6px 6px;
    box-shadow:inset 0 0 0 1px #2a2a2e}}
  .screen img{{display:block;width:764px;height:430px;object-fit:cover;object-position:top left;
    border-radius:6px;background:#1b1b1d}}
  .cam{{width:6px;height:6px;border-radius:50%;background:#26262a;margin:0 auto 6px}}
  .base{{width:888px;height:18px;background:linear-gradient(#d7dade,#b9bdc2);
    border-radius:0 0 12px 12px;clip-path:polygon(2% 0,98% 0,100% 100%,0 100%)}}
  .notch{{width:120px;height:9px;background:#aeb2b7;border-radius:0 0 9px 9px;margin:-1px auto 0}}
</style></head><body><div class="stage"><div class="laptop">
  <div class="screen"><div class="cam"></div><img src="{src}"></div>
  <div class="base"></div><div class="notch"></div>
</div></div></body></html>"""

_PHONE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;background:transparent}}
  .stage{{width:400px;height:800px;display:flex;align-items:center;justify-content:center}}
  .phone{{position:relative;width:340px;height:736px;background:#0c0c0e;border-radius:52px;
    padding:13px;box-shadow:inset 0 0 0 2px #2c2c30,0 22px 44px rgba(0,0,0,.34)}}
  .phone img{{display:block;width:314px;height:710px;object-fit:cover;object-position:top center;
    border-radius:40px;background:#1b1b1d}}
  .island{{position:absolute;top:24px;left:50%;transform:translateX(-50%);
    width:104px;height:28px;background:#000;border-radius:16px;z-index:2}}
</style></head><body><div class="stage"><div class="phone">
  <div class="island"></div><img src="{src}">
</div></div></body></html>"""


def _render(html: str, size: tuple[int, int], out: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(html)
        page = Path(fh.name)
    try:
        subprocess.run(
            [
                _chromium(),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=2",
                "--default-background-color=00000000",
                f"--window-size={size[0]},{size[1]}",
                f"--screenshot={out}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        page.unlink(missing_ok=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tui = SHOTS / "tui-list.svg"
    phone = SHOTS / "webapp-home-mobile.png"
    missing = [p.name for p in (tui, phone) if not p.exists()]
    if missing:
        print(f"missing inputs: {missing}; run the capture tools first", file=sys.stderr)
        return 2

    _render(
        _LAPTOP_HTML.format(src=_data_uri(tui)),
        (920, 560),
        OUT / "tui-laptop-mockup.png",
    )
    _render(
        _PHONE_HTML.format(src=_data_uri(phone)),
        (400, 800),
        OUT / "webapp-phone-mockup.png",
    )
    print(f"wrote device mockups to {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
