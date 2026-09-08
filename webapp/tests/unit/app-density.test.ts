import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The app's density is ONE root declaration, and these pin the properties that
 * make it safe rather than the number itself.
 *
 * Measured off two captures of the same screen and the same workspace
 * (2540x1238): every dimension of the denser reference — rail, type, controls,
 * icons, gutters — is 0.8 of the other. That is a proportional zoom-out, so the
 * root rem is the only lever that reaches all of it, including the vendored
 * component tree this app may not edit and Tailwind v4's rem-derived spacing.
 *
 * A source census, because CSS custom properties and a media query have no
 * runtime artifact to inspect outside a browser: the browser suite is where the
 * resulting geometry is measured, and this is what stops the mechanism being
 * swapped for one that breaks zoom.
 */
const CSS = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

describe("app density", () => {
  it("scales the ROOT, so type and geometry move together", () => {
    expect(CSS).toMatch(/html\s*\{\s*font-size:\s*80%;\s*\}/);
  });

  it("expresses it as a PERCENTAGE, never a px value", () => {
    // A px root discards the reader's own browser font-size setting outright;
    // a percentage is 80% of whatever they chose, so a 20px default still
    // yields 16px here.
    expect(CSS).not.toMatch(/html\s*\{\s*font-size:\s*\d+(\.\d+)?px/);
  });

  it("stands down for a coarse pointer, where a finger sets the floor", () => {
    expect(CSS).toMatch(/@media\s*\(pointer:\s*coarse\)\s*\{\s*html\s*\{\s*font-size:\s*100%/);
  });

  it("leaves every type step in rem, so browser zoom still scales it", () => {
    const ramp = CSS.match(/--text-(xs|sm|base|lg|xl|2xl|3xl):\s*[^;]+;/g) ?? [];
    expect(ramp.length).toBeGreaterThan(5);
    for (const step of ramp) expect(step).toContain("rem");
  });

  it("never locks text scaling", () => {
    // `text-size-adjust: none` would stop a reader zooming out of this at all,
    // which is the one thing a density change must not buy itself.
    expect(CSS).not.toContain("text-size-adjust: none");
  });
});
