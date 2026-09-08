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

const previous = {
  "surface-header-start": [0.32, 0.006, 286],
  "surface-header-end": [0.27, 0.006, 286],
  "surface-header-highlight": [0.37, 0.006, 286],
  "surface-raised": [0.225, 0.006, 286],
};

describe("dark card backgrounds", () => {
  for (const [name, value] of Object.entries(previous)) {
    it(`${name} is 15% darker by WCAG relative luminance`, () => {
      expect(token(name) / luminance(value)).toBeCloseTo(0.85, 2);
    });
  }

  for (const surface of ["surface-header-start", "surface-header-end", "surface-raised"]) {
    for (const text of ["foreground", "content-secondary", "muted-foreground"]) {
      it(`${text} clears AA on ${surface}`, () => {
        expect(contrast(token(text), token(surface))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("foreground clears AA on the header highlight that the loader crosses", () => {
    expect(contrast(token("foreground"), token("surface-header-highlight"))).toBeGreaterThanOrEqual(4.5);
  });

  it("retains the base to raised lightness floor", () => {
    const base = dark.match(/--surface-base: oklch\(([^)]+)\)/)?.[1].split(/\s+/).map(Number)[0];
    const raised = dark.match(/--surface-raised: oklch\(([^)]+)\)/)?.[1].split(/\s+/).map(Number)[0];
    expect(raised).toBeDefined();
    expect(base).toBeDefined();
    expect(raised! - base!).toBeGreaterThanOrEqual(0.05);
  });
});
