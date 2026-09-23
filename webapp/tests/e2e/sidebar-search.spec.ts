import { expect, test, type Locator, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";
import type { DashboardSnapshotView, TicketRef } from "@/lib/grove/api";

const WIDGET_ROOT = "/work/widget";
const BASE_WORKSPACE_ID = FIXTURE_ACTIVITY.projects[0]!.workspaces[0]!.state.id;

const WORKSPACES = {
  root: { id: BASE_WORKSPACE_ID, title: "Frontend migration", branch: "main" },
  console: { id: "search-console-release", title: "Console status refresh", branch: "release/console" },
  ticket: { id: "search-ticket-worker", title: "Background worker", branch: "feat/worker" },
  api: { id: "search-api-contract", title: "Request contract", branch: "fix/request" },
} as const;

const TICKET: TicketRef = {
  provider: "gitea",
  id: "813",
  kind: "issue",
  title: "Fictional worker issue",
  url: "https://forge.example.test/acme/widget/issues/813",
  status: "open",
  draft: false,
  assignee: null,
  ambiguous: false,
};

type Workspace = DashboardSnapshotView["projects"][number]["workspaces"][number];

function workspace(
  spec: (typeof WORKSPACES)[keyof typeof WORKSPACES],
  repoRoot: string,
  index: number,
  ticketRefs: readonly TicketRef[] = [],
): Workspace {
  const template = FIXTURE_ACTIVITY.projects[0]!.workspaces[0]!;
  return {
    ...structuredClone(template),
    state: {
      ...structuredClone(template.state),
      id: spec.id,
      title: spec.title,
      branch: spec.branch,
      repo_root: repoRoot,
      worktree_path: `${repoRoot}/.worktrees/${spec.id}`,
      ticket_refs: [...ticketRefs],
      created_at: new Date(Date.now() - index * 60_000).toISOString(),
      updated_at: new Date(Date.now() - index * 30_000).toISOString(),
    },
  };
}

function snapshot(): DashboardSnapshotView {
  return {
    ...structuredClone(FIXTURE_ACTIVITY),
    projects: [
      {
        repo_root: WIDGET_ROOT,
        repo_name: "Widget",
        cwd: WIDGET_ROOT,
        workspaces: [workspace(WORKSPACES.root, WIDGET_ROOT, 0)],
        error: null,
      },
      {
        repo_root: WIDGET_ROOT,
        repo_name: "Widget",
        cwd: `${WIDGET_ROOT}/apps/console`,
        workspaces: [
          workspace(WORKSPACES.console, WIDGET_ROOT, 1),
          workspace(WORKSPACES.ticket, WIDGET_ROOT, 2, [TICKET]),
        ],
        error: null,
      },
      {
        repo_root: "/work/api",
        repo_name: "API",
        cwd: "/work/api",
        workspaces: [workspace(WORKSPACES.api, "/work/api", 3)],
        error: null,
      },
    ],
    total_workspaces: 4,
    needs_attention: 0,
  };
}

/** Prevent the fake stream from replacing the scenario's deliberately searchable rows. */
async function useSnapshot(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshot() }));
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
}

function searchDialog(page: Page): Locator {
  return page.getByTestId("workspace-search-dialog");
}

function searchInput(page: Page): Locator {
  return searchDialog(page).getByRole("combobox", { name: "Search workspaces", exact: true });
}

function searchResult(page: Page, title: string): Locator {
  return searchDialog(page).getByTestId("fleet-search-result").filter({ hasText: title });
}

async function openSearch(page: Page): Promise<void> {
  await page.getByTestId("sidebar-search-trigger").click();
  await expect(searchDialog(page)).toBeVisible();
}

async function selectProject(page: Page, name: string): Promise<void> {
  await page.getByRole("combobox", { name: "Project context", exact: true }).click();
  await page.getByRole("option", { name, exact: true }).click();
}

