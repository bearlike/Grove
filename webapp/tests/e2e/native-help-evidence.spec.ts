import { expect, test } from "@playwright/test";

import { dp } from "./density";
import { openSharpSurface } from "./sharp-surface-probe";

/**
 * The visual record for the native-help and compact-typography corrections,
 * captured against the FAKE DAEMON's fixtures so nothing in frame is a real
 * host path, repo name or workspace id.
 *
 * These assert as well as capture. A screenshot nobody compares is decoration;
 * the assertions beside each one are what actually fail if the surface
 * regresses, and the image is what a human reads to judge whether the result
 * is any good — which is the half no assertion can cover.
 */
test.describe("native help and compact chrome", () => {
  for (const theme of ["dark", "light"] as const) {
    test(`help reads as an icon, not a typed glyph — ${theme}`, async ({ page }, testInfo) => {
      await page.route("**/api/grove/share-policy**", (route) =>
        route.fulfill({ json: { ttl_seconds: 604800, passcode_set: false } }),
      );
      await openSharpSurface(page, "controls", 540, theme);

      const help = page.locator('button[aria-label^="About "]');
      await expect(help.first()).toBeVisible();
      const count = await help.count();
      expect(count).toBeGreaterThan(4);

      for (let index = 0; index < count; index += 1) {
        const trigger = help.nth(index);
        // A real SVG, never a string. `(i)` is what shipped and what this pins.
        await expect(trigger.locator("svg")).toHaveCount(1);
        await expect(trigger).not.toContainText("(i)");
        // Quiet mark, undiminished target: 24px stands whatever the glyph does.
        const box = await trigger.boundingBox();
        expect(box!.height, await trigger.getAttribute("aria-label") ?? "").toBeGreaterThanOrEqual(24);
        expect(box!.width).toBeGreaterThanOrEqual(24);
      }

      // THE LABEL KEEPS ITS WORD. `Link expiry` shipped as `Link exp…` for one
      // pass: the shared row truncated, and a `CardFields` label column is
      // auto-width, so truncating destroyed the name without saving any space.
      // Found by looking at the built surface — no assertion here saw it, which
      // is why this one exists.
      for (const label of ["Link expiry", "Passcode", "Project policy"]) {
        await expect(
          page.getByText(label, { exact: true }).first(),
          `${label} must not be clipped`,
        ).toBeVisible();
      }

      await page.screenshot({ path: testInfo.outputPath(`help-${theme}.png`) });
    });
  }

  /**
   * The screenshot's headline defect: three equal columns gave a 32-character
   * workspace id a third of the card and broke `grove attach <id>` over four
   * lines. One line at every width is the acceptance criterion, and the copied
   * bytes must equal the displayed command exactly — never a wrapped or
   * truncated rendering of it.
   */
  for (const width of [360, 540, 900] as const) {
    test(`the attach command stays one exact line at ${width}px`, async ({ page }, testInfo) => {
      await page.route("**/api/grove/share-policy**", (route) =>
        route.fulfill({ json: { ttl_seconds: 604800, passcode_set: false } }),
      );
      await openSharpSurface(page, "controls", width, "dark");

      const block = page.getByTestId("attach-command");
      await expect(block).toBeVisible();
      const geometry = await block.evaluate((element) => {
        const well = element.lastElementChild as HTMLElement;
        const line = Number.parseFloat(getComputedStyle(well).lineHeight);
        const inner = well.firstElementChild as HTMLElement | null;
        return {
          text: well.textContent?.trim() ?? "",
          lines: inner ? Math.round(inner.getBoundingClientRect().height / line) : 0,
          fontFamily: getComputedStyle(well).fontFamily,
        };
      });

      expect(geometry.lines, `${width}px`).toBe(1);
      expect(geometry.text).toMatch(/^grove attach \S+$/);
      // §3: a command is a literal you retype, so it stays monospace.
      expect(geometry.fontFamily.toLowerCase()).toContain("mono");

      if (width === 540) {
        await page.screenshot({ path: testInfo.outputPath("attach-command.png") });
      }
    });
  }

  /**
   * The chrome must not be the loudest text on the page. Measured on the built
   * app before this change, every band held a 12px inline floor while the
   * content it frames rendered 9.6-11.2px. What matters is the RANKING, not any
   * one number: title above navigation, navigation above metadata.
   */
  test("workspace chrome ranks title over nav over metadata", async ({ page }, testInfo) => {
    await openSharpSurface(page, "info", 540, "dark");
    await page.getByTestId("work-panel-tab-terminal").click();
    await expect(page.getByTestId("terminal-tab")).toBeVisible();

    const size = async (locator: string) =>
      Number.parseFloat(
        await page.locator(locator).first().evaluate((el) => getComputedStyle(el).fontSize),
      );

    const title = await size('[data-testid="shell-header"] > span');
    const paneTab = await size('[data-testid="pane-transcript"]');
    const workTab = await size('[data-testid="work-panel-tab-terminal"]');
    const metadata = await size('[data-testid="terminal-capture-source"]');

    expect(title).toBeGreaterThan(paneTab);
    expect(paneTab).toBe(workTab);
    expect(workTab).toBeGreaterThan(metadata);
    // Every band still sits on the ramp rather than under it.
    expect(metadata).toBeGreaterThanOrEqual(dp(12));

    // And the header reports nothing: no marquee, no space held for one.
    await expect(page.locator('[data-slot="agent-status"]')).toHaveCount(0);

    await page.screenshot({ path: testInfo.outputPath("chrome-ranking.png") });
  });
});
