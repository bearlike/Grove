import { expect, test, type Browser, type Page } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";
import type { DashboardSnapshotView } from "@/lib/grove/api";

/**
 * The working loader's shared animation clock, measured in a browser because
 * every property it has is a LIFECYCLE property and none of them is visible in
 * static markup.
 *
 * The defect this exists to prevent: the clock used to register its
 * `visibilitychange` / reduced-motion listeners once per SUBSCRIBER, with one
 * shared `sync` reference. `addEventListener` dedupes by reference, so N
 * mounted loaders were ONE registry entry and the FIRST loader to unmount
 * removed it for every loader still on screen. The clock then never heard the
 * tab come back, and every matrix — the rail's marks and the transcript's
 * loader alike — held its last frame until a reload.
 *
 * Reproduced on the running app before the fix: four loaders mounted, tab
 * visible, one frame across eight samples in two seconds.
 *
 * WHY A SPEC AND NOT A UNIT TEST: the unit suite is `node`, so no component
 * here mounts an effect at all — the clock never starts, and every assertion
 * below would pass against the broken code for the wrong reason. The jsdom
 * environment does not load on this host, and its name must not be spelled in
 * prose here (see `webapp/CLAUDE.md`).
 */

/** Marks per loader, as `1` for a lit cell and `0` for a dim one. */
type Frame = string;

const WORKING_ROWS = 3;

/**
 * A fleet whose every row is working, so the rail mounts several marks driven
 * by the one module clock. The template workspace's first session already
 * reports `working`; only identity varies per row.
 */
function workingFleet(): DashboardSnapshotView {
  const template = FIXTURE_ACTIVITY.projects[0].workspaces[0];
  const workspaces = Array.from({ length: WORKING_ROWS }, (_, index) => ({
    ...structuredClone(template),
    state: {
      ...structuredClone(template.state),
      id: `eeeeeeeeeeeeeeeeeeeeeeeeeeeeee${index}`,
      title: `Working row ${index}`,
    },
  }));
  return {
    ...structuredClone(FIXTURE_ACTIVITY),
    projects: [{ ...structuredClone(FIXTURE_ACTIVITY.projects[0]), workspaces }],
    total_workspaces: workspaces.length,
  };
}

/**
 * Serve the synthetic fleet and silence the event stream — the fake daemon
 * pushes its own snapshot on connect and would otherwise overwrite this one
 * after the page had settled.
 */
async function useWorkingFleet(page: Page): Promise<void> {
  await page.route("**/api/grove/activity", (route) => route.fulfill({ json: workingFleet() }));
  await page.route("**/api/grove/events", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }),
  );
}

/** One sample of every mounted loader's matrix. */
async function frame(page: Page): Promise<Frame> {
  return page.evaluate(() =>
    [...document.querySelectorAll('[data-testid="working-mark"],[data-testid="working-loader"]')]
      .map((loader) =>
        [...loader.querySelectorAll("div > span")]
          .map((cell) => (cell.className.includes("opacity-90") ? "1" : "0"))
          .join(""),
      )
      .join("|"),
  );
}

/**
 * Sample the matrix over time.
 *
 * `STEP_MS` is 225, so eight samples 250ms apart span ~2s and cross several
 * steps — a frozen clock is unambiguous rather than merely unlucky.
 */
async function frames(page: Page, count = 8): Promise<readonly Frame[]> {
  const seen: Frame[] = [];
  for (let index = 0; index < count; index += 1) {
    seen.push(await frame(page));
    await page.waitForTimeout(250);
  }
  return seen;
}

/** Drive the real `visibilitychange` edge the clock listens for. */
async function setHidden(page: Page, hidden: boolean): Promise<void> {
  await page.evaluate((value) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (value ? "hidden" : "visible"),
    });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
}

test.beforeEach(async ({ page }) => {
  await useWorkingFleet(page);
  await page.goto("/fleet");
  await expect(page.getByTestId("working-mark").first()).toBeVisible();
});

test("every mounted mark animates, and they all show the same frame", async ({ page }) => {
  // Counted, never assumed: `/fleet` draws each working workspace twice — once
  // in the rail and once on its card — so a hard-coded expectation here would
  // pin the page's composition rather than the clock's contract. What matters
  // is only that more than one loader is mounted.
  const mounted = await page.getByTestId("working-mark").count();
  expect(mounted).toBeGreaterThan(1);

  const seen = await frames(page);

  // Moving at all is the baseline; without it the two tests below could pass
  // against a clock that never started.
  expect(new Set(seen).size).toBeGreaterThan(1);

  // ONE clock, so the marks cannot drift out of phase — the reason the clock is
  // shared at all. Each sample joins every loader's matrix, so a per-loader
  // interval would show differing halves within a single sample.
  for (const sample of seen) {
    const perLoader = sample.split("|");
    expect(perLoader).toHaveLength(mounted);
    expect(new Set(perLoader).size).toBe(1);
  }
});

test("a hidden document stands the animation down, and returning resumes it", async ({ page }) => {
  await setHidden(page, true);
  // Let any in-flight step land before sampling, or the first two samples
  // straddle the edge and a held frame reads as a moving one.
  await page.waitForTimeout(400);

  const hidden = await frames(page, 4);
  expect(new Set(hidden).size).toBe(1);

  await setHidden(page, false);
  const visible = await frames(page);
  expect(new Set(visible).size).toBeGreaterThan(1);
});

