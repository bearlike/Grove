import { expect, test } from "@playwright/test";

import { FIXTURE_USAGE } from "./_fixtures";

for (const width of [390, 1440]) {
  test(`shows a priced subtotal despite incomplete sources at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const cost = { amount: "12.500000", currency: "USD", provenance: "estimated" as const };
    await page.route("**/api/grove/usage/summary*", (route) => route.fulfill({
      json: {
        ...FIXTURE_USAGE.summary,
        sessions: 3,
        cost: null,
        cost_breakdown: { known_cost: cost, priced_sessions: 2, total_sessions: 3 },
      },
    }));
    await page.route("**/api/grove/usage/sessions*", (route) => route.fulfill({
      json: {
        ...FIXTURE_USAGE.sessions,
        rows: FIXTURE_USAGE.sessions.rows.slice(0, 1).map((row) => ({ ...row, cost })),
      },
    }));
    await page.goto("/usage");
    const card = page.getByTestId("usage-cost");
    await expect(card).toContainText("$12.50");
    await expect(card).toContainText("Known partial");
    await expect(card).toContainText("2 of 3 sessions priced");
    await expect(card).not.toContainText("neither");
    await expect(card).not.toContainText("not measured");
    const coverage = page.getByTestId("usage-coverage");
    await expect(coverage).toContainText("transcript cwd was not measured");
    await expect(coverage).not.toContainText("Totals below understate");
    const sessions = page.getByTestId("usage-sessions");
    await expect(sessions.getByRole("columnheader", { name: "Cost", exact: true })).toBeVisible();
    await expect(sessions).toContainText("$12.50");
    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  });
}

test("shows a measured zero instead of the unavailable fallback", async ({ page }) => {
  await page.route("**/api/grove/usage/summary*", (route) => route.fulfill({
    json: {
      ...FIXTURE_USAGE.summary,
      sessions: 1,
      cost: { amount: "0.000000", currency: "USD", provenance: "estimated" },
      cost_breakdown: {
        known_cost: { amount: "0.000000", currency: "USD", provenance: "estimated" },
        priced_sessions: 1,
        total_sessions: 1,
      },
    },
  }));
  await page.goto("/usage");
  const card = page.getByTestId("usage-cost");
  await expect(card).toContainText("$0.00");
  await expect(card).toContainText("Complete estimate");
  await expect(card).not.toContainText("unavailable");
});
