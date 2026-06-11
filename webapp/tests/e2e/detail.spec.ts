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

test("sessions panel lists sessions; a row click expands the turns view", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("sessions-panel")).toBeVisible();

  const rows = page.getByTestId("session-row");
  await expect(rows).toHaveCount(2);
  await expect(page.getByTestId("session-provenance").first()).toHaveText("grove");
  await expect(page.getByTestId("session-provenance").nth(1)).toHaveText("hand-started");

  await rows.first().click();
  await expect(page.getByTestId("turns-view")).toBeVisible();
  const turnRows = page.getByTestId("turn-row");
  await expect(turnRows).toHaveCount(3);
  await expect(turnRows.first()).toContainText("continued session");
  await expect(turnRows.last()).toContainText("run the tests");

  // Re-click collapses the expansion.
  await rows.first().click();
  await expect(page.getByTestId("turns-view")).toHaveCount(0);
});
