import importlib.util
import re
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest
import yaml
from PIL import Image
from tools.landing_aura import BAND, FLOOR, LEFT, OUTPUT, RIGHT, AuraField
from tools.landing_bundle import LandingBundle

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"
SCRIPTS = DOCS / "javascripts"


def _load_hook(name: str) -> ModuleType:
    """Import a mkdocs hook by path; docs/hooks is not a package."""
    spec = importlib.util.spec_from_file_location(name, DOCS / "hooks" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defer_vendor_scripts = _load_hook("defer_vendor_scripts")


def scene_source() -> str:
    """The factory scene's readable source: its entry plus every module under factory/."""
    modules = [SCRIPTS / "grove-factory.js", *sorted((SCRIPTS / "factory").glob("*.js"))]
    return "\n".join(path.read_text() for path in modules)


def test_landing_machine_has_physical_details_and_a_moving_belt() -> None:
    """Inspect real Three.js geometry, not source strings claiming it exists."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the Three.js geometry probe")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("landing_geometry.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_site_root_is_the_landing_page_and_leads_into_the_docs() -> None:
    """A visitor to the published URL meets the landing page, not the sidebar.

    `index.md` is the site root, so this is the page GitHub Pages serves first;
    its two buttons are the only way into the documentation, and both must
    point at the docs home rather than past it into a mid-guide page.
    """
    rendered = (DOCS / "index.md").read_text()

    assert "template: app.html" in rendered
    assert 'class="grove-factory-scene"' in rendered
    assert "data-grove-factory-pause" not in rendered
    assert "grove-factory-scene__fallback" not in rendered
    assert 'href="https://github.com/bearlike/Grove"' in rendered
    # At the root, links into the docs are page-relative and land on the home.
    assert 'href="home/"' in rendered
    assert 'href="../"' not in rendered
    assert 'href="getting-started/"' not in rendered
    # Old inbound links pointed at the site root's anchors; keep them resolving.
    assert 'id="install"' in rendered
    assert 'id="explore-the-docs"' in rendered
    # The supported-tool row shows the README's marks, in the README's order.
    readme = (ROOT / "README.md").read_text()
    readme_marks = re.findall(r'src="docs/logos/support/([a-z-]+)\.png"', readme)
    landing_marks = re.findall(r'src="logos/support/([a-z-]+)\.png"', rendered)
    assert landing_marks == readme_marks, (landing_marks, readme_marks)
    assert len(landing_marks) >= 6
    for mark in landing_marks:
        assert (DOCS / "logos" / "support" / f"{mark}.png").is_file(), mark


def test_landing_hero_reads_title_marks_body_install_actions() -> None:
    """The card installs the PyPI distribution, never the unrelated `grove`.

    Each tab's radio has a panel the checked-state CSS reveals, so a tab added
    without its selector would switch to nothing.
    """
    rendered = (DOCS / "index.md").read_text()
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    title = rendered.index('id="grove-landing-title"')
    support = rendered.index('class="grove-landing__support"')
    body = rendered.index("Bring the coding agents")
    install = rendered.index('class="grove-landing__install"')
    actions = rendered.index('class="grove-landing__actions"')
    assert title < support < body < install < actions
    card = rendered[install:actions]
    tools = re.findall(r'id="grove-install-([a-z]+)"', card)
    assert tools == ["uv", "pipx", "pip"]
    for tool in tools:
        assert f'data-install="{tool}"' in card
        panel = f'.grove-landing__install-card [data-install="{tool}"]'
        assert f"#grove-install-{tool}:checked ~ {panel}" in css
    commands = re.findall(r"<code>(.*?)</code>", card)
    assert commands and all("grove-factory" in re.sub(r"<[^>]+>", "", c) for c in commands)
    assert 'href="getting-started/#install"' in card
    assert rendered.count('id="install"') == 1


def test_documentation_home_keeps_the_theme_kit_layout() -> None:
    """The landing page never replaces the documentation entry page."""
    home = (DOCS / "home.md").read_text()

    assert "template: app.html" not in home
    assert "grove-landing" not in home
    assert "grove-factory-scene" not in home
    assert 'class="ms-hero"' in home
    assert 'class="swiper ms-shots"' in home
    assert "## What is Grove?" in home
    # One level deep now, so its own raw hrefs and images carry the prefix.
    assert 'href="../getting-started/"' in home
    assert 'src="../img/' in home
    assert not (DOCS / "overview.md").exists(), "overview duplicated the home page"


def test_nav_gives_every_page_in_the_sidebar_an_icon() -> None:
    """A nav entry with no `nav_icons` key renders a label with no glyph."""
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text().replace("!!python/name:", ""))
    icons = config["theme"]["nav_icons"]

    def titles(entries: object) -> list[str]:
        found: list[str] = []
        if isinstance(entries, list):
            for entry in entries:
                found.extend(titles(entry))
        elif isinstance(entries, dict):
            for title, value in entries.items():
                found.append(title)
                found.extend(titles(value))
        return found

    missing = [title for title in titles(config["nav"]) if title not in icons]
    assert missing == [], missing
    # The landing page is the root and carries its own chrome, so it is not a
    # sidebar entry; the sidebar's Home is the documentation home.
    assert "index.md" in config["not_in_nav"]
    assert config["nav"][0] == {"Home": "home.md"}
    home_tab = next(t for t in config["theme"]["header_tabs"] if t["label"] == "Home")
    assert home_tab["url"] == "home/"


def test_landing_scene_uses_only_local_runtime_assets() -> None:
    """The page loads one self-contained bundle built from the vendored runtime."""
    source = (DOCS / "index.md").read_text()
    runtime = SCRIPTS / "vendor" / "three.module.js"

    assert re.search(
        r'<script type="module" src="javascripts/grove-factory\.min\.js(\?v=\d+)?"></script>',
        source,
    )
    assert runtime.is_file()
    assert (runtime.parent / "three.core.js").is_file()
    assert "from './three.core.js'" in runtime.read_text()
    # The readable modules import the vendored build by relative path, never a CDN.
    imports = re.findall(r'from "([^"]+)"', scene_source())
    assert imports and all(spec.startswith(".") for spec in imports), imports
    assert any(spec.endswith("vendor/three.module.js") for spec in imports)
    bundle = (SCRIPTS / "grove-factory.min.js").read_text()
    # Self-contained: no static or dynamic import is left to resolve at runtime.
    assert not re.search(r'\bimport\s*[\w{*][^;]*?from\s*"|\bimport\(', bundle)


def test_landing_scene_resolution_is_budgeted_per_canvas() -> None:
    """The renderer takes its pixel-ratio range from the canvas's own area.

    A flat 1.5 cap left 3x phones at half their native sharpness; the range
    itself is pinned by the geometry probe, this pins that the scene uses it.
    """
    scene = (SCRIPTS / "factory" / "scene.js").read_text()
    assert "FrameGovernor.range({" in scene
    assert "cssPixels: width * height" in scene
    assert not re.search(r"devicePixelRatio[^\n]*,\s*1\.5\)", scene)


def test_landing_bundle_is_built_from_the_current_sources() -> None:
    """Editing the scene or the vendored runtime without rebuilding fails here.

    The digest covers every input file, so this needs no JavaScript toolchain.
    Rebuild with ``uv run --group dev python -m tools.landing_bundle``.
    """
    recorded = LandingBundle.recorded_digest(ROOT)
    assert recorded is not None, "the bundle has no source digest header"
    assert recorded == LandingBundle.digest(ROOT), (
        "stale bundle: run python -m tools.landing_bundle"
    )
    inputs = {path.relative_to(SCRIPTS).as_posix() for path in LandingBundle.sources(ROOT)}
    assert {"grove-factory.js", "factory/scene.js", "vendor/three.core.js"} <= inputs


def test_landing_page_defers_the_docs_themes_cdn_libraries() -> None:
    """The landing page prefetches the theme's CDN libraries instead of running them.

    Mermaid alone is 3.5 MB and nothing on this page uses it. Prefetching keeps
    the documentation pages the visitor opens next warm without making the
    landing page download, parse and execute libraries it never calls.
    """
    front_matter = (DOCS / "index.md").read_text().split("---", 2)[1]
    assert yaml.safe_load(front_matter)["defer_vendor_scripts"] is True
    config = yaml.safe_load((ROOT / "mkdocs.yml").read_text().replace("!!python/name:", ""))
    assert "docs/hooks/defer_vendor_scripts.py" in config["hooks"]

    html = """<link href="https://cdn.example/swiper.css" rel="stylesheet"/>
<link href="css/local.css" rel="stylesheet"/>
<script src="https://unpkg.example/mermaid.min.js"></script>
<script integrity="sha384-x" src="js/mermaid-init.js"></script>"""
    deferred = defer_vendor_scripts.defer(html)
    assert '<link rel="prefetch" href="https://cdn.example/swiper.css">' in deferred
    assert '<link rel="prefetch" href="https://unpkg.example/mermaid.min.js">' in deferred
    # Same-origin assets are untouched: the theme's own glue stays in place.
    assert '<link href="css/local.css" rel="stylesheet"/>' in deferred
    assert '<script integrity="sha384-x" src="js/mermaid-init.js"></script>' in deferred
    assert '<script src="https://' not in deferred

    class Page:
        def __init__(self, meta: dict[str, object]) -> None:
            self.meta = meta

    # Only the opted-in page changes; every docs page keeps its libraries.
    assert defer_vendor_scripts.on_post_page(html, page=Page({})) == html
    assert (
        defer_vendor_scripts.on_post_page(html, page=Page({"defer_vendor_scripts": True}))
        == deferred
    )


def test_agent_display_glyphs_are_local_transparent_brand_coloured_paths() -> None:
    """Each face glyph carries its own brand colour on a transparent field.

    The emissive material multiplies a white emissive by this texture, so the
    fill here is what the viewer actually sees lit on the robot's display; a
    white fill would render all three agents identically.
    """
    source = scene_source()
    # Textures resolve against the module so the scene works from any route.
    assert "import.meta.url" in source
    fills = {"claude-code": "#D97757", "codex": "#7A9DFF", "opencode": "#F5B54A"}
    for name, fill in fills.items():
        assert f'"{name}.svg"' in source
        svg = ElementTree.parse(DOCS / "logos" / "agent-displays" / f"{name}.svg").getroot()
        assert svg.attrib["viewBox"] == "0 0 24 24"
        children = list(svg)
        assert len(children) == 1
        assert children[0].tag == "{http://www.w3.org/2000/svg}path"
        assert children[0].attrib["fill"] == fill, name
        assert children[0].attrib["d"]
    assert len(set(fills.values())) == len(fills)


def test_landing_scene_lighting_follows_the_docs_palette() -> None:
    """The warm side of the rig is the docs' clay, and the lamp sits above the robots.

    Magenta anywhere in the rig reads as a second design system beside the
    terracotta buttons. The lamp's height is the reviewer's flare: at antenna
    height it lit the mirror-chrome knobs from centimetres away and bloom
    turned that into a pulsing star.
    """
    source = scene_source()
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()

    magenta = []
    for literal in re.findall(r"0x([0-9a-fA-F]{6})\b", source):
        red, green, blue = (int(literal[i : i + 2], 16) for i in (0, 2, 4))
        # Warm and bright with a blue channel: pink. Amber has almost no blue,
        # white has as much green as blue, and cyan has no red to speak of.
        if red > 180 and blue > 150 and green < min(red, blue) - 60:
            magenta.append(literal)
    assert magenta == [], magenta
    assert "PALETTE.ember" in source
    lamp = re.search(r"scannerLight\.position\.set\(0, ([\d.]+), [\d.]+\)", source)
    assert lamp is not None
    # Antenna knobs top out at robot y 1.5 + 1.66 in belt space.
    assert float(lamp.group(1)) > 3.2
    assert "this.camera.setViewOffset(" in source
    assert "rgba(90, 71, 170" not in css


def test_landing_scene_has_no_cool_light_and_neutral_machine() -> None:
    """The rig is one warm temperature, and the machine is grey, not tinted.

    A cyan rig read as a second design system beside the warm page, and an
    all-amber one tinted the whole machine orange. Light is warm white, ember
    is the one accent, and only the verified check keeps a cool hue.
    """
    source = scene_source()
    cool = []
    for literal in re.findall(r"0x([0-9a-fA-F]{6})\b", source):
        red, green, blue = (int(literal[i : i + 2], 16) for i in (0, 2, 4))
        if blue > red + 24 and literal.lower() != "37d67f":
            cool.append(literal)
    assert cool == [], cool
    shell = re.search(r'material\(0x([0-9a-fA-F]{6})[^)]*\), "robot-body"\)', source)
    assert shell is not None
    red, green, blue = (int(shell.group(1)[i : i + 2], 16) for i in (0, 2, 4))
    assert max(red, green, blue) - min(red, green, blue) < 12, "the shell is tinted"


def test_landing_aura_is_the_generated_field_and_grain_is_fine() -> None:
    """The aura is the committed field the tool renders, masked and layered.

    The field is synthesized from measured profiles, so the committed PNG must
    be exactly what the tool produces, or the reference and the page drift.
    """
    committed = Image.open(ROOT / OUTPUT).convert("RGB")
    assert committed.tobytes() == AuraField.render().tobytes()
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    rendered = (DOCS / "index.md").read_text()
    assert 'url("../img/landing/aura.png")' in css
    assert 'class="grove-landing__aura"' in rendered
    assert 'class="grove-landing__aura-drift"' in rendered
    assert 'class="grove-landing__grain"' in rendered
    # Motion is compositor-only and stops under reduced motion.
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".grove-landing__aura-drift::before" in reduced
    assert ".grove-landing__grain" in reduced
    # The accent word carries the underline, not the whole headline.
    assert 'One software <span class="grove-landing__mark">factory</span>.' in rendered


def test_landing_background_and_aura_settle_to_neutral_black() -> None:
    """The aura, CSS and WebGL must agree where the coloured field ends."""
    assert max(FLOOR) <= 6 and len(set(FLOOR)) == 1
    black = "".join(f"{channel:02x}" for channel in FLOOR)
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    scene = scene_source()
    assert f"--factory-night: #{black}" in css
    assert f"night: 0x{black}" in scene
    image = AuraField.render()
    bottom = [image.getpixel((x, image.height - 1)) for x in range(image.width)]
    assert all(
        max(abs(channel - floor) for channel, floor in zip(pixel, FLOOR, strict=True)) <= 1
        for pixel in bottom
    )
    grain = css.split(".grove-landing__grain {", 1)[1].split("}", 1)[0]
    opacity = re.search(r"opacity:\s*([\d.]+)", grain)
    assert opacity is not None and float(opacity.group(1)) <= 0.02


def test_landing_controls_share_light_text_on_machined_dark_surfaces() -> None:
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    assert "--factory-control:" in css
    assert "--factory-control-edge:" in css
    assert "--factory-control-height: 2rem;" in css
    assert "font-size: var(--factory-control-font-size);" in css
    assert "color: #1c0c08" not in css
    assert "border-radius: 999px" not in css
    assert ".grove-landing__button--primary::before" in css
    assert ".grove-landing__nav-cta::before" in css
    assert ":focus-visible" in css
    assert ".grove-landing__support a::after" in css
    frame = css.split(".grove-landing__support a {", 1)[1].split("}", 1)[0]
    artwork = css.split(".grove-landing__support img {", 1)[1].split("}", 1)[0]
    assert "padding: 0;" in frame
    assert "object-fit: cover;" in artwork
    source = (DOCS / "index.md").read_text()
    assert source.count('<span class="grove-landing__button-icon">') == source.count("<svg") > 0
    assert "Documentation</a>" in source
    assert (
        source.count('class="grove-landing__support-window"')
        == source.count("--support-surface:")
        == 6
    )
    assert "width: 112%;" in artwork and "max-width: none;" in artwork
    assert "border: 2px solid var(--factory-control-edge);" in css
    assert all("inset" not in shadow for shadow in re.findall(r"box-shadow:\s*([^;]+)", css))
    assert "align-self: stretch;" in css


def test_landing_mesh_is_subtle_decorative_and_respects_reduced_motion() -> None:
    source = (DOCS / "index.md").read_text()
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    assert 'class="grove-landing__mesh" aria-hidden="true"' in source
    asset = DOCS / "img" / "landing" / "factory-mesh.svg"
    assert asset.is_file()
    svg = ElementTree.parse(asset).getroot()
    assert svg.attrib["viewBox"] == "0 0 1600 1000"
    rows = svg.findall(".//{http://www.w3.org/2000/svg}path[@class='mesh-row']")
    assert len(rows) >= 16
    # Each row arrives at the next row's original position. The boundary
    # rows are masked, so the whole surface wraps without a direction change.
    for row, following in pairwise(rows):
        assert row.attrib["style"] == f"--next-row: path('{following.attrib['d']}')"
    motion = svg.find("{http://www.w3.org/2000/svg}style")
    assert motion is not None and motion.text is not None
    # The flow keeps its tuned pace, but every animation step repaints this
    # whole full-viewport document, so each is stepped at a bounded rate: a
    # `linear` curve here repaints on every display refresh.
    for name, period in (("mesh-row-flow", 19.2), ("mesh-stream-flow", 16)):
        step = re.search(rf"{name} {period}s steps\((\d+)\) infinite", motion.text)
        assert step is not None, name
        assert int(step.group(1)) / period <= 12, f"{name} repaints faster than 12 Hz"
    # The edge fade is the page's composited CSS mask, never an SVG mask that
    # is repainted with every step.
    assert svg.find(".//{http://www.w3.org/2000/svg}mask") is None
    assert len(svg.findall(".//{http://www.w3.org/2000/svg}path[@class='mesh-stream']")) >= 5
    assert '<object data="img/landing/factory-mesh.svg?v=4"' in source
    assert svg.attrib["preserveAspectRatio"] == "xMidYMid slice"
    assert "alternate" not in motion.text
    assert "prefers-reduced-motion: reduce" in motion.text
    assert "animation: none" in motion.text
    mesh = css.split(".grove-landing__mesh {", 1)[1].split("}", 1)[0]
    opacity = re.search(r"opacity:\s*([\d.]+)", mesh)
    assert opacity is not None and float(opacity.group(1)) <= 0.16
    assert 'url("../img/landing/factory-mesh.svg")' not in mesh
    assert ".grove-landing__mesh object" in css
    assert "pointer-events: none" in mesh
    assert "grove-mesh-flow" not in css


def test_landing_typography_uses_the_themes_current_hero_roles() -> None:
    """Use the theme's loaded families and its display face's real weight."""
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    heading = re.search(r"\.grove-landing__copy h1\s*\{[^}]*font-family:[^}]+\}", css)
    assert heading is not None
    assert "font-family: var(--font-display);" in heading.group()
    assert "font-weight: 400;" in heading.group()
    assert "font-synthesis: none;" in heading.group()
    paragraph = css.split(".grove-landing__copy p {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--font-body);" in paragraph
    assert "font-family: var(--font-sans);" in css
    assert ".grove-landing__nav .grove-landing__nav-link { display: none; }" in css
    assert "IBM Plex Serif" not in css


@pytest.mark.parametrize("control", ["nav-link", "nav-pill", "nav-cta", "button"])
def test_landing_control_typography_matches_navigation(control: str) -> None:
    """A boxed action must not silently switch from the navigation's display role."""
    css = (DOCS / "stylesheets" / "grove-landing.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    declarations = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if f".grove-landing__{control}" in {s.strip() for s in selectors.split(",")}:
            declarations.update(re.findall(r"([\w-]+):\s*([^;]+);", body))
    assert declarations["font-family"] == "var(--font-display)"
    assert declarations["font-size"] == "var(--factory-control-font-size)"
    assert declarations["font-weight"] == "400"
    assert declarations["font-synthesis"] == "none"
    assert declarations["line-height"] == "var(--leading-ui)"
    assert declarations["letter-spacing"] == "normal"


def test_landing_aura_is_a_solar_ramp_that_only_darkens() -> None:
    """The aura walks sun white through amber and red to burgundy, never back.

    A ramp that brightens mid-way reads as a random orange-to-yellow flicker.
    Luminance must fall monotonically along every profile, the band must pass
    through several distinct hues rather than two, and the hot corner must
    give way to the floor well before mid-field so the glow keeps a tight radius.
    """

    def luminance(stop: str) -> float:
        red, green, blue = AuraField._rgb(stop)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    for name, profile in (("band", BAND), ("left", LEFT), ("right", RIGHT)):
        values = [luminance(stop) for stop in profile]
        assert all(a >= b for a, b in pairwise(values)), name

    # Sun white, solar yellow, amber, sun orange, sun red, burgundy: distinct hues.
    hues = {round(AuraField.hue(stop) / 6) for stop in BAND[:14]}
    assert len(hues) >= 5, sorted(hues)
    # Tight radius: half way across the band the field is already burgundy dark.
    assert luminance(BAND[len(BAND) // 2]) < 40
    assert AuraField.strength(LEFT, 0.45) < 0.25


def test_home_and_overview_keep_the_shared_readme_pitch() -> None:
    """Both docs pages carry the README's pitch, byte-identical bar link depth."""
    readme_source = (ROOT / "README.md").read_text()
    readme_overview = readme_source.split("## 🌳 Overview", 1)[1].split("##", 1)[0]

    destinations = {
        "features-containers.md": "features-containers/",
        "features-attachments.md": "features-attachments/",
        "features-diagrams.md": "features-diagrams/",
        "issue-ops.md": "issue-ops/",
    }
    for destination in destinations.values():
        readme_overview = readme_overview.replace(
            f"https://factory.mewbo.com/latest/{destination}", destination
        )

    # Both pages link in markdown form, which mkdocs resolves per page depth.
    for page in ("home.md",):
        source = (DOCS / page).read_text()
        pitch = source.split("## What is Grove?", 1)[1]
        pitch = pitch.split("## Choose your surface", 1)[0]
        pitch = pitch.split("}\n", 1)[1]
        for source_path, destination in destinations.items():
            pitch = pitch.replace(source_path, destination)
        assert pitch.strip() == readme_overview.strip(), page
