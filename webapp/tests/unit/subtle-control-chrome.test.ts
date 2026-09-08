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

  it("keeps composer keyboard focus as an inset opaque ring edge", () => {
    const composerFocus = css.slice(
      css.indexOf('[data-slot="composer-bar"]:has([data-slot="textarea"]:focus-visible)'),
      css.indexOf("/* Placeholder contrast"),
    );

    expect(composerFocus).toContain("outline: 1px solid var(--ring);");
    expect(composerFocus).toContain("outline-offset: -1px;");
    expect(composerFocus).not.toContain("outline: 2px");
    expect(composerFocus).not.toContain("outline-offset: 2px");
  });
});
