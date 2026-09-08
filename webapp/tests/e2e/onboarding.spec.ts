import { expect, test, type Locator, type Page } from "@playwright/test";

import { DEMO_WORKSPACE_ID } from "../../components/grove/onboarding/demo-workspace";
import { buildSteps } from "../../components/grove/onboarding/steps";

/**
 * The tour is a browser-only artifact: a mask cut over a live rectangle and a
 * popover positioned against it, both measured off the real layout. The unit
 * census proves every selector names something; this proves the something is
 * on screen at the moment the step reaches it — including anchors that only
 * exist because a demand ran (a staged chip, the annotation pane, a work tab
 * on a route the tour navigated to) — and that the hole is cut where the
 * anchor actually is, which is the bug the 2026-09-14 screenshots showed.
 */

/** Mark the tour as seen before the app boots, so the auto-open cannot race a test. */
async function seen(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("grove.onboarding.seen", "true"));
}

/** Click "Take the tour" until the card appears: a click before hydration is silently dropped. */
async function openTour(page: Page): Promise<void> {
  await expect(page.locator('[data-pill="project"]')).toBeEnabled({ timeout: 60_000 });
  await expect(async () => {
    await page.getByTestId("launch-take-tour").click();
    await expect(page.getByTestId("onboarding-card")).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
}

/** The mask's cut-out must sit on the anchor: reactour draws it as the 2nd `rect` of the mask. */
async function expectHoleOver(page: Page, anchor: Locator): Promise<void> {
  // reactour draws the hole as the LAST rect in the mask (the transparent
  // click-through highlight), sized as inline style — so a rect inside the
  // SVG `<mask>` has no layout box, and the rendered one is what is compared.
  const hole = page.locator(".reactour__mask rect").last();
  const pad = 6; // the mask padding the tour asks for
  await expect
    .poll(async () => {
      const rect = await hole.boundingBox();
      const target = await anchor.boundingBox();
      if (!rect || !target) return "missing";
      // The padded hole is clipped by the viewport for an anchor flush with
      // an edge (the rail), so compare the padded anchor clamped the same way.
      const want = { x: Math.max(0, target.x - pad), y: Math.max(0, target.y - pad) };
      const dx = rect.x - want.x;
      const dy = rect.y - want.y;
      const dw = rect.width - 2 * pad - target.width;
      return Math.abs(dx) < 4 && Math.abs(dy) < 4 && Math.abs(dw) < 4
        ? "ok"
        : `hole ${JSON.stringify(rect)} vs anchor ${JSON.stringify(target)}`;
    }, { message: "mask hole tracks the anchor", timeout: 5_000 })
    .toBe("ok");
  // The highlight rect carries the glow ring, and it is the rendered element.
  await expect(hole).toHaveClass(/tour-highlight/);
  const stroke = await hole.evaluate((el) => getComputedStyle(el).strokeWidth);
  expect(parseFloat(stroke)).toBeGreaterThan(0);
  // And the popover is inside the viewport.
  const card = await page.getByTestId("onboarding-card").boundingBox();
  const viewport = page.viewportSize()!;
  expect(card!.x).toBeGreaterThanOrEqual(0);
  expect(card!.y).toBeGreaterThanOrEqual(0);
  expect(card!.x + card!.width).toBeLessThanOrEqual(viewport.width);
  expect(card!.y + card!.height).toBeLessThanOrEqual(viewport.height);
}

const STEPS = buildSteps({ workspaceId: DEMO_WORKSPACE_ID, hasDiagram: true });

test.describe("onboarding tour", () => {
  test("opens once on a first visit, and never again", async ({ page }) => {
    await page.goto("/");
    const card = page.getByTestId("onboarding-card");
    await expect(card).toBeVisible({ timeout: 60_000 });
    await expect(card).toHaveAttribute("data-step", "0");
    await page.getByTestId("onboarding-close").click();
    await expect(card).toBeHidden();

    await page.reload();
    await expect(page.getByTestId("launch-page")).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(1_200);
    await expect(card).toBeHidden();
  });

  test("walks all eighteen steps across both routes with the hole on the anchor", async ({ page }) => {
    await seen(page);
    // The demo workspace's Diagram tab embeds the hosted draw.io editor; the
    // tour only needs the TAB, so the editor is stubbed the way diagram.spec
    // does rather than reaching the network.
    await page.route("https://embed.diagrams.net/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>stub</title>" }),
    );
    await page.goto("/");
    await openTour(page);

    const card = page.getByTestId("onboarding-card");
    expect(STEPS).toHaveLength(18);
    for (const [index, step] of STEPS.entries()) {
      await expect(card).toHaveAttribute("data-step", String(index), { timeout: 15_000 });
      await expect(card.getByRole("heading", { name: step.title })).toBeVisible();
      const anchor = page.locator(step.selector).first();
      await expect(anchor, `${step.title} anchor`).toBeVisible({ timeout: 15_000 });
      if (step.position !== "center") await expectHoleOver(page, anchor);
      // The transcript step must show the SAMPLE'S turns, promptly: the demo
      // is written into the activity cache on open, so the page resolves a
      // session immediately instead of waiting for a stream reconnect.
      if (step.title === "The transcript") {
        await expect(page.getByTestId("transcript").first()).toContainText("Add a health endpoint", { timeout: 5_000 });
        await expect(page.getByText("Pick a session to follow")).toHaveCount(0);
      }
      // The Info step is where the demo's populated state is on screen: two
      // tickets, a phase claim with a checklist, runtime facts and identity.
      if (step.title === "Task, tickets and lifecycle") {
        await expect(page.getByTestId("ticket-refs")).toContainText("412");
        await expect(page.getByTestId("ticket-refs")).toContainText("418");
        await expect(page.getByTestId("task-phase-summary")).toBeVisible();
      }
      if (index < STEPS.length - 1) await page.getByTestId("onboarding-next").click();
    }
    // The workspace walk ran on the DEMO workspace — populated Info, two
    // tickets, a queued follow-up and an open Diagram tab — and the demo is
    // torn down with the tour: the route leaves it and nothing invented stays.
    await expect(page).toHaveURL(new RegExp(`/w/${DEMO_WORKSPACE_ID}`));
    await expect(page.getByTestId("shell-header")).toContainText("Sample ·");
    await expect(page.getByTestId("queue-card")).toBeVisible();
    await expect(page.getByTestId("work-panel-tab-diagram")).toHaveAttribute("data-state", "active");
    await page.getByTestId("onboarding-next").click();
    await expect(card).toBeHidden();
    await expect(page).toHaveURL(/\/$/);

    // The landing brief the tour wrote was cleared by `reset`; the staged
    // sample image stays, and the demo project is gone from the rail.
    await expect(page.getByTestId("launch-input")).toHaveValue("", { timeout: 60_000 });
    await expect(page.getByTestId("launch-attachments")).toContainText("tour-sample-annotated-photo.webp");
    await expect(page.getByTestId("fleet-tree")).not.toContainText("Sample · Health endpoint", { timeout: 15_000 });
  });

  test("re-measures when a demand changes the layout, without a tab switch", async ({ page }) => {
    await seen(page);
    await page.goto("/");
    await openTour(page);
    const card = page.getByTestId("onboarding-card");
    // Step 8 (index 7) stages the sample image; the chip row pushes the
    // toolbar down AFTER the step has been measured.
    for (let i = 0; i < 7; i += 1) await page.getByTestId("onboarding-next").click();
    await expect(card).toHaveAttribute("data-step", "7");
    await expect(page.getByTestId("launch-attachments")).toContainText("tour-sample-annotated-photo.webp", { timeout: 15_000 });
    await expectHoleOver(page, page.getByTestId("launch-attach"));
    // Step 9 (index 8) opens the annotation pane beside the page, which
    // re-flows everything; the popover must stay in the viewport.
    await page.getByTestId("onboarding-next").click();
    await expect(page.getByTestId("annotation-pane")).toBeVisible({ timeout: 15_000 });
    await expectHoleOver(page, page.getByTestId("annotation-pane"));
  });

  test("is reachable from the account menu on any page, and lands on the composer", async ({ page }) => {
    await seen(page);
    await page.goto("/fleet");
    await expect(page.getByTestId("fleet-page")).toBeVisible({ timeout: 60_000 });
    await page.getByTestId("account-menu").click();
    await page.getByTestId("account-take-tour").click();
    await expect(page.getByTestId("launch-page")).toBeVisible({ timeout: 30_000 });
    const card = page.getByTestId("onboarding-card");
    await expect(card).toBeVisible({ timeout: 15_000 });
    await page.keyboard.press("ArrowRight");
    await expect(card).toHaveAttribute("data-step", "1");
    await page.keyboard.press("Escape");
    await expect(card).toBeHidden();
  });
});
