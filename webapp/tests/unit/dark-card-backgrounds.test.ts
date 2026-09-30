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

/** The fills before the work panel and rail were quietened. The ladder's page
 * rungs sit at 75% of this luminance; the CARD tier — the card body, its header
 * band and the washes inside it — went to 65% of that again, because card
 * structure is meant to recede into the page. `surface-sunken` is absent on
 * purpose: it is held at the 0.05 L floor below `base`, pinned below. */
const previous = {
  "surface-base": [0.155, 0.005, 286],
  "surface-overlay": [0.285, 0.006, 286],
};
const CARD_TIER = 0.75 * 0.65;
const cardTier = {
  "surface-header-start": [0.303142, 0.006, 286],
  "surface-header-end": [0.255779, 0.006, 286],
  "surface-header-highlight": [0.350505, 0.006, 286],
  "surface-raised": [0.213152, 0.006, 286],
  muted: [0.274, 0.006, 286.033],
  accent: [0.274, 0.006, 286.033],
  secondary: [0.274, 0.006, 286.033],
};

describe("dark card backgrounds", () => {
  for (const [name, value] of Object.entries(previous)) {
    it(`${name} is 25% darker by WCAG relative luminance`, () => {
      expect(token(name) / luminance(value)).toBeCloseTo(0.75, 2);
    });
  }

  for (const [name, value] of Object.entries(cardTier)) {
    it(`${name} recedes to the card tier's share of its original luminance`, () => {
      expect(token(name) / luminance(value)).toBeCloseTo(CARD_TIER, 2);
    });
  }

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

  it("foreground clears AA on the header highlight that the loader crosses", () => {
    expect(contrast(token("foreground"), token("surface-header-highlight"))).toBeGreaterThanOrEqual(4.5);
  });

  const lightness = (name: string) =>
    Number(dark.match(new RegExp(`--${name}: oklch\\(([^)]+)\\)`))?.[1].split(/\s+/)[0]);

  for (const [lower, upper] of [
    ["surface-sunken", "surface-base"],
    ["surface-raised", "surface-overlay"],
  ]) {
    it(`retains the ${lower} to ${upper} lightness floor`, () => {
      expect(lightness(upper) - lightness(lower)).toBeGreaterThanOrEqual(0.05);
    });
  }

  // Under the floor BY DECISION: a card blends toward its page and its edge
  // carries the boundary. It must still rise, never tie or sink.
  it("keeps a card above its page without the full floor", () => {
    const step = lightness("surface-raised") - lightness("surface-base");
    expect(step).toBeGreaterThan(0.02);
    expect(step).toBeLessThan(0.05);
  });
});
