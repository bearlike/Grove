import { expect, test } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";

for (const theme of ["light", "dark"]) {
  test(`${theme} transcript shadow stays on its pane and the composer footer is flat`, async ({ page }) => {
    await page.addInitScript((theme) => {
      localStorage.setItem("theme", theme);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    const root = page.locator(".aui-thread-root");
    const top = page.getByTestId("scroll-edge-top");
    await expect(root).toBeVisible();
    await expect(top).toHaveCSS("opacity", "1");
    // The footer is opaque and nothing scrolls beneath it, so a lower fade
    // only darkened the status region and left the composer row a lighter band.
    await expect(page.getByTestId("scroll-edge-bottom")).toHaveCount(0);
    const geometry = await root.evaluate((element) => {
      const top = element.querySelector('[data-testid="scroll-edge-top"]')!;
      const footer = element.querySelector(".aui-thread-viewport-footer")!;
      const composer = element.querySelector('[data-slot="composer-bar"]')!;
      const box = (element: Element) => element.getBoundingClientRect().toJSON();
      return {
        root: box(element), top: box(top), footer: box(footer), composer: box(composer),
        footerFill: getComputedStyle(footer).backgroundColor,
        overflow: getComputedStyle(element).overflow,
        topZ: getComputedStyle(top).zIndex,
        topImage: getComputedStyle(top).backgroundImage,
        topPointer: getComputedStyle(top).pointerEvents,
      };
    });
    expect(geometry.overflow).toBe("hidden");
    expect(Number(geometry.topZ)).toBeGreaterThan(0);
    expect(geometry.topImage).toContain("linear-gradient");
    expect(geometry.topPointer).toBe("none");
    expect(geometry.top.left).toBeCloseTo(geometry.root.left, 0);
    expect(geometry.top.right).toBeCloseTo(geometry.root.right, 0);
    expect(geometry.top.top).toBeCloseTo(geometry.root.top, 0);
    // The footer now owns an opaque surface; transcript rows cannot bleed
    // through the gaps between its loader, cards and composer.
    expect(geometry.footerFill).not.toBe("rgba(0, 0, 0, 0)");
    const screenshot = await page.screenshot({ path: test.info().outputPath(`transcript-${theme}.png`) });
    const pixels = await page.evaluate(async ({ image, footer, composer }) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${image}`)).blob());
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(bitmap, 0, 0);
      // The pane gutter beside the status region and beside the composer must
      // be one colour: any difference is the band this spec exists to forbid.
      const x = Math.round(footer.left + 3);
      return [footer.top + 4, (composer.top + composer.bottom) / 2].map((y) => {
        const [red, green, blue] = context.getImageData(x, Math.round(y), 1, 1).data;
        return red + green + blue;
      });
    }, { image: screenshot.toString("base64"), footer: geometry.footer, composer: geometry.composer });
    expect(Math.abs(pixels[0] - pixels[1])).toBeLessThanOrEqual(3);
  });
}