/**
 * THE READER WHO ASKED FOR LESS MOTION STILL GETS A MOVING MARK.
 *
 * Every test above runs at Playwright's default `no-preference`, so the suite
 * only ever exercised the branch where the clock runs — which is exactly why
 * the frozen matrix survived five fixes and reached a real reader. Under
 * `reduce` all three animated layers stand down by design (the clock's
 * `wanted()`, the breathe's `no-preference` query, the label's
 * `motion-reduce:animate-none`), and what was left on screen was the vendored
 * position math at tick zero: a static grid indistinguishable from a bug.
 *
 * This asserts the CSS fallback in `globals.css` supplies the motion instead.
 * It cannot go through `frame()` — that samples the CLASS attribute, and the
 * fallback animates a computed `opacity` the class never changes, so the class
 * census is frozen here CORRECTLY. Read the rendered value instead.
 */
test.describe("reduced motion", () => {
  /** Every cell's computed opacity, as the browser is painting it right now. */
  async function opacities(page: Page): Promise<readonly string[]> {
    return page.evaluate(() =>
      [...document.querySelectorAll('[data-testid="working-mark"] div > span')].map(
        (cell) => getComputedStyle(cell).opacity,
      ),
    );
  }

  /**
   * A context that asks for less motion, opened explicitly rather than through
   * `test.use` — the project's fixture type does not carry `reducedMotion`, and
   * both existing reduced-motion specs (`top-left-layout`, `sidebar-sessions`)
   * open their own context for the same reason.
   */
  async function reducedMotionPage(browser: Browser): Promise<Page> {
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    await useWorkingFleet(page);
    await page.goto("/fleet");
    await expect(page.getByTestId("working-mark").first()).toBeVisible();
    return page;
  }

  test("the matrix animates on CSS alone when the JS clock never starts", async ({ browser }) => {
    const page = await reducedMotionPage(browser);
    try {
      // The clock must genuinely be down, or this passes on the clock's own
      // work and proves nothing about the fallback. `frame()` reads the class
      // the clock writes, so one distinct value across ~2s is that proof.
      const classFrames = await frames(page);
      expect(new Set(classFrames).size).toBe(1);

      // The cells still have to be MOVING. Sampled off the animation's own
      // period (2.4s) so two samples cannot land on one phase by luck.
      const seen = new Set<string>();
      for (let index = 0; index < 8; index += 1) {
        seen.add((await opacities(page)).join(","));
        await page.waitForTimeout(180);
      }
      expect(seen.size).toBeGreaterThan(1);

      // The stagger IS the pattern with the clock down: without the per-cell
      // delays every cell would hold one identical value and the grid would
      // pulse as a single block rather than wave.
      const sample = await opacities(page);
      expect(sample.length).toBeGreaterThan(1);
      expect(new Set(sample).size).toBeGreaterThan(1);
    } finally {
      await page.context().close();
    }
  });

  test("the working label shimmers while the rail's empty label costs nothing", async ({
    browser,
  }) => {
    const page = await reducedMotionPage(browser);
    try {
      // `WorkingMark` passes an empty label deliberately, and a shimmer over no
      // glyphs is a per-frame repaint of nothing — the `:empty` rule must still
      // win under `reduce`, where this rule re-arms the shimmer for real labels.
      const mark = page.getByTestId("working-mark").locator(".shimmer").first();
      expect(await mark.evaluate((node) => getComputedStyle(node).animationName)).toBe("none");

      // The workspace transcript is the one surface with a real label. Navigate
      // there rather than fabricating one: that proves `motion-reduce:animate-none`
      // from the vendored `ShimmerLabel` was actually overridden in the composed
      // surface the reader reported, with a non-zero duration and infinite loop.
      // Stay inside the synthetic fleet: its first row has the working activity
      // the loader needs. Navigating to the fixture's original id would leave
      // that snapshot and land on a session picker, proving nothing about the
      // label. The fake daemon's peek is id-agnostic, so the row route supplies
      // the workspace body while the synthetic activity supplies working state.
      await page.goto(`/w/${workingFleet().projects[0].workspaces[0].state.id}`);
      await page.getByTestId("pane-transcript").click();
      const label = page.getByTestId("working-loader").locator(".shimmer");
      await expect(label).toHaveText("Working");
      const animation = await label.evaluate((node) => {
        const style = getComputedStyle(node);
        return {
          name: style.animationName,
          duration: style.animationDuration,
          iterations: style.animationIterationCount,
        };
      });
      expect(animation.name).toBe("tw-shimmer");
      expect(animation.duration).not.toBe("0s");
      expect(animation.iterations).toBe("infinite");
    } finally {
      await page.context().close();
    }
  });
});

test("some loaders unmounting leaves the clock listening for the others", async ({ page }) => {
  // THE REGRESSION ITSELF, in the order that produced it: hide the tab, let
  // some loaders unmount while hidden, then come back.
  //
  // The unmount is a real client-side navigation rather than a re-render with
  // fewer rows: while the document is hidden react-query does not refetch, so
  // a fixture swap would never reach the page. Leaving `/fleet` unmounts every
  // card's mark and the rail's survive, which is precisely the shape of the
  // bug — the departing components' cleanup used to strip the one shared
  // listener out from under the components still on screen.
  const before = await page.getByTestId("working-mark").count();

  await setHidden(page, true);
  await page.waitForTimeout(400);

  await page.getByRole("link", { name: "Usage", exact: true }).click();
  await expect(page).toHaveURL(/\/usage$/);
  const after = await page.getByTestId("working-mark").count();
  // The premise of the test: some left, some stayed. Without this the
  // assertion below could pass on a page that never unmounted anything.
  expect(after).toBeGreaterThan(0);
  expect(after).toBeLessThan(before);

  await setHidden(page, false);

  // Before the fix this held one frame forever: the survivors were still
  // mounted and still subscribed, but nothing was left listening for the tab.
  const seen = await frames(page);
  expect(new Set(seen).size).toBeGreaterThan(1);
});
