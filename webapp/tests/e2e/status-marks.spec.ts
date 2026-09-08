import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";
import type { DashboardSnapshotView } from "@/lib/grove/api";
import type { AgentState, TaskPhase } from "@/components/grove/fleet/types";

type StatusRow = {
  readonly id: string;
  readonly title: string;
  readonly state: AgentState;
  readonly needsAttention: boolean;
  readonly phase: TaskPhase | null;
  readonly blocked?: boolean;
  readonly note?: string | null;
};

const ROWS: readonly StatusRow[] = [
  {
    id: "status-waiting-verifying",
    title: "Waiting and verifying",
    state: "waiting",
    needsAttention: true,
    phase: "verifying",
  },
  {
    id: "status-blocked-implementing",
    title: "Agent blocked while implementing",
    state: "blocked",
    needsAttention: true,
    phase: "implementing",
  },
  {
    id: "status-error",
    title: "Session error",
    state: "error",
    needsAttention: true,
    phase: null,
  },
  {
    id: "status-done",
    title: "Task complete",
    state: "working",
    needsAttention: false,
    phase: "done",
  },
  {
    id: "status-phase-blocked",
    title: "Phase blocked",
    state: "working",
    needsAttention: false,
    phase: "implementing",
    blocked: true,
    note: "waiting for the release window",
  },
  {
    id: "status-no-phase",
    title: "No phase reported",
    state: "working",
    needsAttention: false,
    phase: null,
  },
];

function snapshotFor(rows: readonly StatusRow[]): DashboardSnapshotView {
  const template = FIXTURE_ACTIVITY.projects[0].workspaces[0];
  const workspaces = rows.map((row, index) => ({
    ...structuredClone(template),
    state: {
      ...structuredClone(template.state),
      id: row.id,
      title: row.title,
      created_at: new Date(Date.now() - index * 60_000).toISOString(),
    },
    sessions: [
      {
        ...structuredClone(template.sessions[0]),
        activity: {
          ...structuredClone(template.sessions[0].activity),
          state: row.state,
          needs_attention: row.needsAttention,
        },
      },
    ],
    needs_attention: row.needsAttention,
    phase: row.phase
      ? {
          phase: row.phase,
          note: row.note ?? null,
          updated_at: new Date().toISOString(),
          index: ["scoping", "planning", "implementing", "verifying", "delivering", "done"].indexOf(
            row.phase,
          ),
          total: 6,
          blocked: row.blocked ?? false,
          tickets: [],
        }
      : null,
  }));

  return {
    ...structuredClone(FIXTURE_ACTIVITY),
    projects: [
      { ...structuredClone(FIXTURE_ACTIVITY.projects[0]), workspaces },
    ],
    total_workspaces: workspaces.length,
    needs_attention: rows.filter((row) => row.needsAttention).length,
  };
}

async function useFleet(page: Page): Promise<void> {
  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshotFor(ROWS) }));
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
}

test.describe("fleet status marks", () => {
  test("anchors the blocked planning flag to its icon instead of its label", async ({ page }) => {
    const planning = snapshotFor([
      { id: "planning-blocked", title: "Planning with a dependency", state: "waiting", needsAttention: true, phase: "planning", blocked: true },
      { id: "planning-ready", title: "Planning normally", state: "working", needsAttention: false, phase: "planning" },
    ]);
    await page.route("**/api/grove/activity", route => route.fulfill({ json: planning }));
    await page.route("**/api/grove/events", route => route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }));
    await page.goto("/");
    const phase = page.locator('[data-workspace-id="planning-blocked"] [data-testid="fleet-row-phase-mark"]');
    await expect(phase).toContainText("Planning");
    const geometry = await phase.evaluate(el => {
      const icons = el.querySelectorAll("svg");
      const main = icons[0].getBoundingClientRect();
      const flag = icons[1].getBoundingClientRect();
      return { mainRight: main.right, flagLeft: flag.left, flagRight: flag.right };
    });
    expect(geometry.flagLeft).toBeLessThan(geometry.mainRight);
    expect(geometry.flagRight - geometry.mainRight).toBeLessThanOrEqual(4);
    await expect(page.locator('[data-workspace-id="planning-ready"] [data-testid="fleet-row-phase-mark"] svg')).toHaveCount(1);
  });

  test("keeps independent attention and phase marks readable in both themes", async ({ page }) => {
    await useFleet(page);
    await page.setViewportSize({ width: 1440, height: 900 });

    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.goto("/");
      await expect(page.getByTestId("fleet-row")).toHaveCount(ROWS.length);

      const waiting = page.locator('[data-workspace-id="status-waiting-verifying"]');
      await expect(waiting.getByTestId("fleet-row-attention-mark")).toHaveAttribute(
        "aria-label",
        "Waiting for you — the agent asked a question",
      );
      await expect(waiting.getByTestId("fleet-row-phase-mark")).toHaveAttribute(
        "aria-label",
        "Verifying — step 4 of 6",
      );

      const blocked = page.locator('[data-workspace-id="status-blocked-implementing"]');
      await expect(blocked.getByTestId("fleet-row-attention-mark")).toHaveAttribute(
        "aria-label",
        "Blocked — waiting on a permission or an external dependency",
      );
      await expect(blocked.getByTestId("fleet-row-phase-mark")).toHaveAttribute(
        "aria-label",
        "Implementing — step 3 of 6",
      );

      const error = page.locator('[data-workspace-id="status-error"]');
      await expect(error.getByTestId("fleet-row-attention-mark")).toHaveAttribute(
        "aria-label",
        "Error — the session hit a failure",
      );
      await expect(error.getByTestId("fleet-row-phase-mark")).toHaveCount(0);

      const done = page.locator('[data-workspace-id="status-done"]');
      await expect(done.getByTestId("fleet-row-attention-mark")).toHaveCount(0);
      await expect(done.getByTestId("fleet-row-phase-mark")).toHaveClass(/text-success/);

      const phaseBlocked = page.locator('[data-workspace-id="status-phase-blocked"]');
      await expect(phaseBlocked.getByTestId("fleet-row-phase-mark")).toHaveAttribute(
        "aria-label",
        "Implementing — blocked: waiting for the release window",
      );
      await expect(phaseBlocked.getByTestId("fleet-row-phase-mark")).toHaveClass(/text-warning/);

      const noPhase = page.locator('[data-workspace-id="status-no-phase"]');
      await expect(noPhase.getByTestId("fleet-row-attention-mark")).toHaveCount(0);
      await expect(noPhase.getByTestId("fleet-row-phase-mark")).toHaveCount(0);
      await waiting.locator("a").hover();
      const details = page.getByRole("tooltip").filter({ hasText: "Waiting and verifying" });
      await expect(details).toContainText("Waiting for you — the agent asked a question");
      await expect(details).toContainText("Verifying — step 4 of 6");
      await page.keyboard.press("Escape");

      await page.screenshot({ path: `test-results/status-marks-${theme}.png` });
    }
    await page.emulateMedia({ colorScheme: null });
  });
});
