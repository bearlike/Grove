import { expect, test } from "@playwright/test";
import { FIXTURE_WORKSPACES } from "./_fixtures";

const id = FIXTURE_WORKSPACES[0].id;

test("workspace navigation uses two lean rows without losing content space", async ({ page }) => {
  await page.goto(`/w/${id}`);
  const header = page.getByTestId("shell-header");
  await expect(header).toBeVisible();
  expect((await header.boundingBox())!.height).toBeCloseTo(32, 1);
  for (const view of ["transcript", "work", "split"] as const) {
    await page.getByTestId(`pane-${view}`).click();
    await expect(page.getByTestId(`pane-${view}`)).toHaveAttribute("data-state", "active");
    const control = (await page.getByTestId(`pane-${view}`).boundingBox())!;
    expect(control.height).toBeGreaterThanOrEqual(24);
    if (view !== "transcript") {
      const tabs = (await page.getByTestId("work-panel-tab-terminal").boundingBox())!;
      const shell = (await header.boundingBox())!;
      expect(tabs.y + tabs.height - shell.y).toBeLessThanOrEqual(72);
    }
  }
  await page.setViewportSize({ width: 420, height: 850 });
  await expect(header).toBeVisible();
  expect((await header.boundingBox())!.height).toBeCloseTo(32, 1);
  await expect(page.getByTestId("pane-work")).toBeInViewport();
  await page.screenshot({ path: "test-results/workspace-header-compact-mobile.png" });
});
