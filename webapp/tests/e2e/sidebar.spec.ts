import { test, expect } from "@playwright/test";

// The (shell) rail is nav/SCOPE only now (#96): no per-workspace rows (those
// render once in the main grid). It carries a search box, a repo scope switcher
// (All + one row per repo with a count), agent-state filter chips, and the
// daemon footer. Its controls drive the grid via the one client-state store.
// Fixtures: two repos (Grove ×2, website ×1). Desktop rail + mobile drawer are
// project-gated, mirroring the detail-page split-view specs.

test.describe("desktop rail (nav/scope only)", () => {
  test("offers repo scopes with counts and no per-workspace rows", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("workspace-sidebar")).toBeVisible();
    await expect(page.getByTestId("sidebar-repo-all")).toBeVisible();
    const repos = page.getByTestId("sidebar-repo");
    await expect(repos).toHaveCount(2);
    await expect(
      page.locator('[data-testid="sidebar-repo"][data-repo="/repos/Grove"]'),
    ).toContainText("2");
    await expect(
      page.locator('[data-testid="sidebar-repo"][data-repo="/repos/website"]'),
    ).toContainText("1");
    // The rail no longer mirrors the grid — no workspace rows live here.
    await expect(page.getByTestId("sidebar-entry")).toHaveCount(0);
    // The mobile hamburger is suppressed once the rail is persistent.
    await expect(page.getByTestId("sidebar-trigger")).toBeHidden();
  });

  test("clicking a repo scope narrows the grid to that project", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("workspace-card")).toHaveCount(3);
    await page.locator('[data-testid="sidebar-repo"][data-repo="/repos/website"]').click();
    await expect(page.getByTestId("workspace-card")).toHaveCount(1);
    await expect(page.getByTestId("project-section")).toHaveCount(1);
    // Back to all.
    await page.getByTestId("sidebar-repo-all").click();
    await expect(page.getByTestId("workspace-card")).toHaveCount(3);
  });

  test("search narrows the grid by title/branch", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await page.getByTestId("sidebar-search").fill("spike");
    await expect(page.getByTestId("workspace-card")).toHaveCount(1);
    await expect(page.getByTestId("workspace-card").first()).toContainText("spike");
  });

  test("the attention filter narrows the grid to action-required cards", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("workspace-card")).toHaveCount(3);
    await page.getByTestId("sidebar-attention-toggle").click();
    const cards = page.getByTestId("workspace-card");
    await expect(cards).toHaveCount(1); // only w-grove-2 (waiting) wants the human
    await expect(cards.first()).toHaveAttribute("data-agent-state", "waiting");
  });

  test("the collapse toggle clips the desktop rail and flips its state", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("workspace-sidebar")).toBeVisible();
    // The animated wrapper (the rail's parent) clips to w-0; the inner <aside>
    // keeps a stable w-72, so measure the wrapper, not the aside.
    const wrapper = page.getByTestId("workspace-sidebar").locator("xpath=..");
    const toggle = page.getByTestId("sidebar-collapse-toggle");
    await expect(toggle).toHaveAttribute("aria-label", "Collapse sidebar");
    expect((await wrapper.boundingBox())?.width ?? 0).toBeGreaterThan(100);
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", "Expand sidebar");
    await expect.poll(async () => (await wrapper.boundingBox())?.width ?? 0).toBeLessThan(10);
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", "Collapse sidebar");
    await expect.poll(async () => (await wrapper.boundingBox())?.width ?? 0).toBeGreaterThan(100);
  });
});

test.describe("mobile drawer", () => {
  test("the rail opens as a drawer from the header hamburger", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "drawer is sub-lg");
    await page.goto("/");
    const trigger = page.getByTestId("sidebar-trigger");
    await expect(trigger).toBeVisible();
    await trigger.click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    // The drawer carries the same scope switcher as the desktop rail.
    await expect(drawer.getByTestId("sidebar-repo-all")).toBeVisible();
    await expect(drawer.getByTestId("sidebar-repo")).toHaveCount(2);
  });
});
