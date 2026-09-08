import { expect, test, type Locator, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";
import { dp } from "./density";
import type { DashboardSnapshotView } from "@/lib/grove/api";

/**
 * The session row is measured in a browser because its two essential promises
 * are geometric: the native card anatomy remains scannable, and overflowing
 * labels complete a real marquee cycle. Static markup cannot establish either.
 *
 * The fleet payload stays local to this spec. Its deliberately extreme title,
 * branch and figures would distort every unrelated rail fixture if shared.
 */

interface RowSpec {
  readonly id: string;
  readonly title: string;
  readonly branch: string;
  readonly dirty: number;
  readonly added: number;
  readonly removed: number;
  readonly needsAttention?: boolean;
}

interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

interface RowGeometry {
  readonly row: Rect;
  readonly header: Rect | null;
  readonly agent: Rect | null;
  readonly title: Rect | null;
  readonly metadata: Rect | null;
  readonly context: Rect | null;
  readonly branch: Rect | null;
  readonly attention: Rect | null;
  readonly phase: Rect | null;
  readonly ledger: Rect | null;
  readonly ledgerLine: Rect | null;
  readonly dirty: Rect | null;
  readonly added: Rect | null;
  readonly removed: Rect | null;
  readonly created: Rect | null;
  readonly options: Rect | null;
  readonly contextIcons: readonly number[];
}

interface MetricMeasurement {
  readonly id: string;
  readonly text: string;
  readonly clipped: boolean;
  readonly loops: boolean;
}

const NO_PHASE_ROWS: readonly RowSpec[] = [
  {
    id: "ddddddddddddddddddddddddddddddd4",
    title: "No phase mark leaves no title hole",
    branch: "fix/no-phase-gap",
    dirty: 0,
    added: 0,
    removed: 0,
  },
];

const ROWS: readonly RowSpec[] = [
  {
    id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
    title: "Stabilize long transcript loading and navigation across every pane",
    branch: "feat/transcript-loading-and-navigation-across-panes",
    dirty: 7,
    added: 124,
    removed: 18,
    needsAttention: true,
  },
  {
    id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2",
    title: "Short one",
    branch: "fix/tabs",
    dirty: 0,
    added: 0,
    removed: 0,
  },
  {
    id: "ccccccccccccccccccccccccccccccc3",
    title: "Six figure counters",
    branch: "main",
    dirty: 123456,
    added: 987654,
    removed: 654321,
  },
];

function snapshotFor(
  rows: readonly RowSpec[],
  phase: DashboardSnapshotView["projects"][number]["workspaces"][number]["phase"] =
    FIXTURE_ACTIVITY.projects[0]!.workspaces[0]!.phase,
): DashboardSnapshotView {
  const template = FIXTURE_ACTIVITY.projects[0].workspaces[0];
  const workspaces = rows.map((row) => ({
    ...structuredClone(template),
    state: {
      ...structuredClone(template.state),
      id: row.id,
      title: row.title,
      branch: row.branch,
      // Relative to the run: a frozen timestamp eventually grows from "2h" to
      // a long absolute date and would turn a layout test into a calendar test.
      created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      updated_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    },
    phase,
    needs_attention: row.needsAttention ?? false,
    dirty_files: row.dirty,
    diff_added: row.added,
    diff_removed: row.removed,
  }));
  return {
    ...structuredClone(FIXTURE_ACTIVITY),
    projects: [
      {
        ...structuredClone(FIXTURE_ACTIVITY.projects[0]),
        workspaces,
      },
    ],
    total_workspaces: workspaces.length,
  };
}

/**
 * Serve the synthetic fleet and silence the event stream.
 *
 * The fake daemon's stream sends its own snapshot when connected; intercepting
 * only the activity request would therefore overwrite this fixture after the
 * page initially settled.
 */
async function useFleet(
  page: Page,
  rows: readonly RowSpec[],
  phase?: DashboardSnapshotView["projects"][number]["workspaces"][number]["phase"],
): Promise<void> {
  await page.route("**/api/grove/activity", (route) =>
    route.fulfill({ json: snapshotFor(rows, phase) }),
  );
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
}

/** The row's visual landmarks, all relative to its CardShell wrapper. */
async function geometry(page: Page, id: string): Promise<RowGeometry> {
  return page.evaluate((workspaceId) => {
    const row = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`);
    if (!row) throw new Error(`no row for ${workspaceId}`);

    const rowBox = row.getBoundingClientRect();
    const at = (element: Element | null): Rect | null => {
      if (!element) return null;
      const box = element.getBoundingClientRect();
      return { x: box.x - rowBox.x, y: box.y - rowBox.y, width: box.width, height: box.height };
    };
    const header = row.querySelector<HTMLElement>("header");
    const metadata = row.querySelector<HTMLElement>('[data-testid="rail-metadata"]');
    const context = row.querySelector<HTMLElement>('[data-testid="rail-context"]');
    const created = row.querySelector<HTMLElement>('[data-testid="rail-created"]');

    return {
      row: { x: 0, y: 0, width: rowBox.width, height: rowBox.height },
      header: at(header),
      agent: at(row.querySelector('[data-testid="agent-mark"]')),
      title: at(header?.querySelector('[data-testid="looping-text"]') ?? null),
      metadata: at(metadata),
      context: at(context),
      branch: at(row.querySelector('[data-testid="rail-branch"]')),
      attention: at(row.querySelector('[data-testid="fleet-row-attention-mark"]')),
      phase: at(row.querySelector('[data-testid="fleet-row-phase-mark"]')),
      ledger: at(row.querySelector('[data-testid="rail-ledger"]')),
      ledgerLine: at(created?.parentElement ?? null),
      dirty: at(row.querySelector('[data-testid="rail-dirty"]')),
      added: at(row.querySelector('[data-testid="rail-added"]')),
      removed: at(row.querySelector('[data-testid="rail-removed"]')),
      created: at(created),
      options: at(row.querySelector('button[aria-label="Workspace options"]')),
      contextIcons: [
        ...row.querySelectorAll<SVGSVGElement>(
          '[data-testid="rail-branch"] svg, [data-testid="fleet-row-attention-mark"] > svg, [data-testid="fleet-row-phase-mark"] > span > svg:first-child',
        ),
      ].map((icon) => icon.getBoundingClientRect().width),
    };
  }, id);
}

/**
 * Figures must be checked on their cells, not their text children. A value span
 * is naturally as wide as its full value; the cell is where a cap would clip it.
 */
async function measurements(page: Page): Promise<readonly MetricMeasurement[]> {
  return page.evaluate(() => {
    const ids = ["rail-dirty", "rail-added", "rail-removed", "rail-created"];
    return [...document.querySelectorAll('[data-testid="fleet-row"]')].flatMap((row) => {
      const created = row.querySelector<HTMLElement>('[data-testid="rail-created"]');
      const ledgerLine = created?.parentElement?.getBoundingClientRect();
      if (!ledgerLine) throw new Error("no ledger line");
      return ids.flatMap((id) => {
        const cell = row.querySelector<HTMLElement>(`[data-testid="${id}"]`);
        if (!cell) return [];
        const box = cell.getBoundingClientRect();
        return [{
          id,
          text: (cell.textContent ?? "").replace(/\s+/g, " ").trim(),
          clipped:
            cell.scrollWidth - cell.clientWidth > 1 || box.right - ledgerLine.right > 1,
          loops: cell.querySelector('[data-looping="true"]') !== null,
        }];
      });
    });
  });
}

function expectSameGeometry(after: RowGeometry, before: RowGeometry): void {
  expect(after.row).toEqual(before.row);
  expect(after.header).toEqual(before.header);
  expect(after.agent).toEqual(before.agent);
  expect(after.title).toEqual(before.title);
  expect(after.metadata).toEqual(before.metadata);
  expect(after.context).toEqual(before.context);
  expect(after.branch).toEqual(before.branch);
  expect(after.ledger).toEqual(before.ledger);
  expect(after.ledgerLine).toEqual(before.ledgerLine);
  expect(after.created).toEqual(before.created);
  expect(after.options).toEqual(before.options);
}

/** Uses a real Tab sequence so the focus-visible state is the browser's state. */
async function focusWithKeyboard(page: Page, target: Locator): Promise<void> {
  await page.locator('aside').getByRole('link', { name: 'Grove', exact: true }).focus();
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => document.activeElement === element)) return;
  }
  throw new Error("target was not reached by keyboard navigation");
}

test.describe("the native sidebar row keeps identity, context and figures distinct", () => {
  test("keeps the header, context and ledger in their native relationship", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();

    const first = await geometry(page, ROWS[0].id);
    expect(first.header).not.toBeNull();
    expect(first.agent).not.toBeNull();
    expect(first.title).not.toBeNull();
    expect(first.metadata).not.toBeNull();
    expect(first.context).not.toBeNull();
    expect(first.branch).not.toBeNull();
    expect(first.attention).toBeNull();
    expect(first.phase).not.toBeNull();
    expect(first.ledger).not.toBeNull();
    expect(first.ledgerLine).not.toBeNull();
    expect(first.created).not.toBeNull();
    expect(first.options).not.toBeNull();

    // The row remains compact at normal desktop density without pretending that
    // text zoom and coarse pointers have the same physical budget.
    expect(first.row.height).toBeGreaterThan(50);
    expect(first.row.height).toBeLessThanOrEqual(80);

    // The agent and title share the compact header's center line. Context starts
    // below it; branch and phase/attention live there, not in a title-mark slot.
    expect(first.title!.x).toBeGreaterThanOrEqual(first.agent!.x + first.agent!.width);
    expect(
      Math.abs(
        first.title!.y + first.title!.height / 2 - (first.agent!.y + first.agent!.height / 2),
      ),
    ).toBeLessThanOrEqual(1);
    expect(first.metadata!.y).toBeGreaterThanOrEqual(first.header!.y + first.header!.height);
    expect(first.context!.y).toBeGreaterThan(first.title!.y);
    expect(first.branch!.x).toBeCloseTo(first.context!.x, 0);
    expect(first.ledger!.y).toBeGreaterThan(first.context!.y);

    // The ledger is a separate row: age owns its far edge while the figures
    // retain their intrinsic widths to its left.
    expect(first.created!.x + first.created!.width).toBeCloseTo(
      first.ledgerLine!.x + first.ledgerLine!.width,
      0,
    );
    expect(first.dirty).not.toBeNull();
    expect(first.added).not.toBeNull();
    expect(first.removed).not.toBeNull();

    // The header agent is `size-5`; context marks are the quieter `size-3`.
    expect(first.agent!.width).toBeCloseTo(dp(20), 0);
    expect(first.agent!.height).toBeCloseTo(dp(20), 0);
    expect(first.contextIcons).toHaveLength(2);
    for (const width of first.contextIcons) expect(width).toBeCloseTo(dp(12), 0);

    // A measured-zero workspace still has its created age but no fictional
    // change figures.
    const clean = await geometry(page, ROWS[1].id);
    expect(clean.dirty).toBeNull();
    expect(clean.added).toBeNull();
    expect(clean.removed).toBeNull();
    expect(clean.created).not.toBeNull();
  });

  test("keeps the title's left edge fixed when context marks are absent", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    const marked = await geometry(page, ROWS[0].id);

    await useFleet(page, NO_PHASE_ROWS, null);
    await page.goto("/");
    await expect(page.getByTestId("fleet-row")).toBeVisible();
    const unmarked = await geometry(page, NO_PHASE_ROWS[0].id);

    expect(marked.phase).not.toBeNull();
    expect(unmarked.phase).toBeNull();
    expect(unmarked.attention).toBeNull();
    // Marks now belong in the body context. Their presence cannot reserve or
    // reclaim an identity slot in the header.
    expect(unmarked.title!.x).toBeCloseTo(marked.title!.x, 0);
  });

  test("never clips or animates a figure, whatever the value", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const cells = await measurements(page);
    // Three changed rows per populated workspace plus each workspace's age:
    // assert the census so an empty selector cannot report a false clean pass.
    expect(cells).toHaveLength(9);
    for (const cell of cells) {
      expect(cell.clipped, `${cell.id} "${cell.text}" is clipped`).toBe(false);
      expect(cell.loops, `${cell.id} "${cell.text}" is scrolling`).toBe(false);
    }
    expect(cells.map((cell) => cell.text)).toContain("+987.7K");
    expect(cells.map((cell) => cell.text)).toContain("−654.3K");
  });

  test("makes the branch yield to a wide figure ledger and loop", async ({ page }) => {
    await useFleet(page, [
      {
        ...ROWS[0],
        branch: "feat/a-branch-name-nobody-should-have-typed-but-somebody-did",
        dirty: 123456,
        added: 987654,
        removed: 654321,
      },
    ]);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const squeezed = await geometry(page, ROWS[0].id);
    expect(squeezed.branch!.x).toBeCloseTo(squeezed.context!.x, 0);
    expect(squeezed.created!.x + squeezed.created!.width).toBeCloseTo(
      squeezed.ledgerLine!.x + squeezed.ledgerLine!.width,
      0,
    );
    expect(squeezed.branch!.width).toBeGreaterThan(dp(16));
    await expect(
      page.locator('[data-testid="rail-branch"] [data-looping="true"]'),
    ).toBeAttached();

    // This is deliberately the over-constrained row. Repeating it beside
    // `main` would not exercise flex's choice between the branch and figures.
    for (const cell of await measurements(page)) {
      expect(cell.clipped, `${cell.id} "${cell.text}" is clipped`).toBe(false);
      expect(cell.loops, `${cell.id} "${cell.text}" is scrolling`).toBe(false);
    }
  });

  test("carries no monospace anywhere in the rail", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();

    const mono = await page.evaluate(() => {
      const rail = document.querySelector("aside");
      if (!rail) throw new Error("no docked rail");
      return [...rail.querySelectorAll("*")]
        .map((element) => getComputedStyle(element).fontFamily)
        .filter((family) => /mono/i.test(family));
    });
    expect(mono).toEqual([]);
  });

  test("never overflows the page in either theme or at phone width", async ({ page }) => {
    await useFleet(page, ROWS);
    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      for (const width of [1440, 1024, 420]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/");
        await page.waitForTimeout(150);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${theme} at ${width}px`).toBeLessThanOrEqual(0);
      }
    }
    await page.emulateMedia({ colorScheme: null });
  });
});

