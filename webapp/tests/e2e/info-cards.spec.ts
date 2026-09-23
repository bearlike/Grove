import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY, FIXTURE_PEEK } from "./_fixtures";
import { dp } from "./density";
import type {
  DashboardSnapshotView,
  PhaseView,
  TicketProviderView,
  TicketRef,
  TodoProgressView,
  TokenClassesView,
  WorkspacePeekView,
} from "@/lib/grove/api";

const id = FIXTURE_PEEK.state.id;
const providers: TicketProviderView[] = [
  {
    provider: "gitea",
    label: "Fictional Forge",
    configured: false,
    context: "acme/widget",
  },
];

const longPhaseNote =
  "A deliberately long fictional phase report explains the dependency in enough words to prove that the region grows with prose instead of hiding its ending behind a fixed-height crop.";
const longTicketNote =
  "A deliberately long fictional ticket report names the verification evidence, explains why the follow-up remains necessary, and must wrap inside the row instead of running beyond the work panel.";

const phase = (overrides: Partial<PhaseView> = {}): PhaseView => ({
  phase: "build",
  note: "Working through the fictional workspace card layout.",
  blocked: false,
  updated_at: "2026-09-07T12:00:00.000Z",
  index: 2,
  total: 6,
  tickets: [],
  ...overrides,
});

const todo = (overrides: Partial<TodoProgressView> = {}): TodoProgressView => ({
  total: 4,
  completed: 1,
  in_progress: 2,
  pending: 1,
  ...overrides,
});

const ticket = (overrides: Partial<TicketRef> = {}): TicketRef => ({
  provider: "gitea",
  id: "42",
  kind: "issue",
  title: "Make fictional workspace information cards responsive",
  url: "https://forge.example.test/acme/widget/issues/42",
  status: "open",
  draft: false,
  assignee: "reader",
  ambiguous: false,
  ...overrides,
});

interface Scenario {
  readonly refs?: readonly TicketRef[];
  readonly phase?: PhaseView | null;
  readonly todo?: TodoProgressView | null;
  /** Undefined omits the classes and selects the four-fact fallback. */
  readonly tokens?: TokenClassesView | null;
  readonly noSession?: boolean;
  readonly delayProviders?: boolean;
}

function snapshotFor({
  refs = [],
  phase: reportedPhase = phase(),
  todo: reportedTodo = todo(),
  tokens = {
    fresh_input: 7_654_321,
    cache_read: 98_765_432,
    cache_creation: 123_456,
    output: 654_321,
    provider_total: null,
    reasoning: null,
  },
  noSession = false,
}: Scenario): DashboardSnapshotView {
  const snapshot = structuredClone(FIXTURE_ACTIVITY);
  const workspace = snapshot.projects[0]!.workspaces[0]!;
  workspace.state.ticket_refs = [...refs];
  workspace.phase = reportedPhase;
  workspace.todo = reportedTodo;

  if (noSession) {
    workspace.sessions = [];
  } else {
    workspace.sessions[0]!.tokens = tokens ?? undefined;
    workspace.sessions[0]!.activity = {
      ...workspace.sessions[0]!.activity,
      human_turns: 0,
      tool_calls: 987_654,
      tokens_in: 1_234_567_890,
      tokens_out: 0,
    };
  }
  return snapshot;
}

function peekFor(refs: readonly TicketRef[]): WorkspacePeekView {
  const peek = structuredClone(FIXTURE_PEEK);
  peek.state.ticket_refs = [...refs];
  return peek;
}

/**
 * Keep the unusual payloads local to this spec and stop the fake stream from
 * replacing them a beat after the initial `/activity` read.
 */
async function useScenario(page: Page, current: () => Scenario): Promise<void> {
  await page.route("**/api/grove/activity", (route) =>
    route.fulfill({ json: snapshotFor(current()) }),
  );
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
  await page.route("**/api/grove/workspaces/*/peek", (route) =>
    route.fulfill({ json: peekFor(current().refs ?? []) }),
  );
  await page.route("**/api/grove/tickets/providers**", async (route) => {
    if (current().delayProviders)
      await new Promise((resolve) => setTimeout(resolve, 2_000));
    await route.fulfill({ json: providers });
  });
}

