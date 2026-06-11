import { test, expect } from "@playwright/test";

// The home grid rides the activity stream: cards come from the fake daemon's
// `/activity` snapshot (also resent over the `/events` SSE stub), not from
// `/workspaces` + per-card peeks.

test("home renders repo facet tabs and cards from the activity snapshot", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("All").first()).toBeVisible();
  await expect(page.getByRole("tab", { name: /Grove/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: /website/ })).toBeVisible();
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
});

test("card stat trio reads ahead/behind/dirty off the activity view", async ({ page }) => {
  await page.goto("/");
  const first = page.getByTestId("workspace-card").first();
  await expect(first.getByTestId("stat-trio")).toBeVisible();
  // Pinned to the fake daemon's activity rows (base_ahead 1 / base_behind 0 / dirty_files 2).
  await expect(first.getByTestId("stat-ahead")).toContainText("1");
  await expect(first.getByTestId("stat-behind")).toContainText("0");
  await expect(first.getByTestId("stat-dirty")).toContainText("2");
});

test("repo facet filters cards", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /website/ }).click();
  await expect(page.getByTestId("workspace-card")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "spike" })).toBeVisible();
});

test("card click navigates to detail", async ({ page }) => {
  await page.goto("/");
  await page.getByText("feat dashboard").click();
  await expect(page).toHaveURL(/\/w\/w-grove-1$/);
});
