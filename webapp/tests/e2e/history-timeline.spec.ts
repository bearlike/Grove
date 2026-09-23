import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import { FIXTURE_PEEK } from "./_fixtures";
import type {
  TicketProviderView,
  TicketRef,
  WorkspaceHistoryView,
} from "@/lib/grove/api";

// An explicit zone makes the day headings and 24-hour times deterministic. The
// records straddle a local midnight so grouping cannot accidentally use UTC.
test.use({ locale: "en-GB", timezoneId: "America/Los_Angeles" });

const workspaceId = FIXTURE_PEEK.state.id;
const LOCAL_DAY = "2026-09-14";
const PREVIOUS_LOCAL_DAY = "2026-09-13";

const title = {
  done: "Reported Handoff",
  delivering: "Reported Deliver",
  verifying: "Reported Verify",
  renamed: "Name recorded",
  ticket: "Ticket first recorded",
} as const;

const configuredGitea: TicketProviderView[] = [
  {
    provider: "gitea",
    label: "Fictional Forge",
    configured: true,
    context: "acme/widget",
  },
];

const resolvedTicketUrl = "https://forge.example.test/acme/widget/issues/730";

const resolvedTicket: TicketRef = {
  provider: "gitea",
  id: "730",
  kind: "issue",
  title: "Timeline follows every recorded claim",
  url: resolvedTicketUrl,
  status: "open",
  draft: false,
  assignee: null,
  ambiguous: false,
};

const mixedHistory: WorkspaceHistoryView = {
  name: {
    workspace_id: workspaceId,
    title: "Release preparation",
    description: "The current title remains available after the workspace is gone.",
    repo_root: "/workspace/acme/widget",
    first_seen: "2026-09-13T00:15:00.000Z",
    last_seen: "2026-09-14T23:42:00.000Z",
    deleted_at: null,
  },
  names: [
    {
      title: "Documentation polish",
      description: "Initial title and description are durable history, not a live-state fallback.",
      recorded_at: "2026-09-13T00:15:00.000Z",
    },
    {
      title: "Release preparation",
      description: "Preparing the release after the verification gates passed.",
      recorded_at: "2026-09-14T23:24:00.000Z",
    },
  ],
  progress: [
    {
      recorded_at: "2026-09-14T23:42:00.000Z",
      phase: "handoff",
      blocked: false,
      note: "Published the release and verified the preview.",
      ticket_key: null,
    },
    {
      recorded_at: "2026-09-14T23:38:00.000Z",
      phase: "deliver",
      blocked: false,
      note: "Preparing the release after the verification gates passed.",
      ticket_key: null,
    },
    {
      recorded_at: "2026-09-14T23:30:00.000Z",
      phase: "verify",
      blocked: false,
      note: "Checking the layout and keyboard navigation.",
      ticket_key: "gitea:730",
    },
    {
      recorded_at: "2026-09-14T00:10:00.000Z",
      phase: "verify",
      blocked: true,
      note: "Preview service unavailable. Waiting for access.",
      ticket_key: null,
    },
    {
      recorded_at: "2026-09-14T00:05:00.000Z",
      phase: null,
      blocked: false,
      note: "The provider recorded no phase, but this note must remain readable.",
      ticket_key: null,
    },
  ],
  tickets: [
    {
      ticket_key: "gitea:730",
      provider: "gitea",
      ticket_id: "730",
      kind: "issue",
      first_seen: "2026-09-14T23:18:00.000Z",
      last_seen: "2026-09-14T23:42:00.000Z",
    },
  ],
};

function unconfiguredTicketHistory(): WorkspaceHistoryView {
  return {
    ...mixedHistory,
    progress: [
      ...(mixedHistory.progress ?? []),
      {
        recorded_at: "2026-09-13T23:55:00.000Z",
        phase: "build",
        blocked: false,
        note: "This provider is recorded but deliberately unconfigured.",
        ticket_key: "github:999",
      },
    ],
  };
}

