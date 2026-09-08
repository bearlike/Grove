import { expect, test, type Locator, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";
import type { DashboardSnapshotView, PhaseView } from "@/lib/grove/api";

/**
 * The project context deliberately has groups that share one repository root.
 * That is the case the old root-only rail could not express: `apps/console` and
 * `apps/docs` are distinct configured projects, even though both belong to
 * Widget. The docs project is empty on purpose; a configured project must not
 * disappear merely because no workspace happens to be running in it.
 */
const WIDGET_ROOT = "/work/widget";
const BASE_WORKSPACE_ID = FIXTURE_ACTIVITY.projects[0]!.workspaces[0]!.state.id;

const WORKSPACES = {
  root: {
    // The workspace route reads the same id from the activity snapshot to find
    // the associated agent session; use the real fixture id instead of making
    // a second, disconnected root row.
    id: BASE_WORKSPACE_ID,
    title: "Root workspace without a report",
    branch: "main",
    needsAttention: false,
    phase: null,
  },
  consoleDone: {
    id: "sidebar-console-done",
    title: "Console release complete",
    branch: "release/console",
    needsAttention: false,
    phase: "done" as const,
  },
  consoleBlocked: {
    id: "sidebar-console-blocked",
    title: "Console migration awaiting approval",
    // `main` proves the status words follow a content-sized branch rather than
    // a permanently growing metadata slot.
    branch: "main",
    needsAttention: true,
    phase: "implementing" as const,
  },
  api: {
    id: "sidebar-api-attention-only",
    title: "API contract waiting for a decision",
    branch: "fix/api-contract",
    needsAttention: true,
    phase: null,
  },
} as const;

type WorkspaceSpec = (typeof WORKSPACES)[keyof typeof WORKSPACES];

function phaseFor(spec: WorkspaceSpec): PhaseView | null {
  if (spec.phase === null) return null;
  return {
    phase: spec.phase,
    note: spec.id === WORKSPACES.consoleBlocked.id ? "Awaiting approval" : null,
    updated_at: new Date().toISOString(),
    index: spec.phase === "done" ? 5 : spec.phase === "implementing" ? 2 : 3,
    total: 6,
    blocked: spec.id === WORKSPACES.consoleBlocked.id,
    tickets: [],
  };
}

function workspaceFor(
  spec: WorkspaceSpec,
  repoRoot: string,
  index: number,
): DashboardSnapshotView["projects"][number]["workspaces"][number] {
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
      created_at: new Date(Date.now() - index * 60_000).toISOString(),
      updated_at: new Date(Date.now() - index * 30_000).toISOString(),
    },
    sessions: [
      {
        ...structuredClone(template.sessions[0]),
        activity: {
          ...structuredClone(template.sessions[0]!.activity),
          state: spec.needsAttention ? "waiting" : "working",
          needs_attention: spec.needsAttention,
        },
      },
    ],
    needs_attention: spec.needsAttention,
    phase: phaseFor(spec),
    dirty_files: spec.id === WORKSPACES.consoleBlocked.id ? 123456 : 0,
    diff_added: spec.id === WORKSPACES.consoleBlocked.id ? 987654 : 0,
    diff_removed: spec.id === WORKSPACES.consoleBlocked.id ? 654321 : 0,
  };
}

function snapshot(): DashboardSnapshotView {
  const groups = [
    {
      // The group CWD is the persisted project-subpath identity on the dashboard
      // wire. Workspaces preserve that group as they are selected in the rail.
      repo_root: WIDGET_ROOT,
      repo_name: "Widget",
      cwd: WIDGET_ROOT,
      workspaces: [workspaceFor(WORKSPACES.root, WIDGET_ROOT, 0)],
      error: null,
    },
    {
      repo_root: WIDGET_ROOT,
      repo_name: "Widget",
      cwd: `${WIDGET_ROOT}/apps/console`,
      workspaces: [
        workspaceFor(WORKSPACES.consoleDone, WIDGET_ROOT, 1),
        workspaceFor(WORKSPACES.consoleBlocked, WIDGET_ROOT, 2),
      ],
      error: null,
    },
    {
      repo_root: WIDGET_ROOT,
      repo_name: "Widget",
      cwd: `${WIDGET_ROOT}/apps/docs`,
      workspaces: [],
      error: null,
    },
    {
      repo_root: "/work/api",
      repo_name: "API",
      cwd: "/work/api",
      workspaces: [workspaceFor(WORKSPACES.api, "/work/api", 3)],
      error: null,
    },
  ];
  return {
    ...structuredClone(FIXTURE_ACTIVITY),
    projects: groups,
    total_workspaces: 4,
    needs_attention: 2,
  };
}