/**
 * Reads one looping label's state straight off the Web Animations API.
 * `getAnimations()` reports the animation even if a compositor frame has not
 * painted, unlike a screenshot or a computed transform alone.
 */
async function marquee(page: Page, id: string) {
  return page.evaluate((workspaceId) => {
    const row = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`);
    const label = row?.querySelector<HTMLElement>('[data-looping="true"]');
    const track = label?.firstElementChild as HTMLElement | undefined;
    const animation = track?.getAnimations()[0];
    return {
      looping: label !== null && label !== undefined,
      playState: animation?.playState ?? null,
      duration:
        typeof animation?.effect?.getTiming().duration === "number"
          ? (animation.effect.getTiming().duration as number)
          : null,
      currentTime: typeof animation?.currentTime === "number" ? animation.currentTime : null,
      transform: track ? getComputedStyle(track).transform : null,
      copies: label ? label.textContent?.split("•").length ?? 0 : 0,
      hiddenClones: label ? label.querySelectorAll("[aria-hidden]").length : 0,
    };
  }, id);
}

test.describe("overflowing sidebar text loops, and a still frame cannot prove it", () => {
  test("completes a whole cycle and returns to its own start position", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const start = await marquee(page, ROWS[0].id);
    expect(start.looping, "a title far wider than the rail must loop").toBe(true);
    expect(start.playState).toBe("running");
    expect(start.duration).toBeGreaterThan(0);
    // Two copies and two separators make the wrapped frame visually identical
    // to the first frame rather than snapping back to the first glyph.
    expect(start.copies).toBe(3);
    expect(start.hiddenClones).toBeGreaterThanOrEqual(3);

    const period = start.duration!;
    await page.waitForTimeout(period / 4);
    const quarter = await marquee(page, ROWS[0].id);
    expect(quarter.transform).not.toBe(start.transform);
    expect(quarter.currentTime!).toBeGreaterThan(start.currentTime!);

    await page.waitForTimeout((period * 3) / 4 + 200);
    const wrapped = await marquee(page, ROWS[0].id);
    expect(wrapped.playState).toBe("running");
    expect(wrapped.currentTime!).toBeGreaterThan(period);

    const offsets = await page.evaluate(async (workspaceId) => {
      const row = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`)!;
      const track = row.querySelector<HTMLElement>('[data-looping="true"]')!
        .firstElementChild as HTMLElement;
      const animation = track.getAnimations()[0];
      const duration = animation.effect!.getTiming().duration as number;
      const read = (at: number): number => {
        animation.currentTime = at;
        const matrix = new DOMMatrixReadOnly(getComputedStyle(track).transform);
        return matrix.m41;
      };
      const samples = [read(0), read(duration * 0.5), read(duration - 1), read(duration)];
      animation.play();
      return { samples, width: track.getBoundingClientRect().width };
    }, ROWS[0].id);

    expect(offsets.samples[0]).toBeCloseTo(0, 0);
    expect(offsets.samples[1]).toBeLessThan(offsets.samples[0]);
    expect(offsets.samples[2]).toBeLessThan(offsets.samples[1]);
    // One tick before the period ends the track travelled one copy plus its
    // bullet; at the period boundary its duplicate occupies the start position.
    expect(Math.abs(offsets.samples[2])).toBeCloseTo(offsets.width / 2, 0);
    expect(offsets.samples[3]).toBeCloseTo(0, 0);
  });

  test("leaves the card, its context and its click target still while title text moves", async ({
    page,
  }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const before = await geometry(page, ROWS[0].id);
    await page.waitForTimeout(900);
    const after = await geometry(page, ROWS[0].id);

    expectSameGeometry(after, before);
  });

  test("text that fits stays still and grows no marquee machinery", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const short = await marquee(page, ROWS[1].id);
    expect(short.looping).toBe(false);
    // Scoped to labels: the options button may legitimately have a transition.
    const still = await page.evaluate((workspaceId) => {
      const row = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`)!;
      return [...row.querySelectorAll('[data-testid="looping-text"] > span')].flatMap((track) =>
        track.getAnimations(),
      ).length;
    }, ROWS[1].id);
    expect(still).toBe(0);
  });

  test("the shared control pauses every loop and resumes where it stopped", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const pause = page.getByTestId("marquee-pause");
    await expect(pause).toHaveAttribute("aria-pressed", "false");
    await pause.click();
    await expect(pause).toHaveAttribute("aria-pressed", "true");

    const held = await marquee(page, ROWS[0].id);
    expect(held.playState).toBe("paused");
    await page.waitForTimeout(400);
    const stillHeld = await marquee(page, ROWS[0].id);
    expect(stillHeld.currentTime).toBe(held.currentTime);

    await pause.click();
    await expect(pause).toHaveAttribute("aria-pressed", "false");
    const resumed = await marquee(page, ROWS[0].id);
    expect(resumed.playState).toBe("running");
    expect(resumed.currentTime!).toBeGreaterThanOrEqual(held.currentTime!);
  });

  test("a reader who asked for less motion gets the full text and no animation", async ({
    browser,
  }) => {
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const reduced = await marquee(page, ROWS[0].id);
    expect(reduced.looping).toBe(false);
    await expect(
      page.locator(`[data-workspace-id="${ROWS[0].id}"] [data-testid="looping-text"]`).first(),
    ).toHaveText(ROWS[0].title);
    await context.close();
  });

  test("announces the label once, however many copies are painted", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    const announced = await page.evaluate((workspaceId) => {
      const row = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`)!;
      const visible = (node: Element): boolean => !node.closest("[aria-hidden='true']");
      return [...row.querySelectorAll("[data-testid='looping-text'] > span > span")]
        .filter(visible)
        .map((node) => node.textContent);
    }, ROWS[0].id);

    expect(announced.filter((text) => text === ROWS[0].title)).toHaveLength(1);
    expect(announced).not.toContain(" • ");
  });

  test("suspends a row scrolled out of the rail", async ({ page }) => {
    // Twenty rows overflow the rail's own scroller, so the first rendered row
    // is genuinely outside the observer after the list reaches its tail.
    const many = Array.from({ length: 20 }, (_, index) => ({
      ...ROWS[0],
      id: `w${index}`.padStart(32, "d"),
    }));
    await useFleet(page, many);
    await page.setViewportSize({ width: 1440, height: 700 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();
    await page.waitForTimeout(600);

    // The sort may break activity ties by id, so read the actual top row rather
    // than assuming the first fixture remains first.
    const topId = await page.evaluate(
      () => document.querySelector<HTMLElement>('[data-testid="fleet-row"]')!.dataset.workspaceId!,
    );
    expect((await marquee(page, topId)).playState).toBe("running");

    await page.evaluate(() => {
      const list = document.querySelector<HTMLElement>('[data-testid="fleet-tree"]')!;
      list.scrollTop = list.scrollHeight;
    });
    await page.waitForTimeout(500);

    expect((await marquee(page, topId)).playState).toBe("paused");
  });
});

