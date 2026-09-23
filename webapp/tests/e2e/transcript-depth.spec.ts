import { expect, test } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";

for (const theme of ["light", "dark"]) {
  test(`${theme} transcript shadows stay on their pane and above the composer`, async ({ page }) => {
    await page.addInitScript((theme) => {
      localStorage.setItem("theme", theme);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    const root = page.locator(".aui-thread-root");
    const top = page.getByTestId("scroll-edge-top");
    const bottom = page.getByTestId("scroll-edge-bottom");
    await expect(root).toBeVisible();
    await expect(top).toHaveCSS("opacity", "1");
    await expect(bottom).toHaveCSS("opacity", "1");
    const geometry = await root.evaluate((element) => {
      const top = element.querySelector('[data-testid="scroll-edge-top"]')!;
      const bottom = element.querySelector('[data-testid="scroll-edge-bottom"]')!;
      const footer = element.querySelector(".aui-thread-viewport-footer")!;
      const composer = element.querySelector('[data-slot="composer-bar"]')!;
      const loader = element.querySelector('[data-testid="working-loader"]')!;
      const box = (element: Element) => element.getBoundingClientRect().toJSON();
      return {
        root: box(element), top: box(top), bottom: box(bottom), footer: box(footer),
        composer: box(composer), loader: box(loader),
        footerFill: getComputedStyle(footer).backgroundColor,
        overflow: getComputedStyle(element).overflow,
        topZ: getComputedStyle(top).zIndex,
        topImage: getComputedStyle(top).backgroundImage,
        bottomImage: getComputedStyle(bottom).backgroundImage,
        topPointer: getComputedStyle(top).pointerEvents,
        bottomPointer: getComputedStyle(bottom).pointerEvents,
      };
    });
    expect(geometry.overflow).toBe("hidden");
    expect(Number(geometry.topZ)).toBeGreaterThan(0);
    expect(geometry.topImage).toContain("linear-gradient");
    expect(geometry.bottomImage).toContain("linear-gradient");
    expect(geometry.topPointer).toBe("none");
    expect(geometry.bottomPointer).toBe("none");
    for (const edge of [geometry.top, geometry.bottom]) {
      expect(edge.left).toBeCloseTo(geometry.root.left, 0);
      expect(edge.right).toBeCloseTo(geometry.root.right, 0);
      expect(edge.top).toBeGreaterThanOrEqual(geometry.root.top);
      expect(edge.bottom).toBeLessThanOrEqual(geometry.root.bottom);
    }
    expect(geometry.top.top).toBeCloseTo(geometry.root.top, 0);
    expect(geometry.bottom.bottom).toBeCloseTo(geometry.composer.top, 0);
    expect(geometry.bottom.top).toBeLessThanOrEqual(geometry.loader.top);
    expect(geometry.bottom.bottom).toBeGreaterThan(geometry.loader.bottom);
    // The footer now owns an opaque surface; transcript rows cannot bleed
    // through the gaps between its loader, cards and composer.
    expect(geometry.footerFill).not.toBe("rgba(0, 0, 0, 0)");
    const screenshot = await page.screenshot({ path: test.info().outputPath(`transcript-${theme}.png`) });
    const pixels = await page.evaluate(async ({ image, edge }) => {
      const bitmap = await createImageBitmap(await (await fetch(`data:image/png;base64,${image}`)).blob());
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(bitmap, 0, 0);
      // Sample the pane gutter, away from text and cards, to verify painted
      // depth rather than a gradient that exists in CSS but is covered up.
      return [0.1, 0.5, 0.9].map((fraction) => {
        const [red, green, blue] = context.getImageData(
          Math.round(edge.left + 3), Math.round(edge.top + edge.height * fraction), 1, 1,
        ).data;
        return red + green + blue;
      });
    }, { image: screenshot.toString("base64"), edge: geometry.bottom });
    expect(pixels[0]).toBeGreaterThan(pixels[1]);
    expect(pixels[1]).toBeGreaterThan(pixels[2]);
  });
}
