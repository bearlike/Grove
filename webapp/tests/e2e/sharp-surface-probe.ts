import { expect, type Page, type TestInfo } from "@playwright/test";
import { FIXTURE_WORKSPACES } from "./_fixtures";

export const SHARP_SURFACE_WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;
type WorkTab = "info" | "controls";
type Theme = "light" | "dark";

/** Measure the pane, not the viewport: the split handle owns this width. */
export async function openSharpSurface(
  page: Page,
  tab: WorkTab,
  width: number,
  theme: Theme,
): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.emulateMedia({ colorScheme: theme });
  await page.goto(`/w/${SHARP_SURFACE_WORKSPACE_ID}`);
  await page.evaluate((dark) => document.documentElement.classList.toggle("dark", dark), theme === "dark");
  await page.getByTestId("pane-split").click();
  const panel = page.getByTestId("work-panel");
  const handle = page.locator('[data-slot="resizable-handle"]');
  await expect(panel).toBeVisible();
  const box = (await handle.boundingBox())!;
  const initial = (await panel.boundingBox())!.width;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + initial - Math.min(width, 540), box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.getByTestId(`work-panel-tab-${tab}`).click();
  await page.getByTestId(`${tab}-tab`).waitFor({ state: "visible" });
  await page.evaluate(async () => { await document.fonts.ready; });
  await expect.poll(async () => Math.abs((await panel.boundingBox())!.width - Math.min(width, 540))).toBeLessThan(2);
}

export async function captureSharpSurface(
  page: Page, testInfo: TestInfo, tab: WorkTab, width: number, theme: Theme,
): Promise<void> {
  await page.screenshot({ path: testInfo.outputPath(`sharp-${tab}-${width}-${theme}.png`) });
}

/**
 * How far the send control sits in from the bar's bottom-right corner: the
 * vendored `ComposerBar`'s own padding plus its 1px border, read from the page
 * rather than restated, so the specs pin "send sits in the corner" without
 * owning a number the vendor decides.
 */
export async function barInset(page: Page): Promise<number> {
  return page.locator('[data-slot="composer-bar"]').first().evaluate((bar) => {
    const style = getComputedStyle(bar);
    return Number.parseFloat(style.paddingRight) + Number.parseFloat(style.borderRightWidth);
  });
}
