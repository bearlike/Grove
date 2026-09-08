import { expect, test } from "@playwright/test";
import { openSharpSurface } from "./sharp-surface-probe";

test("Controls retains six cards and bounded icon-label targets at 300px", async ({ page }) => {
  await page.route("**/api/grove/share-policy**", route => route.fulfill({ json: { ttl_seconds: 604800, passcode_set: false } }));
  await openSharpSurface(page, "controls", 300, "dark");
  const controls = page.getByTestId("controls-tab");
  await expect(controls.locator('[data-slot="card-title"]')).toHaveText([
    "Session", "Send keys", "Sharing", "Commands", "Skills", "MCP servers",
  ]);
  const buttons = controls.locator('button[data-slot="button"]');
  const census = await buttons.evaluateAll(elements => elements.map(element => {
    const box = element.getBoundingClientRect();
    return { label: element.textContent?.trim(), height: box.height, mark: !!element.querySelector('svg, [data-slot="kbd"]') };
  }));
  expect(census.length).toBeGreaterThan(10);
  for (const control of census) {
    expect(control.label).toBeTruthy();
    expect(control.mark, control.label).toBe(true);
    expect(control.height, control.label).toBeGreaterThanOrEqual(24);
  }
  const copy = controls.getByRole("button", { name: "Copy attach command", exact: true });
  await copy.focus();
  await page.keyboard.press("Tab");
  const focus = await page.locator(":focus").evaluate(element => ({
    visible: element.matches(":focus-visible"),
    border: getComputedStyle(element).borderWidth,
    halo: getComputedStyle(element).boxShadow,
  }));
  expect(focus.visible).toBe(true);
  expect(focus.border).not.toBe("0px");
  expect(focus.halo).not.toBe("none");
  await expect(controls.getByRole("combobox", { name: "Link expiry" })).toBeVisible();
});
