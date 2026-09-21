import re
from pathlib import Path
from xml.etree import ElementTree

import yaml

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"


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
    source = (DOCS / "index.md").read_text()
    script = DOCS / "javascripts" / "grove-factory.js"
    runtime = DOCS / "javascripts" / "vendor" / "three.module.js"

    assert '<script type="module" src="javascripts/grove-factory.js"></script>' in source
    assert script.is_file()
    assert runtime.is_file()
    core_runtime = runtime.parent / "three.core.js"
    assert core_runtime.is_file()
    assert 'from "./vendor/three.module.js"' in script.read_text()
    assert "from './three.core.js'" in runtime.read_text()


def test_agent_display_glyphs_are_local_transparent_brand_coloured_paths() -> None:
    """Each face glyph carries its own brand colour on a transparent field.

    The emissive material multiplies a white emissive by this texture, so the
    fill here is what the viewer actually sees lit on the robot's display; a
    white fill would render all three agents identically.
    """
    source = (DOCS / "javascripts" / "grove-factory.js").read_text()
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
    source = (DOCS / "javascripts" / "grove-factory.js").read_text()
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
            f"https://bearlike.github.io/Grove/latest/{destination}", destination
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
