import { expect, test } from "@playwright/test";

import { FIXTURE_ACTIVITY, FIXTURE_PEEK } from "./_fixtures";

const id = FIXTURE_PEEK.state.id;

test("Activity and footer use current primary context rather than child or lifetime totals", async ({ page }) => {
  const snapshot = structuredClone(FIXTURE_ACTIVITY);
  const workspace = snapshot.projects[0]!.workspaces[0]!;
  const primary = workspace.sessions[0]!;
  primary.activity.context = { used: 128_450, size: 200_000, used_fraction: 1 };
  primary.activity.tokens_in = 29_415_905;
  const child = structuredClone(primary);
  child.session.session_id = "child-context-fixture";
  child.session.parent_session_id = primary.session.session_id;
  child.activity.context = { used: 900_000, size: 1_000_000, used_fraction: 0.9 };
  workspace.sessions.push(child);

  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshot }));
  // Keep connectivity online without letting the fake daemon replace this
  // scenario with its default snapshot. Closing an empty SSE response instead
  // tests the offline footer and hides every subscription row.
  await page.addInitScript(() => {
    class ScenarioEvents extends EventTarget {
      readyState = 1;
      onopen: (() => void) | null = null;
      onerror = null;
      constructor() {
        super();
        setTimeout(() => this.onopen?.(), 0);
      }
      close(): void { this.readyState = 2; }
    }
    window.EventSource = ScenarioEvents as unknown as typeof EventSource;
  });
  await page.route(`**/api/grove/workspaces/${id}/activity`, (route) =>
    route.fulfill({ json: workspace }),
  );
  await page.route(`**/api/grove/workspaces/${id}/peek`, (route) =>
    route.fulfill({ json: FIXTURE_PEEK }),
  );

  await page.route("**/api/grove/usage/quotas", (route) => route.fulfill({ json: {
    accounts: [{
      account_id: "context-plan", provider: "claude_code", label: "reader@example.com",
      billing_mode: "subscription", status: "ok", subscription: { plan: "max", label: "max", detail: "20x" },
      windows: [{ scope: "weekly", label: "7d", used_percent: 8 }],
    }], coverage: {},
  } }));

  for (const used of [128_450, 12_345, 0]) {
    primary.activity.context = { used, size: 200_000, used_fraction: 1 };
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(`/w/${id}`);
    await page.getByTestId("pane-work").click();
    await page.getByTestId("work-panel-tab-info").click();
    const activity = page.getByTestId("info-tab");
    const footer = page.getByTestId("status-footer");
    const percent = used === 128_450 ? "64.23%" : used === 12_345 ? "6.17%" : "0%";
    await expect(activity).toContainText(percent);
    await expect(activity).toContainText(used.toLocaleString("en-US"));
    await expect(footer).toContainText(percent);
    await expect(footer).not.toContainText("900K");
    await expect(footer).not.toContainText("29.42M");
    await expect(footer.getByTestId("footer-context-window")).toHaveCSS("font-weight", "400");
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTestId("footer-summary-trigger").click();
  const details = page.getByTestId("footer-summary-details");
  await expect(details).toBeVisible();
  await expect(details).toContainText("Claude max 20x");
  await expect(details).not.toContainText("reader@example.com");
  await expect(details.getByTestId("footer-summary-context-window")).toContainText("0 / 200,000 tokens");
  await page.keyboard.press("Escape");

  primary.activity.context = null;
  await page.goto(`/w/${id}`);
  await page.getByTestId("pane-work").click();
  await page.getByTestId("work-panel-tab-info").click();
  await expect(page.getByTestId("context-meter")).toHaveCount(0);
  await expect(page.getByTestId("footer-context-window")).toHaveCount(0);
});
