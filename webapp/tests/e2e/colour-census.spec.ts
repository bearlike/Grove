import { expect, test } from "@playwright/test";

import { openSharpSurface } from "./sharp-surface-probe";

type PaletteClass = {
  readonly tag: string;
  readonly slot: string | null;
  readonly testId: string | null;
  readonly className: string;
  readonly forbidden: readonly string[];
};

function describe({ tag, slot, testId, className, forbidden }: PaletteClass): string {
  return [
    tag,
    slot && `data-slot=${slot}`,
    testId && `data-testid=${testId}`,
    `class=${JSON.stringify(className)}`,
    `forbidden=${forbidden.join(", ")}`,
  ]
    .filter(Boolean)
    .join("; ");
}

test.describe("workspace colour census", () => {
  for (const theme of ["light", "dark"] as const) {
    for (const tab of ["info", "controls"] as const) {
      test(`renders no raw palette utility outside charts in ${tab}, ${theme}`, async ({ page }) => {
        await openSharpSurface(page, tab, 540, theme);

      const measurements = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("[class]")]
          .filter((element) => {
            const style = getComputedStyle(element);
            return (
              element.getClientRects().length > 0 &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              element.closest('[data-slot="chart"]') === null
            );
          })
          .map((element) => {
            const className = element.getAttribute("class") ?? "";
            return {
              tag: element.tagName.toLowerCase(),
              slot: element.dataset.slot ?? null,
              testId: element.dataset.testid ?? null,
              className,
              forbidden: className
                .split(/\s+/)
                .filter((className) => /(?:^|:)(?:bg|text|border|ring|fill|stroke|from|via|to|outline|decoration)-(?:emerald|blue|amber|red)-/.test(className)),
            };
          }),
      );

      expect(measurements.length, "the colour census must inspect a live workspace").toBeGreaterThan(100);
      const paletteClasses = measurements.filter(({ forbidden }) => forbidden.length > 0);
        expect(paletteClasses.map(describe).join("\n")).toBe("");
      });
    }
  }
});
