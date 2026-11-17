import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
const dark = css.slice(css.indexOf(".dark {")).split("\n}")[0];

/** WCAG uses linear sRGB luminance, not OKLCH lightness. Clamp out-of-gamut channels. */
function luminance([lightness, chroma, hue]: number[]): number {
  const a = chroma * Math.cos(hue * Math.PI / 180);
  const b = chroma * Math.sin(hue * Math.PI / 180);
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const channels = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((value) => Math.min(1, Math.max(0, value)));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function token(name: string): number {
  const match = dark.match(new RegExp(`--${name}: oklch\\(([^)]+)\\)`));
  if (!match) throw new Error(`No literal dark token --${name}`);
  return luminance(match[1].split(/\s+/).map(Number));
}

function contrast(a: number, b: number): number {
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/** sRGB → WCAG relative luminance, for the reference's sampled pixels. */
function srgbLuminance(value: number): number {
  const s = value / 255;
  const y = s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  return y;
}

/** The planes sampled off the ChatGPT reference screenshots (2026-10-17):
 * rail #000, page #0e0e0e, card and composer bar #212121. The dark ladder IS
 * these planes — `sunken` the rail, `base` the page, `raised` the card. */
const reference = {
  "surface-sunken": 0,
  "surface-base": 14,
  "surface-raised": 33,
};

describe("dark card backgrounds", () => {
  for (const [name, grey] of Object.entries(reference)) {
    it(`${name} matches the reference plane #${grey.toString(16).padStart(2, "0").repeat(3)}`, () => {
      expect(token(name)).toBeCloseTo(srgbLuminance(grey), 3);
    });
  }

  it("the rail is the sunken rung, so it is the reference's black", () => {
    expect(dark).toMatch(/--sidebar: var\(--surface-sunken\);/);
  });

  for (const surface of [
    "surface-sunken",
    "surface-base",
    "surface-header-start",
    "surface-header-end",
    "surface-raised",
    "surface-overlay",
    "muted",
  ]) {
    for (const text of ["foreground", "content-secondary", "muted-foreground"]) {
      it(`${text} clears AA on ${surface}`, () => {
        expect(contrast(token(text), token(surface))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  // AA is the legal floor, not the look. Dark text read dull at tertiary 7.6:1
  // on the page, so each tier now has a CRISPNESS floor on the page itself,
  // and the three stay separated so the lift never flattens reading order.
  // Pure white on the reference's #0e0e0e page is 18.4:1 — that is the ceiling
  // the primary tier can reach there, so its floor sits just under it.
  const crisp = { foreground: 18, "content-secondary": 13, "muted-foreground": 9 };
  for (const [text, floor] of Object.entries(crisp)) {
    it(`${text} reads crisp on the page, not merely legal`, () => {
      expect(contrast(token(text), token("surface-base"))).toBeGreaterThanOrEqual(floor);
    });
  }

  it("keeps the three text tiers distinct after the lift", () => {
    const on = (text: string) => contrast(token(text), token("surface-base"));
    expect(on("foreground") - on("content-secondary")).toBeGreaterThan(3);
    expect(on("content-secondary") - on("muted-foreground")).toBeGreaterThan(3);
  });

  it("foreground clears AA on the header highlight that the loader crosses", () => {
    expect(contrast(token("foreground"), token("surface-header-highlight"))).toBeGreaterThanOrEqual(4.5);
  });

  const lightness = (name: string) =>
    Number(dark.match(new RegExp(`--${name}: oklch\\(([^)]+)\\)`))?.[1].split(/\s+/)[0]);

  // Every step clears the §4.4 floor now: the reference lifts its cards and
  // composer plainly off the page, so no rung blends into its neighbour.
  for (const [lower, upper] of [
    ["surface-sunken", "surface-base"],
    ["surface-base", "surface-raised"],
    ["surface-raised", "surface-overlay"],
  ]) {
    it(`retains the ${lower} to ${upper} lightness floor`, () => {
      expect(lightness(upper) - lightness(lower)).toBeGreaterThanOrEqual(0.05);
    });
  }
});
