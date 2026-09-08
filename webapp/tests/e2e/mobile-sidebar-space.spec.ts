import { expect, test } from "@playwright/test";

for (const width of [320, 390, 600]) {
  test(`touch sidebar uses its width and contains header controls at ${width}px`, async ({ browser, baseURL }) => {
    const context = await browser.newContext({
      viewport: { width, height: 850 }, isMobile: true, hasTouch: true,
      storageState: "tests/e2e/.auth/storage-state.json",
    });
    const page = await context.newPage();
    await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
    await page.goto(`${baseURL}/`);
    const opener = page.getByTestId("shell-sidebar-sheet");
    await expect(opener).toHaveCSS("min-height", "44px");
    const header = await page.getByTestId("shell-header").boundingBox();
    const target = await opener.boundingBox();
    expect(target!.y + target!.height).toBeLessThanOrEqual(header!.y + header!.height);
    await opener.click();
    const sheet = page.getByRole("dialog", { name: "Grove workspaces", exact: true });
    await expect(sheet.getByTestId("fleet-row").first()).toBeVisible();
    await sheet.evaluate(async el => {
      await Promise.all(el.getAnimations().map(animation => animation.finished.catch(() => undefined)));
    });
    const layout = await sheet.evaluate(el => {
      const box = (selector: string) => el.querySelector(selector)!.getBoundingClientRect().toJSON();
      return {
        sheet: el.getBoundingClientRect().toJSON(),
        header: box('[data-testid="sidebar-brand-header"]'),
        search: box('[data-testid="sidebar-search-trigger"]'),
        close: [...el.querySelectorAll('button')].find(button => button.textContent?.trim() === "Close" || button.getAttribute("aria-label") === "Close")!.getBoundingClientRect().toJSON(),
        list: box('[data-testid="fleet-tree"]'),
        row: box('[data-testid="fleet-row"]'),
        footer: el.querySelector('[data-testid="fleet-tree"]')!.parentElement!.nextElementSibling!.getBoundingClientRect().toJSON(),
        overflow: el.scrollWidth - el.clientWidth,
      };
    });
    expect(layout.overflow).toBeLessThanOrEqual(1);
    expect(layout.list.width).toBeCloseTo(layout.sheet.width - 1, 0);
    expect(layout.footer.width).toBeCloseTo(layout.list.width, 0);
    expect(layout.list.right - layout.row.right).toBeLessThanOrEqual(13);
    for (const control of [layout.search, layout.close]) {
      expect(control.width).toBeGreaterThanOrEqual(44);
      expect(control.height).toBeGreaterThanOrEqual(44);
      expect(control.top).toBeGreaterThanOrEqual(layout.header.top);
      expect(control.bottom).toBeLessThanOrEqual(layout.header.bottom);
      expect(control.right).toBeLessThanOrEqual(layout.header.right);
    }
    expect(layout.search.right).toBeLessThanOrEqual(layout.close.left);
    await sheet.getByTestId("sidebar-search-trigger").click();
    await expect(sheet).toBeHidden();
    await expect(page.getByRole("dialog", { name: "Search workspaces", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Search workspaces", exact: true })).toBeHidden();
    await page.getByTestId("shell-sidebar-sheet").click();
    await expect(sheet).toBeVisible();
    await sheet.getByRole("button", { name: "Close", exact: true }).click();
    await expect(sheet).toBeHidden();
    await context.close();
  });
}
