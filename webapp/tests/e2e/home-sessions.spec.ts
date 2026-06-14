import { test, expect } from "@playwright/test";

// The home page's per-project Sessions section: one collapsed row above each
// repo tab's workspace grid, expanding into the project-wide session list
// (fake daemon's `GET /sessions?repo=` — grove-launched rows per workspace
// plus one hand-staged session).

test("sessions section is collapsed by default on a repo tab", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Grove/ }).click();
  const toggle = page.getByTestId("project-sessions-toggle");
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("session-row")).toHaveCount(0);
  // The grid still renders right below the one compact row.
  await expect(page.getByTestId("workspace-card")).toHaveCount(2);
});

test("expanding shows rows with provenance and workspace attribution", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Grove/ }).click();
  await page.getByTestId("project-sessions-toggle").click();

  const rows = page.getByTestId("session-row");
  await expect(rows).toHaveCount(3); // two grove workspaces + one hand-staged
  // Grove rows carry the workspace attribution link (title + branch);
  // the hand-staged row has none — absence is itself the signal.
  await expect(page.getByTestId("session-workspace-link")).toHaveCount(2);
  await expect(rows.first().getByTestId("session-provenance")).toHaveText("grove");
  await expect(rows.first()).toContainText("kk/feat-dashboard");
  const hand = rows.last();
  await expect(hand.getByTestId("session-provenance")).toHaveText("hand-started");
  await expect(hand.getByTestId("session-workspace-link")).toHaveCount(0);
});

test("a grove row's workspace link reaches the detail page", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Grove/ }).click();
  await page.getByTestId("project-sessions-toggle").click();
  await page.getByTestId("session-workspace-link").first().click();
  await expect(page).toHaveURL(/\/w\/w-grove-1$/);
});

test("a grove row drills down into its turns; the hand-staged row cannot", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Grove/ }).click();
  await page.getByTestId("project-sessions-toggle").click();

  const rows = page.getByTestId("session-row");
  await expect(rows).toHaveCount(3);
  // No expand affordance on the hand-staged row (no dead chevron).
  await expect(rows.last().getByRole("button")).toHaveCount(0);

  await rows.first().getByRole("button").click();
  await expect(page.getByTestId("turns-view")).toBeVisible();
  await expect(page.getByTestId("turn-row").first()).toBeVisible();
});
