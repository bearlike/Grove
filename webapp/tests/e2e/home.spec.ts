import { test, expect } from "@playwright/test";

// `/` is the composer-first surface (#96): a hero composer at the top, then every
// workspace across every project rendered ONCE in the repo-grouped grid (the rail
// is nav/scope only). Cards come from the fake daemon's /activity snapshot. The
// Grove section is attention-first within the repo: w-grove-2 (idle→waiting) leads
// w-grove-1 (active→working); the website section holds w-other-1.

test("home leads with the composer and renders the repo-grouped grid", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("composer-prompt")).toBeVisible();
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
  const sections = page.getByTestId("project-section");
  await expect(sections).toHaveCount(2);
  await expect(page.locator('[data-testid="project-section"][data-repo="/repos/Grove"]')).toBeVisible();
  await expect(page.locator('[data-testid="project-section"][data-repo="/repos/website"]')).toBeVisible();
});

test("attention-first ordering floats the waiting card to the top of its section", async ({ page }) => {
  await page.goto("/");
  const grove = page.locator('[data-testid="project-section"][data-repo="/repos/Grove"]');
  // w-grove-2 is IDLE → its agent maps to WAITING (attention), so it outranks the
  // WORKING card within the Grove section.
  await expect(grove.getByTestId("workspace-card").first()).toHaveAttribute(
    "data-agent-state",
    "waiting",
  );
});

test("each card carries the diff stat trio off the activity view", async ({ page }) => {
  await page.goto("/");
  const first = page.getByTestId("workspace-card").first();
  await expect(first.getByTestId("stat-trio")).toBeVisible();
  // Pinned to the fake daemon's activity rows (base_ahead 1 / base_behind 0 / dirty_files 2).
  await expect(first.getByTestId("stat-ahead")).toContainText("1");
  await expect(first.getByTestId("stat-behind")).toContainText("0");
  await expect(first.getByTestId("stat-dirty")).toContainText("2");
});

test("a card title links to its detail page", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("workspace-grid").getByRole("link", { name: "feat dashboard" }).click();
  await expect(page).toHaveURL(/\/w\/w-grove-1$/);
});

test("the Live toggle raises one focused pane", async ({ page }) => {
  await page.goto("/");
  // w-grove-1 is ACTIVE → its agent maps to WORKING, so it gets a Live toggle.
  const card = page.getByTestId("workspace-card").filter({ hasText: "feat dashboard" });
  await card.getByTestId("live-toggle").click();
  await expect(page.getByTestId("focused-pane")).toBeVisible();
  await expect(page.getByTestId("focused-pane")).toContainText("agent pane for feat dashboard");
});

test("the page has no horizontal scroll on mobile or desktop", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("workspace-card").first()).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflow).toBe(false);
});
