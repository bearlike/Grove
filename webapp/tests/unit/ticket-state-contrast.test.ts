import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

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

function token(block: string, name: string): number {
  const match = block.match(new RegExp(`--${name}: oklch\\(([^)]+)\\)`));
  if (!match) throw new Error(`No literal token --${name} in tested theme`);
  return luminance(match[1].split(/\s+/).map(Number));
}

function contrast(a: number, b: number): number {
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/**
 * A TRACKER STATE IS COLOURED BODY TEXT, NOT A BADGE.
 *
 * The ticket row puts its word directly on the card's raised surface, and the
 * same row can appear over base while its layout changes. These are the actual
 * rungs it reaches. `surface-sunken` is deliberately excluded: light merged
 * text measures 4.24:1 there, but no ticket row sits on that rung, so widening
 * this loop would demand a token change for an imaginary surface.
 */
describe("ticket tracker state words clear AA where ticket rows sit", () => {
  for (const theme of [":root", ".dark"]) {
    const block = css.slice(css.indexOf(`${theme} {`)).split("\n}")[0];
    for (const color of ["success", "destructive", "merged"]) {
      for (const surface of ["raised", "base"]) {
        it(`${theme} ${color} on ${surface} is at least 4.5:1`, () => {
          expect(contrast(token(block, color), token(block, `surface-${surface}`))).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
  }
});
