import { expect, test, type Locator, type Page } from "@playwright/test";

import { STORAGE_STATE } from "../../playwright.config";
import { FIXTURE_WORKSPACES } from "./_fixtures";

const PLAIN_WORKSPACE = FIXTURE_WORKSPACES[0].id;
const DIAGRAM_WORKSPACE = "diagram-workspace";
const WORK_TOOLS = "Workspace tools";
const PANES = "Workspace panes";

const TABS = [
  { value: "terminal", label: "Terminal", content: "terminal-tab" },
  { value: "changes", label: "Changes", content: "changes-tab" },
  { value: "diagram", label: "Diagram", content: "diagram-tab" },
  { value: "files", label: "Files", content: "files-tab" },
  { value: "info", label: "Info", content: "info-tab" },
  { value: "controls", label: "Controls", content: "controls-tab" },
] as const;
const PLAIN_TABS = TABS.filter((tab) => tab.value !== "diagram");

const MODES = ["all", "active", "icons"] as const;
type Mode = (typeof MODES)[number];

const list = (page: Page, label: string): Locator => page.locator(`[aria-label='${label}']`);
const workList = (page: Page): Locator => page.getByTestId("work-panel").getByRole("tablist");
const trigger = (page: Page, value: string): Locator => page.getByTestId(`work-panel-tab-${value}`);
const overflowOf = (row: Locator): Promise<number> =>
  row.evaluate((element) => element.scrollWidth - element.clientWidth);

async function modeOf(row: Locator): Promise<Mode> {
  const mode = await row.getAttribute("data-label-mode");
  expect(MODES).toContain(mode as Mode);
  return mode as Mode;
}

async function marker(row: Locator) {
  return row.evaluate((element) => {
    const active = element.querySelector<HTMLElement>("[role='tab'][aria-selected='true']");
    if (!active) throw new Error("no selected trigger in the tab row");
    const rect = active.getBoundingClientRect();
    const style = getComputedStyle(active);
    const left = Number.parseFloat(style.borderLeftWidth) || 0;
    const foot = Number.parseFloat(style.borderBottomWidth) || 0;
    const want = { x: rect.x, width: rect.width, bottom: rect.bottom };
    const overlay = element.querySelector<HTMLElement>("[data-slot='tabs-active-indicator']");
    if (overlay) {
      const box = overlay.getBoundingClientRect();
      const paint = getComputedStyle(overlay);
      return {
        source: "indicator",
        x: box.x,
        width: box.width,
        bottom: box.bottom,
        opacity: Number.parseFloat(paint.opacity),
        want,
      };
    }
    const after = getComputedStyle(active, "::after");
    if (after.content === "none") throw new Error("the selected trigger paints no mark");
    return {
      source: "pseudo",
      x: rect.x + left + (Number.parseFloat(after.left) || 0),
      width: Number.parseFloat(after.width),
      bottom: rect.bottom - foot - (Number.parseFloat(after.bottom) || 0),
      opacity: Number.parseFloat(after.opacity),
      want,
    };
  });
}

function expectCovers(sample: Awaited<ReturnType<typeof marker>>, label: string): void {
  expect(Math.abs(sample.x - sample.want.x), `${label} mark left (${sample.source})`).toBeLessThanOrEqual(2);
  expect(
    Math.abs(sample.width - sample.want.width),
    `${label} mark width (${sample.source})`,
  ).toBeLessThanOrEqual(2);
  expect(sample.opacity, `${label} mark is painted (${sample.source})`).toBeGreaterThan(0.9);
}

