import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppLogo } from "@/components/grove/app-logo";
import { BrandMark } from "@/components/grove/brand-mark";

/**
 * The logo renders as the WHEEL, and the app icon is what stands alone.
 *
 * The defect this pins shipped in the rail, the login page and the landing
 * page at once, and every gate stayed green through all of it: the mark is one
 * path whose spokes and rim are interior subpaths, so without
 * `fill-rule="evenodd"` each hole fills and the logo paints as a solid
 * octagon. There is no error, no type failure and no class to lint — the path
 * data is byte-identical to the correct artwork and only the FILL RULE differs,
 * which is why a source census over the `d` attribute would have passed
 * throughout. `app/icon.png` and friends were always right, so the app
 * disagreed with its own favicon.
 */
describe("BrandMark", () => {
  it("fills with the even-odd rule, so the wheel keeps its holes", () => {
    // The whole defect, in one attribute. React lowercases `fillRule` into the
    // SVG attribute on the way out, so this is the rendered output rather than
    // the prop spelling.
    const html = renderToStaticMarkup(<BrandMark />);
    expect(html).toContain('fill-rule="evenodd"');
  });

  it("agrees with the icon file it was traced from", () => {
    // The two are one picture with two consumers — the inline component and
    // the app icon pipeline's source — so a fill rule present in one and
    // absent in the other is exactly the drift that produced the bug. Pinned
    // as a cross-artifact census because neither file can state it alone.
    const icon = readFileSync("../docs/img/grove-logo.svg", "utf8");
    expect(icon).toContain('fill-rule="evenodd"');
  });

  it("carries the weight stroke, matching the artwork stroke for stroke", () => {
    // The linework is thin enough at small sizes that the mark needs a stroke
    // in its own fill colour to read, and the stroke is what makes it twice
    // the weight the raw path draws. It has the same drift exposure as the
    // fill rule above and none of its visibility: a component that lost the
    // stroke would render a THINNER logo beside app icons rasterized from an
    // SVG that kept it, which is a difference nobody spots in a diff. Same
    // cross-artifact census, for the same reason.
    const html = renderToStaticMarkup(<BrandMark />);
    expect(html).toContain('stroke="#c86e45"');
    expect(html).toContain('stroke-width="10"');
    expect(html).toContain('stroke-linejoin="round"');

    const icon = readFileSync("../docs/img/grove-logo.svg", "utf8");
    expect(icon).toContain('stroke="#c86e45"');
    expect(icon).toContain('stroke-width="10"');
    expect(icon).toContain('stroke-linejoin="round"');
    // The old artwork said `stroke="none"`, so its absence is half the change.
    expect(icon).not.toContain('stroke="none"');
  });

  it("keeps the brand fill hard-coded rather than inheriting currentColor", () => {
    // A mark that took `currentColor` would go grey inside a muted row and
    // invert in dark mode. This is the one colour in the app that is not a
    // theme token, deliberately.
    const html = renderToStaticMarkup(<BrandMark />);
    expect(html).toContain('fill="#c86e45"');
    expect(html).not.toContain("currentColor");
  });

  it("is decorative unless it stands alone", () => {
    expect(renderToStaticMarkup(<BrandMark />)).toContain('aria-hidden="true"');
    const labelled = renderToStaticMarkup(<BrandMark label="Grove" />);
    expect(labelled).toContain('aria-label="Grove"');
    expect(labelled).not.toContain("aria-hidden");
  });
});

describe("AppLogo", () => {
  it("draws the SHIPPED icon artwork, not a second copy of the tile", () => {
    // The tile's cream and its squircle belong to the icon pipeline. Pointing
    // at the same file every other icon surface uses is what keeps the landing
    // page, the dock and the browser tab showing one picture.
    const html = renderToStaticMarkup(<AppLogo />);
    expect(html).toContain('src="/icon-512.png"');
  });

  it("is decorative unless it stands alone", () => {
    // Same contract as the mark: an empty `alt` is what keeps a screen reader
    // from announcing the product name twice where a wordmark sits beside it.
    const html = renderToStaticMarkup(<AppLogo />);
    expect(html).toContain('alt=""');
    expect(html).toContain('aria-hidden="true"');
    const labelled = renderToStaticMarkup(<AppLogo label="Grove" />);
    expect(labelled).toContain('alt="Grove"');
    expect(labelled).not.toContain("aria-hidden");
  });
});
