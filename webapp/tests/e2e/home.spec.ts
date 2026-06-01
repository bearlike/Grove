import { test, expect } from "@playwright/test";

test("home renders repo facet tabs and cards", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("All").first()).toBeVisible();
  await expect(page.getByRole("tab", { name: /Grove/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: /website/ })).toBeVisible();
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
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
