import { expect, test } from "@playwright/test";

import type { SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

test("long diff lines scroll inside the card and metadata stays in its header", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const longLine = `value = '${"long literal ".repeat(60)}'`;
  await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
    const response = await route.fetch();
    const payload: SessionDetailView = await response.json();
    const entry = payload.turns.flatMap(turn => turn.entries).find(entry => entry.file_edit);
    if (!entry?.file_edit) throw new Error("Expected a file edit fixture");
    entry.file_edit.old_text = "old_value = 1\n";
    entry.file_edit.new_text = `${longLine}\n`;
    entry.tool = {
      name: "Edit", tool_use_id: "edit-diff-fixture", status: "ok",
      input: {}, result: "Applied", duration_ms: 4400,
    };
    await route.fulfill({ json: payload });
  });
  await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
  const card = page.getByTestId("file-edit-card").first();
  const header = card.getByRole("button");
  await expect(header).toContainText("4.4s");
  await expect(header).toContainText("+1");
  await expect(header).toContainText("−1");
  await header.click();
  const body = card.getByRole("region", { name: "Changes to src/api/health.py" });
  await expect(body).toContainText(longLine);
  await expect(card.locator('[data-slot="diff-viewer-split-left"][data-type="del"]')).toContainText("old_value = 1");
  const geometry = await body.evaluate(element => {
    element.scrollLeft = 150;
    return {
      scrolled: element.scrollLeft,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      lineHeight: element.querySelector('[data-type="add"]')!.getBoundingClientRect().height,
      fontSize: parseFloat(getComputedStyle(element).fontSize),
    };
  });
  expect(geometry.scrolled).toBeGreaterThan(0);
  expect(geometry.overflow).toBeLessThanOrEqual(1);
  expect(geometry.lineHeight).toBeLessThan(geometry.fontSize * 3);
  await expect(header).toContainText("4.4s");
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 1280]) {
    test(`file diff has one collapsible header at ${width}px in ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
      await page.getByRole("tab", { name: "Transcript", exact: true }).click();
      const card = page.getByTestId("file-edit-card").first();
      const header = card.getByRole("button");
      await expect(card).toBeVisible();
      await expect(header).toHaveAttribute("aria-expanded", "false");
      await expect(card.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(0);
      await expect(header).toContainText("health.py");
      await expect(header).toContainText("+2");

      await header.click();
      await expect(header).toHaveAttribute("aria-expanded", "true");
      await expect(card.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(2);
      await expect(card.locator('[data-slot="diff-viewer-split-left"][data-type="del"]')).toHaveCount(0);
      await expect(card.locator('[data-slot="diff-viewer-split-right"][data-type="add"]').last()).toContainText("return {'status': 'ok'}");
      await expect(card.locator('[data-slot="diff-viewer-header"]')).toHaveCount(0);
      await expect(card.locator('[data-slot="card"], [data-slot="diff-viewer"]')).toHaveCount(0);
      await expect(card.getByText("health.py", { exact: true })).toHaveCount(1);
      const body = card.getByRole("region", { name: "Changes to src/api/health.py" });
      await expect(body).toContainText("return {'status': 'ok'}");
      const geometry = await body.evaluate(element => {
        const card = element.closest('[data-testid="file-edit-card"]')!;
        return {
          inset: element.getBoundingClientRect().left - card.getBoundingClientRect().left,
          overflow: document.documentElement.scrollWidth - window.innerWidth,
        };
      });
      expect(geometry.inset).toBeLessThanOrEqual(2);
      expect(geometry.overflow).toBeLessThanOrEqual(1);

      await header.focus();
      await page.keyboard.press("Enter");
      await expect(header).toHaveAttribute("aria-expanded", "false");
      await expect(card.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(0);
      await page.keyboard.press("Space");
      await expect(header).toHaveAttribute("aria-expanded", "true");
      await expect(card.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(2);
      await card.screenshot({ path: test.info().outputPath(`file-diff-${theme}-${width}.png`) });
    });
  }
}