function manyProgressHistory(): WorkspaceHistoryView {
  return {
    ...mixedHistory,
    names: [],
    tickets: [],
    progress: Array.from({ length: 154 }, (_, index) => ({
      // Index zero is latest, so the search target at 121 proves the query
      // reaches records the initial 50-row render did not mount.
      recorded_at: new Date(Date.parse("2026-09-15T01:00:00.000Z") - index * 60_000).toISOString(),
      phase: "build",
      blocked: false,
      note:
        index === 121
          ? "Needle event 121 is outside the initial batch."
          : `Recorded progress event ${index}.`,
      ticket_key: null,
    })),
  };
}

async function openHistory(
  page: Page,
  history: WorkspaceHistoryView,
  resolveTicket = false,
): Promise<void> {
  await page.route("**/api/grove/workspaces/*/history", (route) =>
    route.fulfill({ json: history }),
  );
  if (resolveTicket) {
    await page.route("**/api/grove/tickets/providers**", (route) =>
      route.fulfill({ json: configuredGitea }),
    );
    await page.route("**/api/grove/tickets/gitea/730?**", (route) =>
      route.fulfill({ json: resolvedTicket }),
    );
  }
  await page.goto(`/w/${workspaceId}`);
  await expect(page.getByTestId("work-panel")).toBeVisible();
  await page.getByTestId("pane-work").click();
  await page.getByTestId("work-panel-tab-info").click();
  await expect(page.getByTestId("workspace-history-trigger")).toBeVisible();
  await page.getByTestId("workspace-history-trigger").click();
  await expect(page.getByTestId("workspace-history-dialog")).toBeVisible();
}

function historyEvents(page: Page): Locator {
  return page.getByTestId("workspace-history-dialog").getByTestId("history-event");
}

