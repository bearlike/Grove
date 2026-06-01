import { test, expect } from "@playwright/test";

test("detail page renders identity / summary / agent", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("identity-panel")).toBeVisible();
  await expect(page.getByTestId("summary-panel")).toBeVisible();
  await expect(page.getByTestId("agent-panel")).toBeVisible();
  await expect(page.getByTestId("peek-snapshot")).toContainText("npm run dev");
});

test("missing workspace shows error message", async ({ page }) => {
  await page.goto("/w/does-not-exist");
  await expect(page.getByRole("alert")).toBeVisible();
});
