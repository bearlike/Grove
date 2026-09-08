import { expect, test } from "@playwright/test";

import { STORAGE_STATE } from "../../playwright.config";
import { FIXTURE_ACTIVITY } from "./_fixtures";

for (const surface of ["desktop", "touch", "mobile"] as const) {
  test(`${surface} centers the top band and gives sidebar controls one gutter`, async ({ browser, baseURL }, testInfo) => {
    const context = await browser.newContext({
      viewport: { width: surface === "mobile" ? 420 : 1280, height: 900 },
      hasTouch: surface !== "desktop",
      storageState: STORAGE_STATE,
      reducedMotion: "reduce",
    });
    try {
      const page = await context.newPage();
      await page.addInitScript(() => {
        localStorage.setItem("grove.onboarding.seen", "true");
      });
      await page.goto(`${baseURL}/`);
      if (surface === "mobile") await page.getByTestId("shell-sidebar-sheet").click();
      const rail = surface === "mobile"
        ? page.getByRole("dialog", { name: "Grove workspaces" })
        : page.getByTestId("app-sidebar");
      await expect(rail.getByTestId("fleet-row").first()).toBeVisible();
      // Use the picker, not a storage-shape assumption, to reproduce the scoped screenshot.
      await rail.getByTestId("rail-project-context").click();
      await page.getByRole("option").filter({ hasText: FIXTURE_ACTIVITY.projects[0]!.repo_name }).first().click();
      await expect(rail.getByTestId("fleet-rail-group")).toHaveCount(1);
      await page.evaluate(async () => { await document.fonts.ready; });

      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => {
          document.documentElement.classList.remove("light", "dark");
          document.documentElement.classList.add(value);
        }, theme);
        const geometry = await rail.evaluate((root) => {
          const rect = (id: string) => root.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect();
          const band = rect("sidebar-brand-header");
          const search = rect("sidebar-search-trigger");
          const mark = rect("brand-mark");
          const picker = rect("rail-project-context");
          const create = rect("fleet-create-rail");
          const filter = rect("fleet-filter-trigger");
          const pause = rect("marquee-pause");
          const first = rect("fleet-row");
          const tree = rect("fleet-tree");
          const midpoint = (box: DOMRect) => box.top + box.height / 2;
          return {
            top: band.top, bottom: band.bottom,
            searchCenter: midpoint(search), markCenter: midpoint(mark),
            bandCenter: midpoint(band),
            headerToPicker: picker.top - band.bottom,
            pickerToActions: create.top - picker.bottom,
            actionsToCard: first.top - create.bottom,
            leftGutter: picker.left - tree.left,
            rightGutter: tree.right - picker.right,
            fadeDepth: Number.parseFloat(getComputedStyle(root.querySelector(".rail-scroll-depth")!, "::before").height),
            leftEdges: [picker.left, create.left, first.left],
            rightEdges: [picker.right, pause.right, first.right],
            actionCenters: [create, filter, pause].map(midpoint),
            actionHeights: [search, create, filter, pause].map(box => box.height),
          };
        });
        await testInfo.attach(`${surface}-${theme}-geometry`, { body: JSON.stringify(geometry, null, 2), contentType: "application/json" });
        await page.screenshot({ path: testInfo.outputPath(`${surface}-${theme}.png`) });
        expect.soft(geometry.top).toBeCloseTo(0, 1);
        expect.soft(geometry.searchCenter).toBeCloseTo(geometry.bandCenter - 0.5, 1);
        expect.soft(geometry.markCenter).toBeCloseTo(geometry.searchCenter, 1);
        for (const gap of [geometry.headerToPicker, geometry.pickerToActions, geometry.actionsToCard, geometry.rightGutter, geometry.fadeDepth]) {
          expect.soft(gap).toBeCloseTo(geometry.leftGutter, 1);
        }
        for (const edge of geometry.leftEdges) expect.soft(edge).toBeCloseTo(geometry.leftEdges[0]!, 1);
        for (const edge of geometry.rightEdges) expect.soft(edge).toBeCloseTo(geometry.rightEdges[0]!, 1);
        for (const center of geometry.actionCenters) expect.soft(center).toBeCloseTo(geometry.actionCenters[0]!, 1);
        for (const height of geometry.actionHeights) expect.soft(height).toBeGreaterThanOrEqual(surface === "desktop" ? 24 : 44);
        if (surface !== "mobile") {
          const header = (await page.getByTestId("shell-header").boundingBox())!;
          const toggle = (await page.getByTestId("shell-sidebar-toggle").boundingBox())!;
          expect.soft(header.y).toBeCloseTo(geometry.top, 1);
          expect.soft(header.y + header.height).toBeCloseTo(geometry.bottom, 1);
          expect.soft(toggle.y + toggle.height / 2).toBeCloseTo(geometry.searchCenter, 1);
        }
      }
    } finally {
      await context.close();
    }
  });
}