async function settled(page: Page): Promise<void> {
  await expect(page.getByTestId("work-panel")).toBeVisible();
  await page.getByTestId("pane-work").click();
  await page.getByTestId("work-panel-tab-info").click();
  await expect(page.getByTestId("info-tab")).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

type Box = { x: number; y: number; width: number; height: number };
type MetricsLayout = {
  readonly panelWidth: number;
  readonly cells: readonly (Box & {
    readonly scrollWidth: number;
    readonly clientWidth: number;
    readonly scrollHeight: number;
    readonly clientHeight: number;
  })[];
};

async function metricsLayout(page: Page): Promise<MetricsLayout> {
  return page.getByTestId("metrics").evaluate((metrics) => {
    const panel = document.querySelector<HTMLElement>(
      "[data-testid='work-panel']",
    );
    if (!panel) throw new Error("the work panel is absent");
    return {
      panelWidth: panel.getBoundingClientRect().width,
      cells: [...metrics.children].map((cell) => {
        const element = cell as HTMLElement;
        const box = element.getBoundingClientRect();
        return {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
          scrollHeight: element.scrollHeight,
          clientHeight: element.clientHeight,
        };
      }),
    };
  });
}

function rows(layout: MetricsLayout): MetricsLayout["cells"][number][][] {
  const answer: MetricsLayout["cells"][number][][] = [];
  for (const cell of layout.cells) {
    const row = answer.find(
      (candidate) => Math.abs(candidate[0]!.y - cell.y) <= 1,
    );
    if (row) row.push(cell);
    else answer.push([cell]);
  }
  return answer;
}

function assertRegularGrid(
  layout: MetricsLayout,
  expectedColumns: number,
): void {
  const gridRows = rows(layout);
  expect(gridRows).toHaveLength(layout.cells.length / expectedColumns);
  for (const row of gridRows) {
    expect(row).toHaveLength(expectedColumns);
    for (const cell of row) {
      expect(cell.width).toBeCloseTo(row[0]!.width, 0);
      expect(cell.height).toBeCloseTo(row[0]!.height, 0);
      expect(cell.height).toBeGreaterThanOrEqual(dp(64));
      expect(cell.scrollWidth - cell.clientWidth).toBeLessThanOrEqual(1);
      expect(cell.scrollHeight - cell.clientHeight).toBeLessThanOrEqual(1);
    }
    for (let index = 1; index < row.length; index += 1) {
      expect(
        row[index]!.x - (row[index - 1]!.x + row[index - 1]!.width),
      ).toBeCloseTo(dp(8), 0);
    }
  }
  for (let index = 1; index < gridRows.length; index += 1) {
    const above = gridRows[index - 1]![0]!;
    const below = gridRows[index]![0]!;
    expect(below.y - (above.y + above.height)).toBeCloseTo(dp(8), 0);
  }
}

/**
 * This card system intentionally reacts to the width of the resizable work
 * panel, not the window. A static render once passed while a docked panel made
 * one metric overflow and left another alone on a row, so these measurements
 * take their coordinate from the real panel after hydration and fonts settle.
 */
test.describe("Info cards keep their facts legible in the work panel", () => {
  test("reflows six facts without orphaning one, in light and dark", async ({
    page,
  }) => {
    let scenario: Scenario = {};
    await useScenario(page, () => scenario);

    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      for (const [viewport, columns, minimumPanelWidth] of [
        [320, 2, 280],
        [400, 2, 360],
        // The column thresholds are rem values. The 80% root moves the
        // six-column threshold down to this panel width without changing the
        // design-system thresholds themselves.
        [560, 3, 520],
        [700, 6, 660],
        [1_300, 6, 890],
      ] as const) {
        await page.setViewportSize({ width: viewport, height: 1_100 });
        await page.goto(`/w/${id}`);
        await settled(page);

        const layout = await metricsLayout(page);
        expect(
          layout.panelWidth,
          `${theme} at ${viewport}px viewport`,
        ).toBeGreaterThanOrEqual(minimumPanelWidth);
        expect(layout.cells).toHaveLength(6);
        assertRegularGrid(layout, columns);

        const phaseTrack = await page
          .getByTestId("phase-meter")
          .evaluate((meter) => {
            const dots = [...meter.querySelectorAll("ol svg")].map((dot) => {
              const box = dot.getBoundingClientRect();
              return { y: box.y, width: box.width, height: box.height };
            });
            return dots;
          });
        expect(phaseTrack).toHaveLength(6);
        expect(phaseTrack.every((dot) => dot.width > 0 && dot.height > 0)).toBe(
          true,
        );
        expect(new Set(phaseTrack.map((dot) => Math.round(dot.y))).size).toBe(
          1,
        );

        await expect(page.getByTestId("task-phase-summary")).toContainText(
          "Build",
        );
        const labels = await page
          .getByTestId("phase-meter")
          .locator("ol li > span > span")
          .evaluateAll((elements) =>
            elements
              .filter((element) => getComputedStyle(element).display !== "none")
              .map((element) => element.textContent?.trim()),
          );
        expect(labels.filter(Boolean)).toHaveLength(6);
      }
    }
    await page.emulateMedia({ colorScheme: null });
  });

  test("keeps four facts, long reports, ticket meaning, and keyboard access intact", async ({
    page,
  }) => {
    const refs = [ticket()];
    let scenario: Scenario = {
      refs,
      phase: phase({
        note: longPhaseNote,
        tickets: [
          {
            ticket: "gitea:42",
            phase: "verify",
            note: longTicketNote,
            blocked: false,
            index: 3,
          },
        ],
      }),
      todo: todo(),
      tokens: null,
    };
    await useScenario(page, () => scenario);

    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width: 320, height: 1_100 });
      await page.goto(`/w/${id}`);
      await settled(page);

      const layout = await metricsLayout(page);
      expect(layout.cells).toHaveLength(4);
      assertRegularGrid(layout, 2);
      await expect(page.getByTestId("metric-input_total")).toBeVisible();
      await expect(page.getByTestId("metric-fresh_input")).toHaveCount(0);
      await expect(page.getByTestId("metric-cache_read")).toHaveCount(0);
      await expect(page.getByTestId("metric-cache_write")).toHaveCount(0);

      const prose = await page.evaluate(() => {
        const read = (testId: string) => {
          const element = document.querySelector<HTMLElement>(
            `[data-testid="${testId}"]`,
          );
          if (!element) throw new Error(`${testId} is absent`);
          const box = element.getBoundingClientRect();
          return {
            height: box.height,
            scrollWidth: element.scrollWidth,
            clientWidth: element.clientWidth,
            scrollHeight: element.scrollHeight,
            clientHeight: element.clientHeight,
          };
        };
        const tab = document.querySelector<HTMLElement>(
          "[data-testid='info-tab']",
        )!;
        const inspected = [
          ...tab.querySelectorAll<HTMLElement>("[data-testid]"),
        ]
          .filter((element) => element.getClientRects().length > 0)
          .map((element) => ({
            name: element.dataset.testid,
            overflow: element.scrollWidth - element.clientWidth,
          }));
        return {
          documentOverflow:
            document.documentElement.scrollWidth -
            document.documentElement.clientWidth,
          phase: read("phase-note"),
          ticket: read("ticket-note"),
          inspected,
        };
      });
      expect(prose.phase.height).toBeGreaterThan(50);
      expect(prose.ticket.height).toBeGreaterThan(32);
      for (const region of [prose.phase, prose.ticket]) {
        expect(region.scrollWidth - region.clientWidth).toBeLessThanOrEqual(1);
        expect(region.scrollHeight - region.clientHeight).toBeLessThanOrEqual(
          1,
        );
      }
      expect(prose.documentOverflow).toBeLessThanOrEqual(0);
      expect(prose.inspected.length).toBeGreaterThan(10);
      for (const inspected of prose.inspected) {
        expect(
          inspected.overflow,
          `overflow in ${inspected.name}`,
        ).toBeLessThanOrEqual(1);
      }

      const status = page.getByTestId("ticket-status");
      await expect(status).toHaveText("Open");
      await expect(status).toHaveAttribute("data-state", "open");
      await expect(page.getByTestId("ticket-glyph")).toHaveAttribute(
        "data-state",
        "open",
      );
      const semanticColour = await status.evaluate((element) => ({
        colour: getComputedStyle(element).color,
        neutral: getComputedStyle(
          document.querySelector("[data-testid='ticket-count']")!,
        ).color,
      }));
      expect(semanticColour.colour).not.toBe(semanticColour.neutral);

      const metric = page.getByTestId("metric-turns");
      await expect(metric).not.toHaveAttribute("role", /button|link/);
      expect(
        await metric.evaluate((element) => ({
          tabIndex: (element as HTMLElement).tabIndex,
          hasInteractiveRole: element.matches('[role="button"], [role="link"]'),
        })),
      ).toEqual({ tabIndex: -1, hasInteractiveRole: false });

      await expect(
        page.getByRole("progressbar", { name: "Checklist completion" }),
      ).toHaveAttribute("aria-valuenow", "25");
      await expect(
        page.getByRole("progressbar", {
          name: "Average ticket phase progress across every attached ticket",
        }),
      ).toHaveAttribute("aria-valuenow", "60");

      const link = page.getByRole("link", {
        name: /42.*Make fictional workspace/i,
      });
      for (let index = 0; index < 30; index += 1) {
        if (
          await link.evaluate((element) => document.activeElement === element)
        )
          break;
        await page.keyboard.press("Tab");
      }
      await expect(link).toBeFocused();
      expect(
        await link.evaluate((element) => element.matches(":focus-visible")),
      ).toBe(true);
      expect(
        await link.evaluate((element) => getComputedStyle(element).boxShadow),
      ).not.toBe("none");
    }
    await page.emulateMedia({ colorScheme: null });
  });

  test("grows cells at 200% text size instead of cutting their contents", async ({
    page,
  }) => {
    let scenario: Scenario = {};
    await useScenario(page, () => scenario);
    await page.setViewportSize({ width: 320, height: 1_100 });
    await page.goto(`/w/${id}`);
    await settled(page);

    const before = await metricsLayout(page);
    // CSS zoom changes rendering scale but not the authoring font metrics. A root
    // font-size override models text enlargement: every rem-based type and inset
    // grows, so this catches the fixed-height crop that deviceScaleFactor cannot.
    await page.addStyleTag({ content: "html { font-size: 32px !important; }" });
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    const after = await metricsLayout(page);

    expect(after.cells).toHaveLength(before.cells.length);
    expect(
      after.cells.some(
        (cell, index) => cell.height > before.cells[index]!.height + 1,
      ),
    ).toBe(true);
    for (const cell of after.cells) {
      expect(cell.height).toBeGreaterThanOrEqual(dp(64));
      expect(cell.scrollWidth - cell.clientWidth).toBeLessThanOrEqual(1);
      expect(cell.scrollHeight - cell.clientHeight).toBeLessThanOrEqual(1);
    }
  });
});

