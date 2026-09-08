import { expect, test, type Page } from "@playwright/test";

import { PLAN_CONFIRM, TRANSCRIPT_TURNS } from "../fixtures/turns";
import { FIXTURE_ACTIVITY, FIXTURE_SESSIONS, FIXTURE_TODO, FIXTURE_WORKSPACES } from "./_fixtures";
import type { DashboardSnapshotView, SessionTurnView, WorkspaceQueueView } from "@/lib/grove/api";

const WORKSPACE_ID = FIXTURE_WORKSPACES[0]!.id;

/** Enough real transcript entries to make the message viewport overflow. */
const TALL_TURNS: SessionTurnView[] = Array.from(
  { length: 24 },
  () => structuredClone(TRANSCRIPT_TURNS),
).flat();

function snapshotWithFooterCards(): DashboardSnapshotView {
  const snapshot = structuredClone(FIXTURE_ACTIVITY);
  const activity = snapshot.projects[0]!.workspaces[0]!.sessions[0]!.activity;
  activity.questions = structuredClone(PLAN_CONFIRM);
  return snapshot;
}

const QUEUE: WorkspaceQueueView = {
  supported: true,
  messages: [
    {
      position: 0,
      text: "Run the accessibility scan after the plan is approved.",
      sent_at: "2026-08-10T07:22:00Z",
    },
  ],
};

async function useTallFooterFixture(page: Page): Promise<void> {
  const snapshot = snapshotWithFooterCards();
  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshot }));
  // The ordinary fake daemon sends its baseline snapshot on connect. Holding the
  // stream open here keeps the plan approval mounted from the routed snapshot.
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ contentType: "text/event-stream", body: "" }),
  );
  await page.route("**/api/grove/workspaces/*/sessions/*/turns*", (route) =>
    route.fulfill({
      json: {
        session: FIXTURE_SESSIONS[0],
        turns: TALL_TURNS,
        total_turns: TALL_TURNS.length,
        first_turn_index: 0,
        incremental: false,
      },
    }),
  );
  await page.route("**/api/grove/workspaces/*/todo", (route) =>
    route.fulfill({ json: FIXTURE_TODO }),
  );
  await page.route("**/api/grove/workspaces/*/queue", (route) =>
    route.fulfill({ json: QUEUE }),
  );
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme} transcript ends before its opaque footer`, async ({ page }) => {
    await page.addInitScript((value) => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await useTallFooterFixture(page);
    await page.goto(`/w/${WORKSPACE_ID}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();

    const transcript = page.getByTestId("transcript");
    const viewport = transcript.locator('[data-slot="aui_thread-viewport"]');
    const footer = page.getByTestId("transcript-footer");
    await expect(viewport).toBeVisible();
    await expect(footer).toBeVisible();
    // Every persistent footer constituent is present simultaneously. Omitting
    // one makes the geometry assertion pass through a shorter footer.
    await expect(page.getByTestId("working-loader")).toBeVisible();
    await expect(page.getByTestId("todo-card")).toBeVisible();
    await expect(page.getByTestId("queue-card")).toBeVisible();
    await expect(page.getByTestId("plan-approval")).toBeVisible();
    await expect(page.locator('[data-slot="composer-bar"]')).toBeVisible();

    const geometry = await transcript.evaluate((element) => {
      const viewport = element.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]')!;
      const footer = document.querySelector<HTMLElement>('[data-testid="transcript-footer"]')!;
      viewport.scrollTop = viewport.scrollHeight;
      const context = document.createElement("canvas").getContext("2d")!;
      context.fillStyle = getComputedStyle(footer).backgroundColor;
      context.fillRect(0, 0, 1, 1);
      const [, , , alpha] = context.getImageData(0, 0, 1, 1).data;
      const messages = element.querySelectorAll('[data-slot="aui_assistant-message-root"]');
      const lastMessage = messages.item(messages.length - 1)!;
      const box = (node: Element) => node.getBoundingClientRect().toJSON();
      return {
        viewport: box(viewport),
        footer: box(footer),
        lastMessage: box(lastMessage),
        scrollHeight: viewport.scrollHeight,
        clientHeight: viewport.clientHeight,
        alpha,
        fill: getComputedStyle(footer).backgroundColor,
      };
    });

    expect(geometry.scrollHeight).toBeGreaterThan(geometry.clientHeight);
    expect(geometry.viewport.bottom).toBeLessThanOrEqual(geometry.footer.top + 1);
    expect(geometry.lastMessage.bottom).toBeLessThanOrEqual(geometry.viewport.bottom + 1);
    expect(geometry.alpha).toBe(255);
    expect(geometry.fill).not.toBe("rgba(0, 0, 0, 0)");
    await page.screenshot({ path: test.info().outputPath(`footer-${theme}.png`) });

    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 700 });
      await viewport.evaluate(element => { element.scrollTop = 0; });
      const scrollButton = page.getByRole("button", { name: "Scroll to bottom", exact: true });
      await expect(scrollButton).toBeVisible();
      await scrollButton.click();
      await expect.poll(() => viewport.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThanOrEqual(2);
      const viewportBox = (await viewport.boundingBox())!;
      const footerBox = (await footer.boundingBox())!;
      expect(viewportBox.height).toBeGreaterThan(100);
      expect(viewportBox.y + viewportBox.height).toBeLessThanOrEqual(footerBox.y + 1);
      await page.locator('[data-slot="composer-bar"]').scrollIntoViewIfNeeded();
      await expect(page.locator('[data-slot="composer-bar"]')).toBeInViewport();
    }

  });
}