async function tabTo(page: Page, target: Locator): Promise<void> {
  for (let index = 0; index < 16; index += 1) {
    if (await target.evaluate((element) => document.activeElement === element)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error("keyboard focus did not reach the expected history control");
}

test.describe("workspace change history", () => {
  test("merges progress, names, and tickets into one latest-first local chronology", async ({ page }) => {
    await openHistory(page, mixedHistory);

    const dialog = page.getByTestId("workspace-history-dialog");
    await expect(dialog.getByRole("searchbox", { name: "Search history" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
    for (const filter of ["Progress", "Names", "Tickets"] as const) {
      await expect(dialog.getByRole("button", { name: filter, exact: true })).toHaveAttribute("aria-pressed", "false");
    }

    // The first five records must be from different source collections in their
    // actual chronological order, not three collection-shaped lists stacked by
    // section. `tickets` participates at `first_seen`, never `last_seen`.
    const events = historyEvents(page);
    await expect(events).toHaveCount(8);
    expect(await events.evaluateAll((rows) => rows.every((row) => row.tagName === "LI"))).toBe(true);
    await expect(events.nth(0)).toContainText(title.done);
    await expect(events.nth(1)).toContainText(title.delivering);
    await expect(events.nth(2)).toContainText(title.verifying);
    await expect(events.nth(2)).toContainText("gitea:730");
    await expect(events.nth(3)).toContainText(title.renamed);
    await expect(events.nth(3)).toContainText("Release preparation");
    await expect(events.nth(4)).toContainText(title.ticket);
    await expect(events.nth(4)).toContainText("gitea:730");

    await expect(dialog.getByRole("heading", { level: 3, name: LOCAL_DAY, exact: true })).toBeVisible();
    await expect(dialog.getByRole("heading", { level: 3, name: PREVIOUS_LOCAL_DAY, exact: true })).toBeVisible();
    await expect(events.nth(0).getByText("16:42", { exact: true })).toBeVisible();
    await expect(events.nth(1).getByText("16:38", { exact: true })).toBeVisible();
    await expect(events.nth(4).getByText("16:18", { exact: true })).toBeVisible();

    await expect(events.nth(3)).toContainText("Preparing the release after the verification gates passed.");
    await expect(events.nth(4)).toContainText("Issue");
    await expect(events.nth(4)).toContainText("Last observed 2026-09-14 at 16:42");
    await expect(events.filter({ hasText: "Preview service unavailable. Waiting for access." })).toContainText(/blocked/i);
    // A null phase is a historical fact, not a malformed row to omit.
    await expect(events.filter({ hasText: "The provider recorded no phase, but this note must remain readable." })).toHaveCount(1);

    for (const [filter, count] of [
      ["Progress", 5],
      ["Names", 2],
      ["Tickets", 1],
    ] as const) {
      await dialog.getByRole("button", { name: filter, exact: true }).click();
      await expect(events).toHaveCount(count);
    }
    await dialog.getByRole("button", { name: "All", exact: true }).click();
    await expect(events).toHaveCount(8);
  });

  test("resolves configured ticket references into safe dotted-underlined links", async ({ page }) => {
    // The durable record is absent for a legacy workspace, but InfoTab still
    // holds the authenticated workspace's repo root. Ticket enrichment must use
    // that live authorization context rather than making a durable name required.
    await openHistory(page, { ...unconfiguredTicketHistory(), name: null }, true);

    const events = historyEvents(page);
    const progressRef = events
      .filter({ hasText: "Checking the layout and keyboard navigation." })
      .getByRole("link", { name: "gitea:730", exact: true });
    const ticketRef = events
      .filter({ hasText: "Ticket first recorded" })
      .getByRole("link", { name: "gitea:730", exact: true });

    for (const link of [progressRef, ticketRef]) {
      await expect(link).toHaveAttribute("href", resolvedTicketUrl);
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", /noopener/);
      await expect(link).toHaveAttribute("rel", /noreferrer/);
      await expect(link).toHaveText("gitea:730");
      expect(await link.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          line: style.textDecorationLine,
          style: style.textDecorationStyle,
        };
      })).toEqual({ line: "underline", style: "dotted" });
    }

    await page.context().route(resolvedTicketUrl, (route) =>
      route.fulfill({ contentType: "text/html", body: "ticket opened" }),
    );
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      progressRef.click(),
    ]);
    await popup.waitForURL(resolvedTicketUrl);
    await expect(popup.locator("body")).toHaveText("ticket opened");

    const unknown = events.filter({ hasText: "This provider is recorded but deliberately unconfigured." });
    await expect(unknown).toContainText("github:999");
    await expect(unknown.getByRole("link", { name: "github:999", exact: true })).toHaveCount(0);
  });

  test("searches the complete chronology, extends its batch by 50, and clears an empty type filter", async ({ page }) => {
    await openHistory(page, manyProgressHistory());

    const dialog = page.getByTestId("workspace-history-dialog");
    const events = historyEvents(page);
    const search = dialog.getByRole("searchbox", { name: "Search history" });
    await expect(events).toHaveCount(50);
    await expect(dialog.getByText("Showing 50 of 154 recorded events", { exact: true })).toBeVisible();

    await dialog.getByRole("button", { name: "Show more", exact: true }).click();
    await expect(events).toHaveCount(100);
    await expect(dialog.getByText("Showing 100 of 154 recorded events", { exact: true })).toBeVisible();

    await search.fill("Needle event 121");
    await expect(events).toHaveCount(1);
    await expect(events.first()).toContainText("Needle event 121 is outside the initial batch.");
    await expect(dialog.getByText("Showing 1 of 1 recorded events", { exact: true })).toBeVisible();

    await dialog.getByRole("button", { name: "Names", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Names", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(dialog.getByText("No matching events", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Clear filters", exact: true }).click();

    await expect(search).toHaveValue("");
    await expect(dialog.getByRole("button", { name: "All", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(events).toHaveCount(50);
    await expect(dialog.getByText("Showing 50 of 154 recorded events", { exact: true })).toBeVisible();
  });

  for (const theme of ["light", "dark"] as const) {
    test(`keeps the timeline operable and uncut at desktop and a 390px narrow dialog in ${theme}`, async ({ page }, testInfo: TestInfo) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.clock.setFixedTime(new Date("2026-09-15T00:00:00.000Z"));
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      await page.setViewportSize({ width: 1280, height: 900 });
      await openHistory(page, mixedHistory);
      await expect(page.locator("html")).toHaveClass(new RegExp(`(?:^|\\s)${theme}(?:\\s|$)`));
      const dialog = page.getByTestId("workspace-history-dialog");
      const desktopDone = historyEvents(page).filter({ hasText: "Published the release and verified the preview." });
      await expect(desktopDone).toHaveCount(1);
      const desktopAge = desktopDone.getByText("18m ago", { exact: true });
      await expect(desktopAge).toBeVisible();
      const ageMetrics = await desktopAge.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          height: element.getBoundingClientRect().height,
          lineHeight:
            style.lineHeight === "normal"
              ? Number.parseFloat(style.fontSize) * 1.2
              : Number.parseFloat(style.lineHeight),
        };
      });
      expect(ageMetrics.height).toBeLessThanOrEqual(ageMetrics.lineHeight * 1.5);
      const desktopTime = desktopDone.getByText("16:42", { exact: true });
      const desktopTitle = desktopDone.getByText(title.done, { exact: true });
      const [desktopTimeBox, desktopTitleBox, desktopTimeFont, titleFont] = await Promise.all([
        desktopTime.boundingBox(),
        desktopTitle.boundingBox(),
        desktopTime.evaluate((element) => getComputedStyle(element).fontSize),
        desktopTitle.evaluate((element) => getComputedStyle(element).fontSize),
      ]);
      expect(desktopTimeBox).not.toBeNull();
      expect(desktopTitleBox).not.toBeNull();
      // Wide chronology: the clock precedes the rail and content.
      expect(desktopTimeBox!.x).toBeLessThan(desktopTitleBox!.x);
      expect(Number.parseFloat(desktopTimeFont)).toBeLessThan(Number.parseFloat(titleFont));
      const desktopScreenshot = testInfo.outputPath(`history-${theme}-desktop.png`);
      await dialog.screenshot({ path: desktopScreenshot });
      await testInfo.attach(`history-timeline-${theme}-desktop`, {
        path: desktopScreenshot,
        contentType: "image/png",
      });

      await page.keyboard.press("Escape");
      await expect(dialog).not.toBeVisible();
      await page.setViewportSize({ width: 390, height: 844 });
      const trigger = page.getByTestId("workspace-history-trigger");
      await expect(trigger).toBeVisible();
      await trigger.click();
      await expect(dialog).toBeVisible();

      const search = dialog.getByRole("searchbox", { name: "Search history" });
      await tabTo(page, search);
      await expect(search).toBeFocused();
      expect(await search.evaluate((element) => element.matches(":focus-visible"))).toBe(true);
      await page.keyboard.press("Tab");
      const all = dialog.getByRole("button", { name: "All", exact: true });
      await expect(all).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(all).toHaveAttribute("aria-pressed", "true");

      const narrowDone = historyEvents(page).filter({ hasText: "Published the release and verified the preview." });
      await expect(narrowDone).toHaveCount(1);
      await expect(narrowDone.getByText("18m ago", { exact: true })).toBeVisible();
      const narrowTime = narrowDone.getByText("16:42", { exact: true });
      const narrowTitle = narrowDone.getByText(title.done, { exact: true });
      const [narrowTimeBox, narrowTitleBox, narrowTimeFont, overflow] = await Promise.all([
        narrowTime.boundingBox(),
        narrowTitle.boundingBox(),
        narrowTime.evaluate((element) => getComputedStyle(element).fontSize),
        page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
      ]);
      expect(narrowTimeBox).not.toBeNull();
      expect(narrowTitleBox).not.toBeNull();
      // Narrow chronology: the clock follows the event contents rather than
      // creating a separate, overflowing time rail.
      expect(narrowTimeBox!.y).toBeGreaterThan(narrowTitleBox!.y);
      expect(narrowTimeFont).toBe(desktopTimeFont);
      expect(overflow, `${theme} narrow history overflow`).toBeLessThanOrEqual(1);
      const narrowScreenshot = testInfo.outputPath(`history-${theme}-390.png`);
      await dialog.screenshot({ path: narrowScreenshot });
      await testInfo.attach(`history-timeline-${theme}-390`, {
        path: narrowScreenshot,
        contentType: "image/png",
      });

      await page.keyboard.press("Escape");
    });
  }
});
