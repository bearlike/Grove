import { test, expect } from "@playwright/test";

// `/activity` and the home grid are ONE composer-first surface at `/`. The
// route is kept (not deleted) so bookmarks/links don't 404 — it redirects to
// the merged surface.

test("/activity redirects to the composer-first workspace surface", async ({ page }) => {
  await page.goto("/activity");
  await expect(page).toHaveURL(/\/$/);
  // The merged surface is the landing (Hero by default): the composer plus the
  // Hero|Overview toggle. The card grid lives one persisted toggle away.
  await expect(page.getByTestId("composer-prompt")).toBeVisible();
  await expect(page.getByTestId("landing-view-toggle")).toBeVisible();
});
