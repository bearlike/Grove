import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

/*
 * Contrast contract for the neutral + terracotta ramp (design-direction.md
 * §4.4). Mirrors the status-tokens.test.ts drift-test
 * pattern: read the REAL tokens out of globals.css and assert a computed
 * WCAG guarantee, so a future token edit that quietly lightens a value below
 * floor fails the build instead of an eyeballed launch-day audit.
 *
 * Scope: only the free-to-retheme neutral + brand tokens. The frozen
 * --status, --agent, and --ref prefixed hues are Python-mirrored and owned
 * by status-tokens.test.ts — this file doesn't touch them.
 *
 * Floors: AA 4.5:1 for ALL text (no large-text discount for 12-13px
 * metadata), AAA 7:1 for chat prose, 3:1 for non-text indicators
 * (focus ring, CTA fill boundary).
 */

const GLOBALS_CSS = path.resolve(__dirname, "../../app/globals.css");

// --- WCAG 2.1 math: HSL triplet -> relative luminance -> contrast ratio -----
function hslToLuminance(h: number, s: number, l: number): number {
  s /= 100;
  l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(f(0)) + 0.7152 * lin(f(8)) + 0.0722 * lin(f(4));
}

function ratio(a: number, b: number): number {
  const hi = Math.max(a, b);
  const lo = Math.min(a, b);
  return (hi + 0.05) / (lo + 0.05);
}

// --- parse the :root / .dark HSL triplets out of the real stylesheet -------
type Luminances = Record<string, number>;

function parseTheme(css: string, selector: string): Luminances {
  const block = css.match(new RegExp(`${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`));
  if (!block) throw new Error(`no ${selector} block found in globals.css`);
  const out: Luminances = {};
  // Matches `--token: H S% L%` triplets only — hex tokens (the frozen
  // --status/--agent/--ref vars) and rgba() (--scrollbar-thumb) don't match
  // this pattern and are silently skipped, which is what we want here.
  const re = /--([a-z][\w-]*)\s*:\s*(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%/g;
  for (const m of block[1].matchAll(re)) {
    out[m[1]] = hslToLuminance(parseFloat(m[2]), parseFloat(m[3]), parseFloat(m[4]));
  }
  return out;
}

const css = readFileSync(GLOBALS_CSS, "utf8");
const THEMES: Record<string, Luminances> = {
  light: parseTheme(css, ":root"),
  dark: parseTheme(css, ".dark"),
};

// --- the contract (explicit pair lists, per design-direction.md §4.4) ------
const TEXT_SURFACES = ["background", "sidebar", "card", "elevated", "muted"];
const PROSE_SURFACES = ["background", "card", "elevated", "muted"];

// [textToken, surfaces, floor, label]
const TEXT_AA: [string, string[], number, string][] = [
  ["foreground", TEXT_SURFACES, 4.5, "body/prose"],
  ["muted-foreground", TEXT_SURFACES, 4.5, "12-13px meta/label/placeholder (danger zone)"],
  ["code", ["code-well"], 4.5, "code on code-well"],
  ["primary-fg", ["background", "card"], 4.5, "terracotta text (you/link)"],
];
const PROSE_AAA: [string, string[], number, string][] = [
  ["foreground", PROSE_SURFACES, 7.0, "chat prose (read for minutes -> AAA)"],
];
// [fg, bg, floor, label]
const CTA: [string, string, number, string][] = [
  ["primary-foreground", "primary-strong", 4.5, "white label on CTA fill"],
];
const NONTEXT: [string, string, number, string][] = [
  ["ring", "background", 3.0, "focus ring"],
  ["primary-strong", "background", 3.0, "CTA fill vs canvas (component boundary)"],
];

describe.each(Object.entries(THEMES))("contrast contract — %s theme", (theme, L) => {
  it.each(TEXT_AA)("AA >= 4.5 · %s text on its surfaces (%s)", (tok, surfaces, floor) => {
    for (const s of surfaces) {
      expect(L[tok], `${theme}: token --${tok} not found in globals.css`).toBeDefined();
      expect(L[s], `${theme}: token --${s} not found in globals.css`).toBeDefined();
      expect(ratio(L[tok], L[s]), `${theme}: --${tok} on --${s}`).toBeGreaterThanOrEqual(floor);
    }
  });

  it.each(PROSE_AAA)("AAA >= 7 · %s (%s)", (tok, surfaces, floor) => {
    for (const s of surfaces) {
      expect(ratio(L[tok], L[s]), `${theme}: --${tok} on --${s}`).toBeGreaterThanOrEqual(floor);
    }
  });

  it.each(CTA)("AA >= 4.5 · %s on %s (%s)", (fg, bg, floor) => {
    expect(ratio(L[fg], L[bg]), `${theme}: --${fg} on --${bg}`).toBeGreaterThanOrEqual(floor);
  });

  it.each(NONTEXT)("non-text >= 3 · %s on %s (%s)", (fg, bg, floor) => {
    expect(ratio(L[fg], L[bg]), `${theme}: --${fg} on --${bg}`).toBeGreaterThanOrEqual(floor);
  });
});
