import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

const theme = (selector: string) => css.slice(css.indexOf(`${selector} {`)).split("\n}")[0];

const outlinedControl = ':is(button, a, [role="button"])[data-variant="outline"]';

/**
 * Reads an OKLCH token's lightness. The tokens this file guards are a TIER
 * RELATIONSHIP, not three magic numbers: pinning the literals meant every
 * retune failed here as if it were a regression, which is how a test stops
 * being a contract and becomes a copy of the file next to it.
 */
describe("subtle outlined control chrome", () => {
  // The resting-vs-focus tier relationship is pinned in `control-edge-tokens.test.ts`
  // and is deliberately not restated here — two copies of one contract disagree
  // the first time either is edited.
  it("covers tooltip-wrapped outlined controls without weakening focus or invalid states", () => {
    expect(css).toContain(`${outlinedControl}:not(:focus-visible):not([aria-invalid="true"])`);
    expect(css).not.toContain('[data-slot="button"][data-variant="outline"]');
  });

  it("removes the native outline shadow at its semantic seam", () => {
    expect(css).toContain(`${outlinedControl} {\n  box-shadow: none;\n}`);
  });

  it("gives the composer's focused border a visible ring without an outer frame", () => {
    const composerFocus = css.slice(
      css.indexOf('[data-slot="composer-bar"]:has([data-slot="textarea"]:focus-visible)'),
      css.indexOf("/* Placeholder contrast"),
    );

    expect(composerFocus).toContain("border-color: var(--ring);");
    expect(composerFocus).not.toContain("outline:");
  });

  it("drops the resting edge of header-band controls but keeps their focus ring", () => {
    const header = '.workspace-header :is(button, a)[data-variant="outline"]:not(:focus-visible):not([aria-invalid="true"])';
    expect(css).toContain(`${header} {\n  border-color: transparent;\n}`);
  });
});