/**
 * These are data states, not styling variants. Reusing a page keeps the browser
 * cost modest while a complete reload resets the query cache, so each assertion
 * still observes the exact wire shape named beside it.
 */
test.describe("Info cards name missing and exceptional workspace reports", () => {
  test("renders every absence, zero, loading, and bounded-list state honestly", async ({
    page,
  }) => {
    let scenario: Scenario = {};
    await useScenario(page, () => scenario);
    await page.setViewportSize({ width: 700, height: 1_100 });

    const visit = async (next: Scenario): Promise<void> => {
      scenario = next;
      await page.goto(`/w/${id}`);
      await settled(page);
    };

    await visit({ phase: null, todo: null, refs: [] });
    await expect(page.getByTestId("task-empty")).toBeVisible();
    await expect(page.getByTestId("phase-meter")).toHaveCount(0);
    await expect(page.getByTestId("checklist-meter")).toHaveCount(0);
    await expect(page.getByTestId("tickets-empty")).toBeVisible();

    await visit({ phase: phase({ note: "   " }), todo: null });
    await expect(page.getByTestId("phase-meter")).toBeVisible();
    await expect(page.getByTestId("phase-note")).toHaveCount(0);
    await expect(page.getByTestId("checklist-meter")).toHaveCount(0);

    await visit({ phase: phase({ blocked: true, note: "" }), todo: null });
    await expect(page.getByTestId("phase-blocked")).toBeVisible();
    await expect(page.getByTestId("phase-note")).toHaveCount(0);

    await visit({
      phase: phase({
        blocked: true,
        note: "A fictional dependency is unavailable.",
      }),
    });
    await expect(page.getByTestId("phase-blocked")).toBeVisible();
    await expect(page.getByTestId("phase-note")).toBeVisible();

    await visit({ phase: null, todo: todo() });
    await expect(page.getByTestId("checklist-meter")).toBeVisible();
    await expect(page.getByTestId("task-empty")).toHaveCount(0);

    const noTitleNoClaim = ticket({
      id: "43",
      title: null,
      url: null,
      assignee: null,
    });
    await visit({ refs: [noTitleNoClaim], phase: phase({ tickets: [] }) });
    await expect(page.getByText("No title recorded")).toBeVisible();
    await expect(page.getByTestId("ticket-claim")).toHaveText(
      /No phase reported/,
    );

    const many = Array.from({ length: 8 }, (_, index) =>
      ticket({
        id: String(100 + index),
        title: `Fictional linked ticket ${index + 1}`,
        url: null,
      }),
    );
    await visit({ refs: many, phase: phase({ tickets: [] }) });
    await expect(page.getByTestId("tickets-bounded")).toBeVisible();
    await expect(page.getByTestId("ticket-count")).toHaveText("8 linked");
    await expect(page.getByTestId("ticket-refs").locator("li")).toHaveCount(8);

    await visit({
      tokens: {
        fresh_input: null,
        cache_read: 0,
        cache_creation: 9_876_543_210,
        output: 0,
      },
    });
    await expect(page.getByTestId("metrics")).toHaveAttribute(
      "data-facts",
      "6",
    );
    await expect(page.getByTestId("metric-fresh_input")).toContainText(
      "Not measured",
    );
    await expect(page.getByTestId("metric-cache_read")).toContainText("0");
    await expect(page.getByTestId("metric-output")).toContainText("0");
    await expect(page.getByTestId("metric-cache_write")).toContainText(/9\.9B/);

    scenario = { refs: [], delayProviders: true };
    await page.goto(`/w/${id}`);
    await settled(page);
    await expect(page.getByTestId("tickets-providers-loading")).toBeVisible();
  });
});