/** A snapshot route alone is racy: the fake event source sends its own snapshot. */
async function useSnapshot(page: Page): Promise<void> {
  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshot() }));
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
}

function railRows(page: Page): Locator {
  return page.locator("aside [data-testid='fleet-row']");
}

function railRow(page: Page, id: string): Locator {
  return page.locator(`aside [data-workspace-id="${id}"]`);
}

function sidebar(page: Page): Locator {
  return page.locator("aside");
}

function projectPicker(page: Page): Locator {
  return sidebar(page).getByRole("combobox", { name: "Project context", exact: true });
}

async function selectProject(page: Page, name: string): Promise<void> {
  await projectPicker(page).click();
  // The dialog is portalled, unlike its rail trigger.
  const search = page.getByRole("combobox", { name: "Search projects", exact: true });
  await expect(search).toBeVisible();
  await search.fill(name);
  await page.getByRole("option", { name, exact: true }).click();
}

async function tabTo(page: Page, target: Locator): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => document.activeElement === element)) return;
  }
  throw new Error("Could not reach sidebar row with the keyboard");
}

test.describe("native sidebar project context", () => {
  test("keeps tooltip-wrapped outlined controls quiet at rest and clear on keyboard focus", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);
    for (const theme of ["light", "dark"]) {
      await page.evaluate(value => {
        document.documentElement.classList.remove("light", "dark");
        document.documentElement.classList.add(value);
      }, theme);
      const search = page.getByTestId("sidebar-search-trigger");
      await page.getByTestId("shell-header").click({ position: { x: 250, y: 15 } });
      await page.mouse.move(900, 500);
      for (const id of ["sidebar-search-trigger", "fleet-create-rail", "fleet-filter-trigger", "marquee-pause", "shell-sidebar-toggle"]) {
        const control = page.getByTestId(id);
        await expect(control).toHaveCSS("box-shadow", "none");
        const quietEdge = await control.evaluate(el => {
          const probe = document.createElement("span");
          probe.style.borderColor = "var(--edge-control)";
          el.append(probe);
          const color = getComputedStyle(probe).borderTopColor;
          probe.remove();
          return color;
        });
        await expect(control).toHaveCSS("border-top-color", quietEdge);
      }
      await page.locator('aside').getByRole("link", { name: "Grove", exact: true }).focus();
      await page.keyboard.press("Tab");
      await expect(search).toBeFocused();
      await expect(search).toHaveCSS("box-shadow", "none");
      const focusEdge = await search.evaluate(el => {
        const probe = document.createElement("span");
        probe.style.borderColor = "var(--ring)";
        el.append(probe);
        const color = getComputedStyle(probe).borderTopColor;
        probe.remove();
        return color;
      });
      await expect(search).toHaveCSS("border-top-color", focusEdge);
    }
  });

  test("keeps active working pixels distinct against the dark card header", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);
    await page.evaluate(() => {
      document.documentElement.classList.remove("light");
      document.documentElement.classList.add("dark");
    });
    const mark = page.locator('aside [data-testid="working-mark"]').first();
    await expect(mark).toBeVisible();
    const ratios = await mark.evaluate((loader) => {
      const header = loader.closest("header")!;
      const css = getComputedStyle(header);
      const pixel = loader.querySelector(".opacity-90")!;
      const canvas = document.createElement("canvas").getContext("2d")!;
      const paint = (background: string, foreground?: string) => {
        canvas.globalAlpha = 1;
        canvas.fillStyle = background;
        canvas.fillRect(0, 0, 1, 1);
        if (foreground) {
          canvas.globalAlpha = 0.9;
          canvas.fillStyle = foreground;
          canvas.fillRect(0, 0, 1, 1);
        }
        const rgb = [...canvas.getImageData(0, 0, 1, 1).data].slice(0, 3);
        return rgb.map(value => {
          const v = value / 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
      };
      return ["--surface-header-start", "--surface-header-end"].map(token => {
        const background = css.getPropertyValue(token);
        const ground = paint(background);
        const signal = paint(background, getComputedStyle(pixel).backgroundColor);
        return (Math.max(signal, ground) + 0.05) / (Math.min(signal, ground) + 0.05);
      });
    });
    for (const ratio of ratios) expect(ratio).toBeGreaterThanOrEqual(3);
  });

  test("matches the header collapse control to the neighboring search button", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    const search = page.getByTestId("sidebar-search-trigger");
    const collapse = page.getByTestId("shell-sidebar-toggle");
    await expect(search).toBeVisible();
    await expect(collapse).toBeVisible();
    const appearance = (element: HTMLElement) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const icon = element.querySelector("svg")!.getBoundingClientRect();
      return { width: box.width, height: box.height, icon: icon.width,
        border: style.borderTopWidth, radius: style.borderTopLeftRadius,
        fill: style.backgroundColor, color: style.color };
    };
    expect(await collapse.evaluate(appearance)).toEqual(await search.evaluate(appearance));
    expect((await collapse.boundingBox())!.width).toBe(24);
    await collapse.click();
    await expect(page.getByTestId("app-sidebar")).toHaveAttribute("data-collapsed", "true");
    const create = page.getByTestId("fleet-create-rail");
    await expect(create).toHaveCSS("width", "24px");
    await expect(create).toHaveCSS("height", "24px");
    await expect(search).toHaveCSS("width", "24px");
    await expect(search).toHaveCSS("height", "24px");
    await collapse.click();
    await expect(page.getByTestId("app-sidebar")).toHaveAttribute("data-collapsed", "false");
  });

  /**
   * An equality, not four literals: the defect is the relationship, and a
   * fine-only assertion passes just as happily when the touch branch is dropped.
   */
  test("gives the project selector the same height as its action row", async ({ browser, baseURL }) => {
    const ids = ["rail-project-context", "fleet-create-rail", "fleet-filter-trigger", "marquee-pause"];
    for (const pointer of ["fine", "coarse"] as const) {
      const context = await browser.newContext({
        viewport: { width: 1280, height: 900 },
        hasTouch: pointer === "coarse",
        isMobile: false,
        storageState: "tests/e2e/.auth/storage-state.json",
      });
      const page = await context.newPage();
      await useSnapshot(page);
      await page.goto(`${baseURL}/`);
      await expect(railRows(page)).toHaveCount(4);
      const heights = await page.locator('aside [data-testid="fleet-tree"]').evaluate(
        (list, testids) => testids.map(
          (id) => list.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect().height,
        ),
        ids,
      );
      const floor = pointer === "coarse" ? 44 : 24;
      for (const height of heights) {
        expect(height).toBeCloseTo(heights[0]!, 1);
        expect(height).toBeGreaterThanOrEqual(floor);
      }
      await context.close();
    }
  });

  test("uses matching border-only sidebar actions and collapse control", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => {
        document.documentElement.classList.remove("light", "dark");
        document.documentElement.classList.add(value);
      }, theme);
      for (const id of ["fleet-create-rail", "sidebar-search-trigger", "fleet-filter-trigger", "marquee-pause", "shell-sidebar-toggle"]) {
        const control = page.getByTestId(id);
        await expect(control).toHaveAttribute("data-variant", "outline");
        await expect(control).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
        await expect(control).toHaveCSS("border-top-width", "1px");
      }
    }
  });

  test("matches vertical control gaps to card gaps without widening the action row", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);
    const spacing = await page.locator('aside [data-testid="fleet-tree"]').evaluate((list) => {
      const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
      const selector = list.querySelector('[data-testid="rail-project-context"]')!.getBoundingClientRect();
      const action = list.querySelector('[data-testid="fleet-create-rail"]')!;
      const row = action.parentElement!;
      const groups = [...list.querySelectorAll('[data-testid="fleet-rail-group"]')];
      return {
        unit: root / 16,
        selectorToAction: action.getBoundingClientRect().top - selector.bottom,
        listGap: Number.parseFloat(getComputedStyle(list).rowGap),
        groupGap: Number.parseFloat(getComputedStyle(groups[0].parentElement!).rowGap),
        cardGaps: groups.map(group => Number.parseFloat(getComputedStyle(group).rowGap)),
        actionGap: Number.parseFloat(getComputedStyle(row).columnGap),
      };
    });
    expect(spacing.selectorToAction).toBeCloseTo(12 * spacing.unit, 1);
    expect(spacing.listGap).toBeCloseTo(12 * spacing.unit, 1);
    expect(spacing.groupGap).toBeCloseTo(12 * spacing.unit, 1);
    for (const gap of spacing.cardGaps) expect(gap).toBeCloseTo(12 * spacing.unit, 1);
    expect(spacing.actionGap).toBeCloseTo(6 * spacing.unit, 1);
  });

  test("gives every session the highlighted card's expanded body padding", async ({ page }) => {
    await useSnapshot(page);
    await page.goto(`/w/${BASE_WORKSPACE_ID}`);
    await expect(railRows(page)).toHaveCount(4);
    const cards = await railRows(page).evaluateAll((rows) => rows.map((row) => {
      const body = row.querySelector('[data-testid="rail-metadata"]')!.parentElement!;
      const style = getComputedStyle(body);
      return {
        selected: row.getAttribute("data-selected") === "true",
        top: Number.parseFloat(style.paddingTop),
        bottom: Number.parseFloat(style.paddingBottom),
        expected: Number.parseFloat(getComputedStyle(document.documentElement).fontSize) / 2,
      };
    }));
    expect(cards.some((card) => card.selected)).toBe(true);
    expect(cards.some((card) => !card.selected)).toBe(true);
    for (const card of cards) {
      expect(card.top).toBeCloseTo(card.expected, 1);
      expect(card.bottom).toBeCloseTo(card.expected, 1);
    }
  });

  test("aligns the smaller brand band with the workspace header", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    const brand = await page.getByTestId("sidebar-brand-header").boundingBox();
    const header = await page.getByTestId("shell-header").boundingBox();
    expect(brand!.y).toBeCloseTo(header!.y, 1);
    expect(brand!.height).toBe(32);
    expect(brand!.y + brand!.height).toBeCloseTo(header!.y + header!.height, 1);
    expect((await sidebar(page).getByTestId("brand-mark").boundingBox())!.width).toBeLessThan(24);
  });

  test("shares the project selection when moving from the mobile sheet to desktop", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 420, height: 900 });
    await page.goto("/");
    await page.getByTestId("shell-sidebar-sheet").click();
    const drawer = page.getByRole("dialog", { name: "Grove workspaces" });
    await drawer.getByRole("combobox", { name: "Project context", exact: true }).click();
    await page.getByRole("option", { name: "Widget — console", exact: true }).click();
    await expect(drawer.getByTestId("fleet-row")).toHaveCount(2);
    await drawer.getByRole("button", { name: "Close", exact: true }).click();
    await expect(drawer).toBeHidden();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(projectPicker(page)).toContainText("Widget — console");
    await expect(railRows(page)).toHaveCount(2);
  });

  test("keeps enlarged figures inside the sidebar without shrinking their text", async ({ page }) => {
    await useSnapshot(page);
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);
    await page.addStyleTag({ content: "aside { width: 320px !important; } aside [data-testid='rail-metadata'] { font-size: 32px !important; line-height: 1.4 !important; }" });
    const row = railRow(page, WORKSPACES.consoleBlocked.id);
    const amounts = ["rail-dirty", "rail-added", "rail-removed", "rail-created"];
    for (const id of amounts) {
      const cell = row.getByTestId(id);
      const bounds = await cell.evaluate((el) => {
        const r = el.closest('[data-testid="fleet-row"]')!.getBoundingClientRect();
        const c = el.getBoundingClientRect();
        return { right: c.right, edge: r.right, clipped: el.scrollWidth > el.clientWidth + 1 };
      });
      expect(bounds.clipped, id).toBe(false);
      expect(bounds.right, id).toBeLessThanOrEqual(bounds.edge);
    }
  });

  test("defaults to all configured projects and can clear an empty project context", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);

    await expect(projectPicker(page)).toBeVisible({ timeout: 5_000 });
    await projectPicker(page).click();
    const options = ["All projects", "Widget — widget", "Widget — console", "Widget — docs"];
    for (const name of options) {
      const option = page.getByRole("option", { name, exact: true });
      await expect(option).toBeVisible();
      // The popup scales in. Measure after that animation settles rather than
      // accepting its transient sub-28px scaled frame as the hit target.
      await expect
        .poll(async () => (await option.boundingBox())?.height ?? 0)
        .toBeGreaterThanOrEqual(28);
    }
    await page.keyboard.press("Escape");

    // Selecting an empty configured project is a real state, with the context
    // trigger as its clear path rather than an erased option.
    await selectProject(page, "Widget — docs");
    await expect(projectPicker(page)).toContainText("Widget — docs");
    await expect(railRows(page)).toHaveCount(0);
    await expect(page.getByTestId("fleet-empty-rail")).toBeVisible();

    await selectProject(page, "All projects");
    await expect(railRows(page)).toHaveCount(4);

    // Context narrows navigation only. The fleet dashboard stays all-projects,
    // rather than acquiring a second, surprising project filter.
    await page.goto("/fleet");
    await expect(page.getByTestId("workspace-card")).toHaveCount(4);
  });

  test("selecting a nested project isolates its group, intersects search, and keeps the current draft", async ({
    page,
  }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/w/${BASE_WORKSPACE_ID}`);
    await page.getByTestId("pane-split").click();
    const draft = page.getByRole("textbox", { name: "Message input", exact: true });
    await expect(draft).toBeVisible();
    await draft.fill("A sidebar context change must not discard this draft");

    await selectProject(page, "Widget — console");
    await expect(railRows(page)).toHaveCount(2);
    await expect(railRow(page, WORKSPACES.consoleDone.id)).toBeVisible();
    await expect(railRow(page, WORKSPACES.consoleBlocked.id)).toBeVisible();
    await expect(railRow(page, WORKSPACES.root.id)).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`/w/${BASE_WORKSPACE_ID}$`));
    await expect(draft).toHaveValue("A sidebar context change must not discard this draft");

    await sidebar(page).getByTestId("sidebar-search-trigger").click();
    await page.getByTestId("workspace-search-dialog").getByRole("combobox", { name: "Search workspaces", exact: true }).fill("awaiting approval");
    await page.keyboard.press("Escape");
    await expect(railRows(page)).toHaveCount(1);
    await expect(railRow(page, WORKSPACES.consoleBlocked.id)).toBeVisible();
  });

  test("keeps project choice out of the sidebar filter menu", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    await sidebar(page).getByTestId("fleet-filter-trigger").click();
    // Radix portals the menu outside the rail; only its duplicate trigger needs
    // the sidebar scope.
    const menu = page.getByTestId("fleet-filter-menu");
    await expect(menu).toBeVisible();
    // The picker above owns project scope. Repeating it as hide-checkboxes in
    // this menu made two controls issue contradictory instructions.
    await expect(menu.getByText("Project", { exact: true })).toHaveCount(0);
    await expect(menu.getByRole("menuitemcheckbox", { name: /Widget/ })).toHaveCount(0);
  });

  test("keeps compact rows content-sized, legible, and complete", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(railRows(page)).toHaveCount(4);

    const geometry = await page.evaluate(
      ({ rootId, blockedId, doneId }) => {
        const row = (id: string) => document.querySelector<HTMLElement>(`[data-workspace-id="${id}"]`)!;
        const rect = (element: Element | null) => {
          if (!element) return null;
          const box = element.getBoundingClientRect();
          return { x: box.x, y: box.y, right: box.right, height: box.height };
        };
        const blocked = row(blockedId);
        const root = row(rootId);
        const blockedLink = blocked.querySelector<HTMLElement>("a")!;
        // Intentional name truncation is allowed. These cells alone are values
        // whose clipped glyphs would become different values, so only they are
        // checked for overflow.
        const quantities = ["rail-dirty", "rail-added", "rail-removed", "rail-created"].flatMap(
          (testId) => {
            const element = blocked.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
            return element ? [{ testId, clipped: element.scrollWidth > element.clientWidth + 1 }] : [];
          },
        );
        const title = blocked.querySelector<HTMLElement>("[data-testid='looping-text']")!;
        const metadata = blocked.querySelector<HTMLElement>("[data-testid='rail-metadata']")!;
        const semanticOrder = (container: HTMLElement): string[] => {
          const semanticIds = new Set([
            "rail-branch",
            "fleet-row-attention-mark",
            "fleet-row-phase-mark",
          ]);
          return [...container.querySelectorAll<HTMLElement>("[data-testid]")]
            .map((element) => element.dataset.testid)
            .filter((testId): testId is string => testId !== undefined && semanticIds.has(testId));
        };
        const context = blocked.querySelector<HTMLElement>("[data-testid='rail-context']")!;
        return {
          blocked: {
            box: rect(blockedLink),
            attention: rect(blocked.querySelector("[data-testid='fleet-row-attention-mark']")),
            phase: rect(blocked.querySelector("[data-testid='fleet-row-phase-mark']")),
            context: rect(context),
            contextOrder: semanticOrder(context),
            title: rect(title),
            branch: rect(blocked.querySelector("[data-testid='rail-branch']")),
            titleFont: Number.parseFloat(getComputedStyle(title).fontSize),
            metadataFont: Number.parseFloat(getComputedStyle(metadata).fontSize),
            clipped: blockedLink.scrollHeight > blockedLink.clientHeight + 1,
          },
          root: {
            contextOrder: semanticOrder(root.querySelector<HTMLElement>("[data-testid='rail-context']")!),
          },
          attentionOnly: {
            contextOrder: semanticOrder(row("sidebar-api-attention-only").querySelector<HTMLElement>("[data-testid='rail-context']")!),
          },
          donePhaseText: row(doneId)
            .querySelector<HTMLElement>("[data-testid='fleet-row-phase-mark']")
            ?.textContent?.trim(),
          blockedPhaseText: blocked
            .querySelector<HTMLElement>("[data-testid='fleet-row-phase-mark']")
            ?.textContent?.trim(),
          shortBranchGaps: {
            branchToAttention:
              rect(blocked.querySelector("[data-testid='fleet-row-attention-mark']"))!.x -
              rect(blocked.querySelector("[data-testid='rail-branch']"))!.right,
            attentionToPhase:
              rect(blocked.querySelector("[data-testid='fleet-row-phase-mark']"))!.x -
              rect(blocked.querySelector("[data-testid='fleet-row-attention-mark']"))!.right,
            branchWidth: rect(blocked.querySelector("[data-testid='rail-branch']"))!.right -
              rect(blocked.querySelector("[data-testid='rail-branch']"))!.x,
          },
          quantities,
        };
      },
      {
        rootId: WORKSPACES.root.id,
        blockedId: WORKSPACES.consoleBlocked.id,
        doneId: WORKSPACES.consoleDone.id,
      },
    );

    expect(geometry.blocked.box!.height).toBeLessThanOrEqual(78);
    expect(geometry.blocked.clipped).toBe(false);
    expect(geometry.blocked.titleFont).toBeCloseTo(11.2, 1);
    expect(geometry.blocked.metadataFont).toBeCloseTo(10.4, 1);
    expect(geometry.blocked.branch!.y).toBeGreaterThan(geometry.blocked.title!.y);
    expect(geometry.blocked.context!.height).toBeGreaterThan(0);
    // Status is readable content on the body line, not an icon reservation in
    // the title. The same body order applies when one or both reports are gone.
    expect(geometry.blocked.contextOrder).toEqual([
      "rail-branch",
      "fleet-row-attention-mark",
      "fleet-row-phase-mark",
    ]);
    expect(geometry.root.contextOrder).toEqual(["rail-branch"]);
    expect(geometry.attentionOnly.contextOrder).toEqual([
      "rail-branch",
      "fleet-row-attention-mark",
    ]);
    // `main` cannot be a wide empty slot just because the row also has two
    // status words. Both gaps are the compact rhythm, not flex remainder.
    expect(geometry.shortBranchGaps.branchToAttention).toBeGreaterThanOrEqual(4);
    expect(geometry.shortBranchGaps.branchToAttention).toBeLessThanOrEqual(10);
    expect(geometry.shortBranchGaps.attentionToPhase).toBeGreaterThanOrEqual(4);
    expect(geometry.shortBranchGaps.attentionToPhase).toBeLessThanOrEqual(10);
    expect(geometry.shortBranchGaps.branchWidth).toBeLessThan(48);
    expect(geometry.donePhaseText).toMatch(/done/i);
    expect(geometry.blockedPhaseText).toMatch(/implementing/i);
    for (const quantity of geometry.quantities) expect(quantity.clipped, quantity.testId).toBe(false);
  });

  test("does not move a row on hover or keyboard focus, including forced colours", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/w/${BASE_WORKSPACE_ID}`);
    const row = railRow(page, WORKSPACES.consoleBlocked.id);
    const link = row.locator("a");
    await expect(link).toBeVisible();

    const resting = (await link.boundingBox())!;
    await row.hover();
    const hovered = (await link.boundingBox())!;
    expect(hovered).toEqual(resting);

    const options = row.getByRole("button", { name: "Workspace options", exact: true });
    await expect(options).toBeVisible();
    const optionBox = (await options.boundingBox())!;
    expect(optionBox.width).toBeGreaterThanOrEqual(28);
    expect(optionBox.height).toBeGreaterThanOrEqual(28);

    await sidebar(page).getByRole("link", { name: "Grove", exact: true }).focus();
    await tabTo(page, link);
    const focused = (await link.boundingBox())!;
    expect(focused).toEqual(resting);
    await page.emulateMedia({ forcedColors: "active" });
    await expect(link).toBeFocused();
    expect(await link.evaluate((element) => getComputedStyle(element).outlineWidth)).not.toBe("0px");
    await page.emulateMedia({ forcedColors: "none" });
  });

  test("offers a native tooltip in both themes and pauses scrolling text", async ({ page }) => {
    await useSnapshot(page);
    await page.setViewportSize({ width: 1440, height: 900 });

    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.goto("/");
      // Reloading underneath an already-hovering pointer does not dispatch a new
      // enter edge, so move it out before asking the new rail for its tooltip.
      await expect(railRows(page)).toHaveCount(4);
      await page.evaluate(async () => { await document.fonts.ready; });
      await page.mouse.move(800, 700);
      const pause = page.getByTestId("marquee-pause");
      await pause.hover();
      await expect(page.getByRole("tooltip", { name: "Pause scrolling text", exact: true })).toBeVisible();
      await expect(pause).toHaveAttribute("aria-pressed", "false");
      await pause.click();
      await expect(pause).toHaveAttribute("aria-pressed", "true");
    }
    await page.emulateMedia({ colorScheme: null });
  });
});
