import { test, expect } from "@playwright/test";

// `/activity` folded into `/` (issue #89, #96): the old wall and the home grid
// are now ONE composer-first surface. The route is kept (not deleted) so
// bookmarks/links don't 404 — it redirects to the merged surface.

test("/activity redirects to the composer-first workspace surface", async ({ page }) => {
  await page.goto("/activity");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("composer-prompt")).toBeVisible();
  await expect(page.getByTestId("workspace-card")).toHaveCount(3);
});
