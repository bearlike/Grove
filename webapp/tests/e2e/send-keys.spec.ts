import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";
import { dp } from "./density";

const WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;

/**
 * Send keys is a card on the Controls tab, not a header popover — so every
 * test here has to walk to it. The walk is also the assertion that it is
 * reachable at all.
 */
async function openSendKeys(page: Page) {
  await page.goto(`/w/${WORKSPACE_ID}`);
  await page.getByTestId("pane-work").click();
  await page.getByTestId("work-panel-tab-controls").click();
  const card = page.getByTestId("send-keys-card");
  await expect(card).toBeVisible();
  return card;
}

test("sends one named key at a time without claiming more than delivery", async ({ page }) => {
  const delivered: unknown[] = [];
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/api/grove/workspaces/${WORKSPACE_ID}/keys`, async (route) => {
    delivered.push(route.request().postDataJSON());
    if (delivered.length === 1) await pending;
    await route.fulfill({ status: 204 });
  });

  const card = await openSendKeys(page);
  await expect(card.locator('[data-slot="kbd"]')).toHaveCount(9);
  await expect(card.getByRole("button", { name: "Send Up", exact: true }).locator("kbd").last()).toHaveText("↑");
  await expect(card).toContainText("Ctrl+C may exit the agent");
  for (const key of ["Ctrl+C", "Up", "Down", "Left", "Right", "Enter", "Tab", "Escape"]) {
    await expect(card.getByRole("button", { name: `Send ${key}`, exact: true })).toBeVisible();
  }

  await card.getByRole("button", { name: "Send Up", exact: true }).click();
  await expect.poll(() => delivered).toEqual([{ key: "Up" }]);
  await expect(card.getByRole("button", { name: "Send Up", exact: true })).toBeDisabled();
  await expect(card.getByRole("button", { name: "Send Enter", exact: true })).toBeDisabled();
  await expect(card.getByRole("status")).toContainText("Sending Up");
  release();
  await expect(card.getByRole("status")).toContainText("Delivered Up.");

  await card.getByRole("button", { name: "Send Right", exact: true }).click();
  await expect.poll(() => delivered).toEqual([{ key: "Up" }, { key: "Right" }]);
  await expect(card.getByRole("status")).toContainText("Delivered Right.");
  expect(delivered).toHaveLength(2);
});

/**
 * THE HEADER ONLY NAVIGATES NOW. It used to report as well, through a status
 * pill carrying the agent's own prose — the widest and most restless thing in
 * a 32px band whose job is to stay still. Both the pill and the send-keys
 * trigger before it left for surfaces that can hold them: the phase note reads
 * on the Task card, the keys are a card on Controls.
 *
 * An ABSENCE needs its own positive evidence, so this asserts what the band
 * DOES contain: the three pane tabs, at full height, with nothing between the
 * title and them holding space for a pill that is gone.
 */
test("the header navigates and offers neither status nor keys of its own", async ({ page }) => {
  await page.goto(`/w/${WORKSPACE_ID}`);
  const header = page.getByTestId("shell-header");
  await expect(header).toBeVisible();
  await expect(page.locator('[data-slot="agent-status"]')).toHaveCount(0);
  await expect(page.getByTestId("send-keys-trigger")).toHaveCount(0);

  // The tabs are still there, still full-size, and still the only controls.
  for (const pane of ["transcript", "work", "split"]) {
    const tab = page.getByTestId(`pane-${pane}`);
    await expect(tab).toBeVisible();
    expect((await tab.boundingBox())?.height).toBeGreaterThanOrEqual(dp(24));
  }
  // No spacer took the pill's place: the tab strip sits flush against the
  // header's right edge, so the reclaimed width went back to the title.
  const strip = await page.locator('[aria-label="Workspace panes"]').boundingBox();
  const band = await header.boundingBox();
  expect(band!.x + band!.width - (strip!.x + strip!.width)).toBeLessThan(dp(24));
});

/*
 * A "status marquee" test used to live here, driving `useOverflowMotion`
 * through the header pill: overflow detection, real textIndent interpolation,
 * reduced-motion stand-down, and ResizeObserver retirement. The pill is gone,
 * so this file has no subject for it — and the mechanism is NOT untested. Its
 * surviving consumer is the sidebar, where `tests/e2e/sidebar-sessions.spec.ts`
 * covers the same four properties against `[data-looping]` on a surface that
 * still loops on purpose. Deleting the assertions rather than repointing them
 * at the rail is deliberate: that file already owns them, and a second copy
 * would be two tests to update the next time the mechanism moves.
 */

test("surfaces terminal refusal without retrying or claiming delivery", async ({ page }) => {
  let calls = 0;
  await page.route(`**/api/grove/workspaces/${WORKSPACE_ID}/keys`, async (route) => {
    calls++;
    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ detail: { error: "steering_unsupported", message: "send keys requires the agent's live terminal" } }),
    });
  });
  const card = await openSendKeys(page);
  await card.getByRole("button", { name: "Send Escape", exact: true }).click();
  await expect(card.getByRole("alert")).toContainText("live terminal");
  await expect(card.getByRole("status")).not.toContainText("Delivered");
  await expect(card.getByRole("button", { name: "Send Escape", exact: true })).toBeEnabled();
  expect(calls).toBe(1);
});

test("keeps provider cancellation as a preset separate from raw keys", async ({ page }) => {
  let cancelled = 0;
  await page.route(`**/api/grove/workspaces/${WORKSPACE_ID}/interrupt`, async (route) => {
    cancelled++;
    await route.fulfill({ status: 204 });
  });
  const card = await openSendKeys(page);
  await card.getByRole("button", { name: "Cancel turn", exact: true }).click();
  await expect.poll(() => cancelled).toBe(1);
  await expect(card.getByRole("status")).toContainText("Cancellation request delivered.");
});
