import { expect, test } from "@playwright/test";
import type { DashboardSnapshotView, SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_ACTIVITY, FIXTURE_WORKSPACES } from "./_fixtures";
import { PLAN_CONFIRM } from "../fixtures/turns";

for (const theme of ["light", "dark"]) {
  test(`${theme} mailbox and composer cards lead with their marks`, async ({ page }) => {
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    const activity: DashboardSnapshotView = structuredClone(FIXTURE_ACTIVITY);
    for (const project of activity.projects) {
      for (const workspace of project.workspaces) {
        if (workspace.state.id !== FIXTURE_WORKSPACES[0].id) continue;
        for (const session of workspace.sessions) {
          if (session.activity) session.activity.questions = PLAN_CONFIRM;
        }
      }
    }
    await page.route("**/api/grove/activity", route => route.fulfill({ json: activity }));
    await page.route("**/api/grove/events", route => route.fulfill({
      contentType: "text/event-stream",
      body: `event: snapshot\ndata: ${JSON.stringify(activity)}\n\n`,
    }));
    await page.route("**/api/grove/workspaces/*/queue", route => route.fulfill({
      json: { supported: true, messages: [{ text: "Review the changes", sent_at: "2026-09-15T12:00:00Z", position: 0 }] },
    }));
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Review the change", started_at: null, entries: [{
        role: "notification", text: "Review received", mailbox: {
          sender: "Reviewer", recipient: "Implementer", subject: "Review", body: "The tests pass.", kind: "peer",
        },
      }] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();
    await page.getByTestId("tool-call-group").getByRole("button").first().click();
    await expect(page.getByTestId("mailbox-card-icon")).toBeVisible();
    await expect(page.getByTestId("queue-card").locator("svg.lucide-list-ordered")).toBeVisible();
    await expect(page.getByTestId("todo-card").locator("svg.lucide-list-todo")).toBeVisible();
    await expect(page.getByTestId("plan-approval").locator("svg.lucide-clipboard-check")).toBeVisible();
    await page.getByTestId("agent-message").screenshot({ path: test.info().outputPath(`mailbox-${theme}.png`) });
    await page.getByTestId("queue-card").screenshot({ path: test.info().outputPath(`queue-${theme}.png`) });
    await page.getByTestId("plan-approval").screenshot({ path: test.info().outputPath(`plan-${theme}.png`) });
  });
}