test.describe("sidebar workspace search", () => {
  test("keeps search reachable inside the collapsed rail", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await expect(page.locator('aside [data-testid="fleet-row"]')).toHaveCount(4);
    await page.getByTestId("shell-sidebar-toggle").click();
    const rail = page.locator("aside");
    const trigger = rail.getByTestId("sidebar-search-trigger");
    const bounds = await trigger.evaluate(el => {
      const a = el.closest("aside")!.getBoundingClientRect(), b = el.getBoundingClientRect();
      return { left: b.left, right: b.right, railLeft: a.left, railRight: a.right };
    });
    expect(bounds.left).toBeGreaterThanOrEqual(bounds.railLeft);
    expect(bounds.right).toBeLessThanOrEqual(bounds.railRight);
    await trigger.click();
    await expect(searchDialog(page)).toBeVisible();
  });

  test("keeps search in the brand band and motion control in the rail actions", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    await expect(page.locator('aside [data-testid="fleet-row"]')).toHaveCount(4);
    await expect(searchDialog(page)).toBeHidden();
    const brand = page.getByTestId("sidebar-brand-header");
    const rail = page.getByTestId("fleet-tree");
    await expect(brand.getByTestId("sidebar-search-trigger")).toHaveCount(1);
    await expect(brand.getByRole("button", { name: "Search workspaces", exact: true })).toHaveCount(1);
    await expect(rail.getByTestId("sidebar-search-trigger")).toHaveCount(0);
    await expect(page.locator("aside input[aria-label='Search workspaces']")).toHaveCount(0);

    const pause = rail.getByTestId("marquee-pause");
    await expect(pause).toHaveAttribute("aria-pressed", "false");
    await pause.click();
    await expect(pause).toHaveAttribute("aria-pressed", "true");
  });

  test("opens one named dialog from the trigger or the shared Ctrl/Cmd+K shortcut", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");

    await expect(page.locator('aside [data-testid="fleet-row"]')).toHaveCount(4);
    const opener = page.getByTestId("sidebar-search-trigger");
    await opener.focus();
    await page.keyboard.press("ControlOrMeta+K");
    await expect(page.getByRole("dialog", { name: "Search workspaces", exact: true })).toHaveCount(1);
    await expect(searchDialog(page).getByText("Recent workspaces", { exact: true })).toBeVisible();
    await expect(searchInput(page)).toBeFocused();

    // The shortcut and pointer affordance share a controller; a second shortcut
    // must not mount a second dialog or leave two listeners fighting focus.
    await page.keyboard.press("ControlOrMeta+K");
    await expect(searchDialog(page)).toBeHidden();
    await expect(opener).toBeFocused();
    await openSearch(page);

    await searchDialog(page).getByTestId("fleet-filter-trigger").click();
    const menu = page.getByTestId("fleet-filter-menu");
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("combobox", { name: "Project context", exact: true })).toHaveCount(0);
  });

  test("uses the fleet matcher, recovers from no results, and honors selected project scope", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    await selectProject(page, "Widget — console");
    await openSearch(page);
    await expect(searchDialog(page).getByTestId("fleet-search-result")).toHaveCount(2);
    await expect(searchResult(page, WORKSPACES.api.title)).toHaveCount(0);

    for (const [query, title] of [
      ["status refresh", WORKSPACES.console.title],
      ["release/console", WORKSPACES.console.title],
      ["gitea#813", WORKSPACES.ticket.title],
    ]) {
      await searchInput(page).fill(query);
      await expect(searchResult(page, title)).toHaveCount(1);
      await expect(searchDialog(page).getByTestId("fleet-search-result")).toHaveCount(1);
    }

    await searchInput(page).fill("does-not-exist");
    await expect(searchDialog(page).getByText(/no workspaces match/i)).toBeVisible();
    await searchInput(page).fill("");
    await expect(searchDialog(page).getByTestId("fleet-search-result")).toHaveCount(2);
  });

  test("matches a project name outside a narrowed context", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await openSearch(page);

    await searchInput(page).fill("api");
    await expect(searchResult(page, WORKSPACES.api.title)).toHaveCount(1);
  });

  test("navigates when a result is selected and restores trigger focus on Escape", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");

    const trigger = page.getByTestId("sidebar-search-trigger");
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(searchDialog(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(searchDialog(page)).toBeHidden();
    await expect(trigger).toBeFocused();

    await openSearch(page);
    await searchResult(page, WORKSPACES.console.title).click();
    await expect(page).toHaveURL(new RegExp(`/w/${WORKSPACES.console.id}$`));
    await expect(searchDialog(page)).toBeHidden();
  });

  test("closes the mobile sheet before opening the app-level search dialog", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 420, height: 900 });
    await page.goto("/");

    await page.getByTestId("shell-sidebar-sheet").click();
    const sheet = page.getByRole("dialog", { name: "Grove workspaces", exact: true });
    await expect(sheet).toBeVisible();
    await sheet.getByTestId("sidebar-search-trigger").click();

    await expect(searchDialog(page)).toBeVisible();
    await expect(sheet).toBeHidden();
    await expect(page.getByRole("dialog")).toHaveCount(1);
  });

  /**
   * The overlay's scale only exists as rendered geometry: half of it is reached
   * through slot selectors on classes the vendored `Command` writes itself, so a
   * source assertion for the right utility can pass against the wrong pixels —
   * and every figure below is the ramp resolved at the 80% density root, which is
   * exactly what a class name cannot tell you.
   */
  test("renders the overlay at the reading scale, with the filter trigger on its own floor", async ({
    page,
  }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await openSearch(page);

    const fontSize = async (locator: Locator): Promise<number> =>
      await locator.evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize));

    // `text-base` and `text-sm` at the 80% root. The rows and the query read at
    // the same step, a step above the tertiary project name beside them.
    expect(await fontSize(searchInput(page))).toBeCloseTo(11.2, 1);
    expect(await fontSize(searchResult(page, WORKSPACES.root.title))).toBeCloseTo(11.2, 1);
    expect(await fontSize(searchDialog(page).locator("[cmdk-group-heading]").first())).toBeCloseTo(
      10.4,
      1,
    );

    const heightOf = async (locator: Locator): Promise<number> =>
      (await locator.boundingBox())?.height ?? 0;

    // The overlay itself: `sm:max-w-2xl`, and a workspace title has to have room
    // to be read rather than truncated beside its project.
    const overlay = await page.locator('[data-slot="dialog-content"]').boundingBox();
    expect(overlay?.width).toBeGreaterThan(500);

    // The input band and the rows are geometry rather than type, and Tailwind
    // derives spacing from rem too — so `h-12` is 38.4px here, not 48. That is
    // the point: the band rides the same density lever its text does.
    expect(
      await heightOf(searchDialog(page).locator('[data-slot="command-input-wrapper"]')),
    ).toBeCloseTo(38.4, 0);
    expect(await heightOf(searchResult(page, WORKSPACES.root.title))).toBeGreaterThanOrEqual(30);

    // A pointer does not shrink when type gets denser, and it does not grow when
    // type gets larger either — the trigger stays on its own 24px floor.
    const trigger = searchDialog(page).getByTestId("fleet-filter-trigger");
    const triggerBox = await trigger.boundingBox();
    expect(triggerBox?.height).toBeGreaterThanOrEqual(24);
    expect(triggerBox?.height).toBeLessThan(32);
  });
});
