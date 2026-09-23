import { expect, test } from "@playwright/test";

import { FIXTURE_ACTIVITY } from "./_fixtures";

for (const theme of ["light", "dark"] as const) {
  for (const mobile of [false, true]) {
    test(`${theme} ${mobile ? "mobile" : "desktop"} sidebar keeps depth and breathing room inside its scroller`, async ({ browser, baseURL }) => {
      const context = await browser.newContext({
        viewport: { width: mobile ? 390 : 1280, height: 720 },
        isMobile: mobile,
        hasTouch: mobile,
        storageState: "tests/e2e/.auth/storage-state.json",
      });
      const page = await context.newPage();
      await page.addInitScript((theme) => {
        localStorage.setItem("theme", theme);
        localStorage.setItem("grove.onboarding.seen", "true");
      }, theme);
      const snapshot = structuredClone(FIXTURE_ACTIVITY);
      const project = snapshot.projects[0];
      const template = project.workspaces[0];
      project.workspaces = Array.from({ length: 16 }, (_, index) => ({
        ...structuredClone(template),
        state: { ...template.state, id: `depth-${index}`, title: `Workspace ${index + 1}` },
      }));
      await page.route("**/api/grove/activity", (route) => route.fulfill({ json: snapshot }));
      await page.route("**/api/grove/events", (route) =>
        route.fulfill({ contentType: "text/event-stream", body: "" }),
      );
      await page.goto(`${baseURL}/`);
      if (mobile) {
        await page.getByTestId("shell-sidebar-sheet").click();
        await page.getByRole("dialog", { name: "Grove workspaces", exact: true }).evaluate(async (element) => {
          await Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
        });
      }
      const tree = page.getByTestId("fleet-tree").filter({ visible: true });
      await expect(tree.getByTestId("fleet-row")).toHaveCount(16);

      const measure = () => tree.evaluate((element) => {
        const frame = element.parentElement!;
        const box = element.getBoundingClientRect();
        const frameBox = frame.getBoundingClientRect();
        const first = element.firstElementChild!.getBoundingClientRect();
        const rows = element.querySelectorAll('[data-testid="fleet-row"]');
        const last = rows[rows.length - 1].getBoundingClientRect();
        const top = getComputedStyle(frame, "::before");
        const bottom = getComputedStyle(frame, "::after");
        return {
          topGap: first.top - box.top,
          bottomGap: box.bottom - last.bottom,
          scrollable: element.scrollHeight > element.clientHeight,
          top: { image: top.backgroundImage, height: parseFloat(top.height), pointer: top.pointerEvents },
          bottom: { image: bottom.backgroundImage, height: parseFloat(bottom.height), pointer: bottom.pointerEvents },
          frame: { x: frameBox.x, y: frameBox.y, width: frameBox.width, height: frameBox.height },
          scroller: { x: box.x, y: box.y, width: box.width, height: box.height },
          overflow: getComputedStyle(frame).overflow,
          position: getComputedStyle(frame).position,
        };
      });
      const start = await measure();
      expect(start.scrollable).toBe(true);
      expect(start.topGap).toBeCloseTo(start.top.height, 1);
      expect(start.top.image).toContain("linear-gradient");
      expect(start.bottom.image).toContain("linear-gradient");
      expect(start.top.pointer).toBe("none");
      expect(start.bottom.pointer).toBe("none");
      expect(start.top.height).toBeGreaterThan(0);
      expect(start.top.height).toBeLessThanOrEqual(start.topGap);
      expect(start.frame).toEqual(start.scroller);
      expect(start.overflow).toBe("hidden");
      expect(start.position).toBe("relative");

      await tree.evaluate((element) => { element.scrollTop = element.scrollHeight; });
      const end = await measure();
      // Scroll offsets round to device pixels while card bounds can be fractional.
      // The same rounding bounds the second check: a card's fractional height
      // (87.2px at the 80% root) can leave the gap a sub-pixel short of the fade.
      expect(Math.abs(end.bottomGap - end.bottom.height)).toBeLessThanOrEqual(1);
      expect(end.bottom.height).toBeLessThanOrEqual(end.bottomGap + 0.5);
      expect(end.frame).toEqual(start.frame);
      await page.screenshot({ path: test.info().outputPath(`sidebar-${theme}-${mobile ? "mobile" : "desktop"}.png`) });
      await context.close();
    });
  }
}