async function openSplit(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/w/${PLAIN_WORKSPACE}`);
  await page.getByTestId("pane-split").click();
  await expect(page.getByTestId("work-panel")).toBeVisible();
  await page.evaluate(async () => { await document.fonts.ready; });
}

async function resizeWorkPane(page: Page, width: number): Promise<void> {
  const panel = page.getByTestId("work-panel");
  const box = (await page.locator('[data-slot="resizable-handle"]').boundingBox())!;
  const current = (await panel.boundingBox())!.width;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + current - width, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => Math.abs((await panel.boundingBox())!.width - width)).toBeLessThan(3);
}

test.describe("workspace tab rows", () => {
  for (const width of [320, 390] as const) {
    test(`reaches every built-in tool at ${width}px with no horizontal scroll`, async ({ page }) => {
      for (const [workspace, expected] of [
        [PLAIN_WORKSPACE, PLAIN_TABS],
        [DIAGRAM_WORKSPACE, TABS],
      ] as const) {
        await page.setViewportSize({ width, height: 780 });
        await page.goto(`/w/${workspace}`);
        await page.getByTestId("pane-work").click();
        const row = workList(page);
        await expect(row.getByRole("tab")).toHaveCount(expected.length);

        for (const tab of expected) {
          // The accessible name survives every label mode; the visible text
          // does not, so reachability is asserted through the name.
          await expect(trigger(page, tab.value)).toHaveAccessibleName(tab.label);
          await expect(trigger(page, tab.value)).toBeInViewport();
          await trigger(page, tab.value).click();
          await expect(trigger(page, tab.value)).toHaveAttribute("aria-selected", "true");
          await expect(page.getByTestId(tab.content)).toBeVisible();
        }

        const sideways = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          body: document.body.scrollWidth - document.body.clientWidth,
        }));
        expect(await overflowOf(row), `${workspace} row overflow`).toBeLessThanOrEqual(1);
        expect(sideways.document, `${workspace} document overflow`).toBeLessThanOrEqual(1);
        expect(sideways.body, `${workspace} body overflow`).toBeLessThanOrEqual(1);
      }
    });
  }

  test("hands labels back through all → active → icons as the pane narrows", async ({ page }) => {
    await openSplit(page);
    const row = workList(page);
    const seen: { width: number; mode: Mode }[] = [];

    for (const width of [500, 400, 320, 240, 180, 150] as const) {
      await row.evaluate((element, width) => { element.parentElement!.style.width = `${width}px`; }, width);
      await page.evaluate(() => new Promise(requestAnimationFrame));
      const mode = await modeOf(row);
      seen.push({ width, mode });
      expect(await overflowOf(row), `row overflow at ${width}px`).toBeLessThanOrEqual(1);

      const labels = await row.evaluate((element) =>
        [...element.querySelectorAll<HTMLElement>("[role='tab']")].map((tab) => {
          const span = tab.querySelector<HTMLElement>("[data-tab-label]");
          if (!span) return null;
          const inner = span.firstElementChild as HTMLElement | null;
          return {
            value: tab.getAttribute("data-testid"),
            selected: tab.getAttribute("aria-selected") === "true",
            text: span.textContent?.trim() ?? "",
            shown:
              getComputedStyle(span).visibility !== "hidden" &&
              span.getBoundingClientRect().width > 0,
            measurable: Math.max(span.scrollWidth, inner?.getBoundingClientRect().width ?? 0),
          };
        }),
      );
      expect(labels.every((entry) => entry !== null), `${width}px: every trigger has a label`).toBe(true);
      for (const label of labels) {
        expect(label!.text, `${width}px ${label!.value} text`).not.toBe("");
        expect(label!.measurable, `${width}px ${label!.value} measurable`).toBeGreaterThan(0);
      }
      const shown = labels.filter((entry) => entry!.shown);
      if (mode === "all") expect(shown, `${width}px shown`).toHaveLength(labels.length);
      if (mode === "active") {
        expect(shown, `${width}px shown`).toHaveLength(1);
        expect(shown[0]!.selected, `${width}px shows the selected label`).toBe(true);
      }
      if (mode === "icons") expect(shown, `${width}px shown`).toHaveLength(0);
    }

    expect(new Set(seen.map((sample) => sample.mode)), JSON.stringify(seen)).toEqual(new Set(MODES));
    const ranks = seen.map((sample) => MODES.indexOf(sample.mode));
    for (let index = 1; index < ranks.length; index += 1) {
      expect(ranks[index]!, JSON.stringify(seen)).toBeGreaterThanOrEqual(ranks[index - 1]!);
    }
  });

  test("keeps one label mode while the reader cycles the tabs", async ({ page }) => {
    await openSplit(page);
    const row = workList(page);
    await resizeWorkPane(page, 300);
    const initial = await modeOf(row);
    expect(initial).toBe("active");

    for (const tab of PLAIN_TABS) {
      await trigger(page, tab.value).click();
      await expect(trigger(page, tab.value)).toHaveAttribute("aria-selected", "true");
      expect(await modeOf(row), `mode after selecting ${tab.value}`).toBe(initial);
      expect(await overflowOf(row), `overflow after ${tab.value}`).toBeLessThanOrEqual(1);
    }
  });

  test("keeps the active mark under its trigger through a held drag", async ({ page }) => {
    await openSplit(page);
    const row = workList(page);
    await trigger(page, "controls").click();

    const box = (await page.locator('[data-slot="resizable-handle"]').boundingBox())!;
    const [originX, originY] = [box.x + box.width / 2, box.y + box.height / 2];
    await page.mouse.move(originX, originY);
    await page.mouse.down();
    const samples: Awaited<ReturnType<typeof marker>>[] = [];
    for (const dx of [-80, -180, -280, -360, -430] as const) {
      // One move, no `steps`, no wait: catch the frame the reader is looking at.
      await page.mouse.move(originX + dx, originY);
      samples.push(await marker(row));
    }
    await page.mouse.up();

    samples.forEach((sample, index) => expectCovers(sample, `drag sample ${index}`));
    // The drag genuinely moved the trigger, or the samples prove nothing.
    expect(
      Math.abs(samples.at(-1)!.want.x - samples[0]!.want.x) +
        Math.abs(samples.at(-1)!.want.width - samples[0]!.want.width),
      "the drag moved the selected trigger",
    ).toBeGreaterThan(4);
  });

  test("closes each tab row with a single rule the active mark lands on", async ({ page }) => {
    await openSplit(page);
    await trigger(page, "info").click();
    await expect(page.getByTestId("info-tab")).toBeVisible();

    for (const label of [PANES, WORK_TOOLS] as const) {
      const rule = await list(page, label).evaluate((element) => {
        const bordered: { tag: string; bottom: number }[] = [];
        let node: HTMLElement | null = element.parentElement;
        // Five levels is past the real nesting and stops short of `html`, whose
        // own border would be a false positive.
        for (let depth = 0; node && depth < 5; depth += 1) {
          if (Number.parseFloat(getComputedStyle(node).borderBottomWidth) > 0) {
            bordered.push({ tag: node.tagName.toLowerCase(), bottom: node.getBoundingClientRect().bottom });
          }
          node = node.parentElement;
        }
        return {
          listBorder: Number.parseFloat(getComputedStyle(element).borderBottomWidth),
          listClass: element.className,
          bordered,
        };
      });

      expect(rule.listBorder, `${label} list draws no rule of its own`).toBe(0);
      expect(rule.listClass, `${label} list is marked`).toContain("workspace-tab-list");
      expect(rule.bordered, `${label} closing rules`).toHaveLength(1);
      const mark = await marker(list(page, label));
      expectCovers(mark, label);
      // The mark ends where the band's one rule ends, which is what makes the
      // active tab read as a break IN that rule rather than a stripe above it.
      expect(
        Math.abs(mark.bottom - rule.bordered[0]!.bottom),
        `${label} mark sits on the closing rule`,
      ).toBeLessThanOrEqual(1.5);
    }
    await expect(page.locator(".workspace-tab-strip.workspace-work-tabs")).toHaveCount(1);
  });

  test("moves selection by keyboard and floors every fine-pointer target", async ({ page }) => {
    await openSplit(page);
    await workList(page).evaluate((element) => { element.parentElement!.style.width = "150px"; });
    await expect(workList(page)).toHaveAttribute("data-label-mode", "icons");
    expect(await modeOf(workList(page))).toBe("icons");

    const [first, second, last] = [
      trigger(page, PLAIN_TABS[0].value),
      trigger(page, PLAIN_TABS[1].value),
      trigger(page, PLAIN_TABS.at(-1)!.value),
    ];
    await first.click();
    await expect(first).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(second).toBeFocused();
    await expect(second).toHaveAttribute("aria-selected", "true");
    await expect(first).toHaveAttribute("aria-selected", "false");
    await page.keyboard.press("ArrowLeft");
    await expect(first).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("End");
    await expect(last).toBeFocused();
    await expect(last).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(first).toBeFocused();
    await expect(first).toHaveAttribute("aria-selected", "true");
    for (const tab of PLAIN_TABS) {
      await expect(trigger(page, tab.value)).toHaveAccessibleName(tab.label);
    }
    // The two selection attributes must agree, or a tooltip composition has
    // overwritten `data-state` and every spec reading it is silently lying.
    await expect(first).toHaveAttribute("data-state", "active");

    const boxes = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("[role='tab']")].map((tab) => {
        const rect = tab.getBoundingClientRect();
        return { name: tab.getAttribute("data-testid"), width: rect.width, height: rect.height };
      }),
    );
    expect(boxes.length).toBeGreaterThan(0);
    for (const box of boxes) {
      expect(box.height, `${box.name} height`).toBeGreaterThanOrEqual(24);
      expect(box.width, `${box.name} width`).toBeGreaterThanOrEqual(24);
    }
    expect((await page.getByTestId("shell-header").boundingBox())!.height).toBeGreaterThanOrEqual(32);
  });
});

test("floors targets and both bands under a finger", async ({ browser, baseURL }) => {
  for (const width of [320, 390] as const) {
    const context = await browser.newContext({
      baseURL,
      storageState: STORAGE_STATE,
      viewport: { width, height: 800 },
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    try {
      await page.goto(`/w/${PLAIN_WORKSPACE}`);
      expect(
        await page.evaluate(() => matchMedia("(pointer: coarse)").matches),
        "coarse-pointer emulation took",
      ).toBe(true);
      await page.getByTestId("pane-work").tap();
      const row = workList(page);
      await expect(row.getByRole("tab")).toHaveCount(PLAIN_TABS.length);

      const geometry = await row.evaluate((element) => ({
        overflow: element.scrollWidth - element.clientWidth,
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        band: (element.parentElement ?? element).getBoundingClientRect().height,
        header: document
          .querySelector<HTMLElement>("[data-testid='shell-header']")!
          .getBoundingClientRect().height,
        triggers: [...element.querySelectorAll<HTMLElement>("[role='tab']")].map((tab) => {
          const rect = tab.getBoundingClientRect();
          return { name: tab.getAttribute("data-testid"), width: rect.width, height: rect.height };
        }),
      }));

      expect(geometry.overflow, `${width}px row overflow`).toBeLessThanOrEqual(1);
      expect(geometry.document, `${width}px document overflow`).toBeLessThanOrEqual(1);
      for (const tab of geometry.triggers) {
        expect(tab.height, `${width}px ${tab.name} height`).toBeGreaterThanOrEqual(44);
        expect(tab.width, `${width}px ${tab.name} width`).toBeGreaterThanOrEqual(44);
      }
      // 44px of target plus the band's own 1px closing rule — any less and the
      // rule was taken out of the reader's thumb.
      expect(geometry.band, `${width}px coarse work band`).toBeGreaterThanOrEqual(45);
      expect(geometry.header, `${width}px coarse header band`).toBeGreaterThanOrEqual(44);

      for (const tab of PLAIN_TABS) {
        await expect(trigger(page, tab.value)).toHaveAccessibleName(tab.label);
        await trigger(page, tab.value).tap();
        await expect(page.getByTestId(tab.content)).toBeVisible();
      }
    } finally {
      await context.close();
    }
  }
});
