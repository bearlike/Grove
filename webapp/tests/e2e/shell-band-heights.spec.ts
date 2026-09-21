import { expect, test } from "@playwright/test";

import { dp } from "./density";
import { openSharpSurface } from "./sharp-surface-probe";

const BAND_HEIGHT = 32;

/**
 * The ramp's floor step, AT THE DENSITY ROOT — `text-xs` is 12px written
 * against a 16px root and renders at 9.6px here.
 *
 * It used to be a flat 12, which these bands only met because each carried an
 * inline `max(12px, …)` override. Measured on the built app that override was
 * the defect rather than the guard: it held the chrome at 12px while the
 * terminal output and transcript prose it frames rendered 9.6-11.2px, so the
 * frame outweighed the picture. Expressed through `dp` the assertion still
 * catches a band that drops BELOW the ramp, which is what it was for, without
 * demanding the chrome opt out of the density lever every other surface obeys.
 */
const MINIMUM_TEXT_SIZE = dp(12);

test("workspace chrome closes the shell, work strip, and active tab sub-bar as 32px bands", async ({ page }) => {
  await openSharpSurface(page, "info", 540, "light");
  await page.getByTestId("work-panel-tab-terminal").click();
  await expect(page.getByTestId("terminal-tab")).toBeVisible();

  const layout = await page.evaluate(() => {
    const header = document.querySelector<HTMLElement>("[data-testid='shell-header']");
    const paneTabs = document.querySelector<HTMLElement>("[aria-label='Workspace panes']");
    const workTabs = document.querySelector<HTMLElement>(
      "[data-testid='work-panel'] > .workspace-tab-strip",
    );
    const terminal = document.querySelector<HTMLElement>(
      "[data-testid='terminal-tab'] > div:first-child",
    );
    const rail = document.querySelector<HTMLElement>("[data-testid='app-sidebar']");
    const resizer = document.querySelector<HTMLElement>(
      "[data-slot='resizable-handle']",
    );
    const split = document.querySelector<HTMLElement>(
      "[data-slot='resizable-panel-group']",
    );
    if (!header || !paneTabs || !workTabs || !terminal || !rail || !resizer || !split) {
      throw new Error("workspace shell census fixtures are absent");
    }
    const measureBand = (name: string, element: HTMLElement) => {
      const box = element.getBoundingClientRect();
      return {
        name,
        box: { y: box.y, height: box.height },
        // VISIBLE text only. `sr-only` clips a label to a 1px box that still
        // reports client rects and a font size, so an accessible name for an
        // icon button was being measured as though a reader could see it —
        // which would let a genuinely unreadable visible label pass behind it.
        textSizes: [...element.querySelectorAll<HTMLElement>("*")]
          .filter((child) => {
            if (!child.textContent?.trim()) return false;
            const box = child.getBoundingClientRect();
            return box.width > 1 && box.height > 1;
          })
          .map((child) => Number.parseFloat(getComputedStyle(child).fontSize)),
        borderBottomWidth: Number.parseFloat(getComputedStyle(element).borderBottomWidth),
      };
    };
    const paneBox = paneTabs.getBoundingClientRect();
    const headerBox = header.getBoundingClientRect();
    return {
      bands: [
        measureBand("shell header", header),
        measureBand("work tab strip", workTabs),
        measureBand("terminal sub-bar", terminal),
      ],
      paneInsideHeader:
        paneBox.y >= headerBox.y &&
        paneBox.y + paneBox.height <= headerBox.y + headerBox.height,
      railBorderRight: Number.parseFloat(getComputedStyle(rail).borderRightWidth),
      resizer: {
        box: (() => {
          const box = resizer.getBoundingClientRect();
          return { y: box.y, height: box.height, width: box.width };
        })(),
        background: getComputedStyle(resizer).backgroundColor,
        split: (() => {
          // The split handle belongs to the workspace pane group, below the
          // 32px shell header. `annotation-split` wraps that group and begins
          // at the viewport top, so it cannot be the handle's geometry peer.
          const box = resizer.parentElement!.getBoundingClientRect();
          return { y: box.y, height: box.height };
        })(),
      },
    };
  });

  expect(layout.paneInsideHeader).toBe(true);
  expect(layout.bands).toHaveLength(3);
  for (const band of layout.bands) {
    expect(band.box.height, band.name).toBeCloseTo(BAND_HEIGHT, 0);
    expect(band.borderBottomWidth, `${band.name} bottom rule`).toBe(1);
    expect(band.textSizes.length, `${band.name} must contain text`).toBeGreaterThan(0);
    expect(Math.min(...band.textSizes), `${band.name} text floor`).toBeGreaterThanOrEqual(
      MINIMUM_TEXT_SIZE,
    );
  }
  for (let index = 1; index < layout.bands.length; index += 1) {
    const above = layout.bands[index - 1]!;
    const below = layout.bands[index]!;
    expect(below.box.y - (above.box.y + above.box.height)).toBeCloseTo(0, 0);
  }

  expect(layout.railBorderRight).toBe(1);
  expect(layout.resizer.box.width).toBeCloseTo(1, 0);
  expect(layout.resizer.box.y).toBeCloseTo(layout.resizer.split.y, 0);
  expect(layout.resizer.box.height).toBeCloseTo(layout.resizer.split.height, 0);
  expect(layout.resizer.background).not.toBe("rgba(0, 0, 0, 0)");
});
