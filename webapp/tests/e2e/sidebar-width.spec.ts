import { expect, test } from "@playwright/test";

test("mobile sheet remains full width", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByTestId("shell-sidebar-sheet").click();
  const sheet = page.getByRole("dialog", { name: "Grove workspaces", exact: true });
  await expect(sheet).toBeVisible();
  await expect.poll(() => sheet.evaluate((el) => el.getBoundingClientRect().width)).toBeCloseTo(390, 0);
});

for (const fontSize of ["80%", "100%"] as const) {
  test(`expanded sidebar preserves its reduced measure and action targets at ${fontSize} root`, async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
    await page.goto("/");
    const rail = page.locator("aside");
    await expect(rail.getByTestId("fleet-create-rail")).toBeVisible();
    await page.evaluate((size) => { document.documentElement.style.fontSize = size; }, fontSize);

    // A stale width at either consumer, or a missing Tailwind utility, must fail
    // on rendered geometry rather than merely matching the constant's spelling.
    await expect.poll(() => rail.evaluate((el) =>
      el.getBoundingClientRect().width / parseFloat(getComputedStyle(document.documentElement).fontSize),
    )).toBeCloseTo(23.8, 2);

    const actions = rail.getByTestId("fleet-create-rail").locator("..");
    const boxes = await actions.locator("a, button").evaluateAll((elements) =>
      elements.map((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, width: rect.width, height: rect.height };
      }),
    );
    expect(boxes).toHaveLength(3);
    const bounds = await rail.boundingBox();
    for (const [index, box] of boxes.entries()) {
      expect(box.width).toBeGreaterThanOrEqual(24);
      expect(box.height).toBeGreaterThanOrEqual(24);
      expect(box.left).toBeGreaterThanOrEqual(bounds!.x);
      expect(box.right).toBeLessThanOrEqual(bounds!.x + bounds!.width);
      expect(box.top).toBeCloseTo(boxes[0]!.top, 1);
      if (index > 0) expect(box.left).toBeGreaterThanOrEqual(boxes[index - 1]!.right);
    }

    await testInfo.attach("expanded-sidebar", { body: await page.screenshot(), contentType: "image/png" });
    await rail.getByRole("button", { name: /Filter workspaces/ }).click();
    await expect(page.getByRole("menu")).toBeVisible();
    await page.keyboard.press("Escape");
    await rail.getByRole("button", { name: "Pause scrolling text", exact: true }).click();
    await expect(rail.getByRole("button", { name: "Resume scrolling text", exact: true })).toBeVisible();

    await page.getByTestId("shell-sidebar-toggle").click();
    await expect.poll(() => rail.evaluate((el) =>
      el.getBoundingClientRect().width / parseFloat(getComputedStyle(document.documentElement).fontSize),
    )).toBeCloseTo(3, 2);
  });
}
