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
              element.closest('[data-slot="chart"]') === null &&
              // `elements-agent-handoff` bakes its unsettled arrow/recipient
              // pill as literal `text-blue-*`/`bg-blue-*` JSX — Grove ALWAYS
              // renders `settled={false}` (agent-message.tsx: the `true`
              // branch compounds the sender pill's dim to 1.78:1, well under
              // the 4.5:1 floor) and `className` only reaches the vendored
              // root, never these two children. Overriding the RENDERED
              // colour is real (globals.css's `.handoff-agent` scope), but
              // the vendored markup itself is `registry:check`-frozen and a
              // class census can only ever see the source it emits, not what
              // a later rule paints over it. Same shape as the terminal/Files
              // exemptions design-system.md §"Two surfaces are legitimately
              // exempt" already documents; this is the third.
              element.closest('[data-slot="agent-handoff"]') === null
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