test.describe("the row keeps its states, menu and destination", () => {
  test("keeps its CardShell edge and geometry through selected, hover and keyboard focus", async ({
    page,
  }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/w/${ROWS[0].id}`);
    const row = page.locator(`[data-workspace-id="${ROWS[0].id}"]`);
    await expect(row).toBeVisible();

    const link = row.locator("a").first();
    await expect(link).toHaveAttribute("aria-current", "page");
    await expect(row.getByTestId("fleet-row-marker")).toBeAttached();

    // Selection belongs to the CardShell wrapper: its border participates in
    // the row boundary while the link stays a content and navigation region.
    const borders = await page.evaluate((workspaceId) => {
      const shell = document.querySelector<HTMLElement>(`[data-workspace-id="${workspaceId}"]`)!;
      const link = shell.querySelector<HTMLElement>("a")!;
      return {
        shellWidth: getComputedStyle(shell).borderTopWidth,
        linkWidth: getComputedStyle(link).borderTopWidth,
      };
    }, ROWS[0].id);
    expect(borders.shellWidth).toBe("1px");
    expect(borders.linkWidth).toBe("0px");

    const resting = await geometry(page, ROWS[0].id);
    await link.hover();
    await page.waitForTimeout(120);
    const hovered = await geometry(page, ROWS[0].id);
    expectSameGeometry(hovered, resting);

    await focusWithKeyboard(page, link);
    await expect(link).toBeFocused();
    const focused = await geometry(page, ROWS[0].id);
    expectSameGeometry(focused, resting);

    const other = page.locator('[data-testid="fleet-row"][data-selected="false"] a').first();
    await expect(other).not.toHaveAttribute("aria-current", "page");
  });

  test("reveals a full native tooltip for the title on hover and keyboard focus", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const row = page.locator(`[data-workspace-id="${ROWS[0].id}"]`);
    const link = row.locator("a").first();
    await expect(row).toBeVisible();

    await link.hover();
    await expect(page.getByRole("tooltip").filter({ hasText: ROWS[0].title })).toBeVisible();
    await expect(page.locator('[role="tooltip"]:not([data-state="closed"])')).toHaveCount(1);
    await page.keyboard.press("Escape");

    await focusWithKeyboard(page, link);
    await expect(link).toBeFocused();
    await expect(page.getByRole("tooltip").filter({ hasText: ROWS[0].title })).toBeVisible();
    await expect(page.locator('[role="tooltip"]:not([data-state="closed"])')).toHaveCount(1);
  });

  test("keeps options reachable by pointer and context menu", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const row = page.locator(`[data-workspace-id="${ROWS[0].id}"]`);
    await expect(row).toBeVisible();

    await row.hover();
    const options = row.getByRole("button", { name: "Workspace options" });
    await expect(options).toBeVisible();
    await options.click();
    await expect(page.getByRole("menuitem", { name: "Rename…" })).toBeVisible();
    await page.keyboard.press("Escape");

    // Right-click anywhere on the row opens the same menu, so the ellipsis is
    // discoverable rather than the only path to its actions.
    await row.click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Edit description…" })).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("reserves header space for options without covering title or body", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const row = page.locator(`[data-workspace-id="${ROWS[0].id}"]`);
    await expect(row).toBeVisible();

    const resting = await geometry(page, ROWS[0].id);
    await row.hover();
    await page.waitForTimeout(150);
    await expect(row.getByRole("button", { name: "Workspace options" })).toBeVisible();
    const hovered = await geometry(page, ROWS[0].id);

    // The small one-pixel allowance covers fractional root-density rounding;
    // an unreserved button would cover a meaningful portion of the title.
    const titleToButton = hovered.options!.x - (hovered.title!.x + hovered.title!.width);
    expect(titleToButton).toBeGreaterThanOrEqual(-1);
    expect(hovered.options!.y + hovered.options!.height).toBeLessThanOrEqual(
      hovered.metadata!.y,
    );
    expectSameGeometry(hovered, resting);
  });

  test("deletes a session only after the shared confirmation", async ({ page }) => {
    await useFleet(page, ROWS);
    // Kill is a POST to `…/kill`, not a DELETE. A route that never matches must
    // not make this assertion green by accident.
    const killed: string[] = [];
    await page.route(
      (url) => url.pathname.endsWith("/kill"),
      async (route) => {
        killed.push(new URL(route.request().url()).pathname);
        await route.fulfill({ status: 204, body: "" });
      },
    );

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const row = page.locator(`[data-workspace-id="${ROWS[0].id}"]`);
    await expect(row).toBeVisible();

    await row.click({ button: "right" });
    await page.getByTestId("fleet-row-delete").click();

    const dialog = page.getByTestId("kill-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId("kill-delete-branch")).toBeVisible();

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(killed).toEqual([]);

    await row.click({ button: "right" });
    await page.getByTestId("fleet-row-delete").click();
    await page.getByTestId("kill-confirm").click();
    await expect.poll(() => killed.length).toBe(1);
    expect(killed[0]).toContain(ROWS[0].id);
  });

  test("collapses the desktop rail and keeps the mobile sheet usable", async ({ page }) => {
    await useFleet(page, ROWS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByTestId("fleet-row").first()).toBeVisible();

    const aside = page.locator("aside").first();
    const expandedWidth = (await aside.boundingBox())!.width;
    await page.getByTestId("shell-sidebar-toggle").click();
    await page.waitForTimeout(300);

    // The icon rail is a distinct compact state, not an expanded rail with an
    // invisible list. Its exact expanded width stays owned by RAIL_WIDTH.
    const collapsedWidth = (await aside.boundingBox())!.width;
    expect(collapsedWidth).toBeLessThan(expandedWidth);
    expect(expandedWidth / collapsedWidth).toBeGreaterThan(9);
    const hidden = await page.evaluate(() => {
      const list = document.querySelector<HTMLElement>('aside [data-testid="fleet-row"]');
      const group = list?.closest<HTMLElement>("[inert]");
      return { inert: group !== null, opacity: group ? getComputedStyle(group).opacity : null };
    });
    expect(hidden.inert).toBe(true);
    expect(hidden.opacity).toBe("0");

    await page.getByTestId("shell-sidebar-toggle").click();
    await page.waitForTimeout(300);
    await expect(page.locator('aside [data-testid="fleet-row"]').first()).toBeVisible();
    expect((await aside.boundingBox())!.width).toBeCloseTo(expandedWidth, 0);

    // Below md the docked aside is absent and the same tree is presented in a
    // full-width Sheet, where its normal-density row remains compact.
    await page.setViewportSize({ width: 420, height: 850 });
    await page.getByTestId("shell-sidebar-sheet").click();
    const sheetRow = page.locator('[role="dialog"] [data-testid="fleet-row"]').first();
    await expect(sheetRow).toBeVisible();
    const box = (await sheetRow.boundingBox())!;
    expect(box.height).toBeGreaterThan(60);
    expect(box.height).toBeLessThanOrEqual(80);
    expect(box.width).toBeLessThanOrEqual(420);
  });
});
