import { test, expect } from "@playwright/test";

test("activity dashboard renders one attention-first card wall", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
  // Three fixture workspaces across two repos on ONE flat wall — project
  // identity is a per-card chip, not a group heading.
  await expect(page.getByTestId("session-card")).toHaveCount(3);
  await expect(page.getByTestId("project-chip").filter({ hasText: "Grove" }).first()).toBeVisible();
  await expect(page.getByTestId("project-chip").filter({ hasText: "website" }).first()).toBeVisible();
  // Attention-first ordering: a WAITING fixture outranks the WORKING one.
  await expect(page.getByTestId("session-card").first()).toHaveAttribute(
    "data-agent-state",
    "waiting",
  );
});

test("the consolidated filter narrows the wall", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByTestId("session-card")).toHaveCount(3);
  // Open the single filter dropdown and switch on "Needs attention only" — keeps
  // only the WAITING sessions (the idle-status fixtures).
  await page.getByTestId("dashboard-filter").click();
  await page.getByTestId("filter-attention").click();
  await page.keyboard.press("Escape");
  const cards = page.getByTestId("session-card");
  await expect(cards).not.toHaveCount(3);
  for (const card of await cards.all()) {
    await expect(card).toHaveAttribute("data-agent-state", "waiting");
  }
});

test("a session card title links to its detail page", async ({ page }) => {
  await page.goto("/activity");
  await page.getByRole("link", { name: "feat dashboard" }).click();
  await expect(page).toHaveURL(/\/w\/w-grove-1$/);
});

test("the Live toggle raises one focused pane", async ({ page }) => {
  await page.goto("/activity");
  // w-grove-1 is ACTIVE → its agent maps to WORKING, so it gets a Live toggle.
  const card = page.getByTestId("session-card").filter({ hasText: "feat dashboard" });
  await card.getByTestId("live-toggle").click();
  await expect(page.getByTestId("focused-pane")).toBeVisible();
  await expect(page.getByTestId("focused-pane")).toContainText("agent pane for feat dashboard");
});

test("dashboard has no horizontal scroll on mobile or desktop", async ({ page }) => {
  await page.goto("/activity");
  await expect(page.getByTestId("session-card").first()).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflow).toBe(false);
});
