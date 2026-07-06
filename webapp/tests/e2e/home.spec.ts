import { test, expect, type Page } from "@playwright/test";

// `/` is the composer-first landing (ADE #140): a persisted `Hero | Overview`
// toggle. Hero (default) shows the composer + a Recent strip; Overview shows
// today's repo-grouped card grid VERBATIM (zero-loss). The fleet is never
// hidden — it lives in the left session rail. Cards come from the fake daemon's
// /activity snapshot (Grove ×2 + website ×1).

/** Flip to the Overview card grid (persisted) and wait for it to render. */
async function toOverview(page: Page) {
  await page.getByTestId("landing-view-overview").click();
  await expect(page.getByTestId("workspace-grid")).toBeVisible();
}

test("hero is the default landing: composer + recent strip, no card grid", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("composer-prompt")).toBeVisible();
  await expect(page.getByTestId("landing-view-hero")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("composer-recent")).toBeVisible();
  // The card grid is opt-in — not rendered until Overview is chosen.
  await expect(page.getByTestId("workspace-card")).toHaveCount(0);
});

test("Overview reveals the repo-grouped card grid and persists across reload", async ({ page }) => {
  await page.goto("/");
  await toOverview(page);
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
  const sections = page.getByTestId("project-section");
  await expect(sections).toHaveCount(2);

  // The choice is persisted (one ui-store slice) — a reload lands on Overview.
  await page.reload();
  await expect(page.getByTestId("landing-view-overview")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
});

test("Overview keeps attention-first ordering within a section", async ({ page }) => {
  await page.goto("/");
  await toOverview(page);
  const grove = page.locator('[data-testid="project-section"][data-repo="/repos/Grove"]');
  await expect(grove.getByTestId("workspace-card").first()).toHaveAttribute(
    "data-agent-state",
    "waiting",
  );
});

test("Overview card title links to its detail page", async ({ page }) => {
  await page.goto("/");
  await toOverview(page);
  await page.getByTestId("workspace-grid").getByRole("link", { name: "feat dashboard" }).click();
  await expect(page).toHaveURL(/\/w\/w-grove-1$/);
});

test("Overview Live toggle raises one focused pane", async ({ page }) => {
  await page.goto("/");
  await toOverview(page);
  const card = page.getByTestId("workspace-card").filter({ hasText: "feat dashboard" });
  await card.getByTestId("live-toggle").click();
  await expect(page.getByTestId("focused-pane")).toBeVisible();
  await expect(page.getByTestId("focused-pane")).toContainText("agent pane for feat dashboard");
});

test("the page has no horizontal scroll in either landing view", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("composer-prompt")).toBeVisible();
  const noOverflow = async () =>
    page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    );
  expect(await noOverflow()).toBe(true);
  await toOverview(page);
  await expect(page.getByTestId("workspace-card").first()).toBeVisible();
  expect(await noOverflow()).toBe(true);
});
