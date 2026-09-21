import { expect, test, type Page } from "@playwright/test";
import type { DigestEntryView, SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

/**
 * A file edit is folded into the tool timeline (#755/#756/#775): opening an
 * edit step gives a native split diff inside a `CardShell`, not a standalone
 * `file-edit-card`. That anatomy is pinned by `tool-timeline.spec.ts`; this
 * file owns what it does NOT — scroll containment on a long line, and the
 * collapsed/expanded/keyboard cycle at two widths and both themes.
 */
function edit(
  path: string,
  display: string,
  oldText: string,
  newText: string,
  id: string,
  durationMs = 900,
): DigestEntryView {
  return {
    role: "file_edit", text: `Edit ${display}`, question: null, todo: null,
    tool: { name: "Edit", input: { file_path: path }, tool_use_id: id, status: "ok", duration_ms: durationMs, result: "Applied", body: "inline" },
    file_edit: { path, display_path: display, old_text: oldText, new_text: newText },
  };
}

async function routeSingleEdit(page: Page, entries: DigestEntryView[]): Promise<void> {
  await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async (route) => {
    const response = await route.fetch();
    const payload: SessionDetailView = await response.json();
    payload.turns = [{ user_text: "Edit the health endpoint", started_at: null, entries }];
    await route.fulfill({ json: payload });
  });
}

test("long diff lines scroll inside the card and metadata stays in its header", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const longLine = `value = '${"long literal ".repeat(60)}'`;
  await routeSingleEdit(page, [
    edit("/w/src/api/health.py", "src/api/health.py", "old_value = 1\n", `${longLine}\n`, "edit-diff-fixture", 4400),
  ]);
  await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
  const group = page.getByTestId("tool-call-group").first();
  await expect(group).toBeVisible();
  await group.getByRole("button").first().click();
  const step = group.locator('[data-tool-use-id="edit-diff-fixture"]');
  const header = step.getByRole("button").first();
  await expect(header).toContainText("4.4s");
  await expect(header).toContainText("+1");
  await expect(header).toContainText("−1");
  await header.click();
  const body = step.getByRole("region", { name: "Changes to src/api/health.py" });
  await expect(body).toContainText(longLine);
  await expect(step.locator('[data-slot="diff-viewer-split-left"][data-type="del"]')).toContainText("old_value = 1");
  const geometry = await body.evaluate((element) => {
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
  // Metadata (duration, counts) lives in the step's own collapsed-visible
  // header, never inside the disclosure — it must still read after opening.
  await expect(header).toContainText("4.4s");
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 1280]) {
    test(`file diff has one collapsible header at ${width}px in ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      // A REAL edit — one changed line inside an otherwise unchanged file —
      // so the diff renders SPLIT (`isCreation` forces unified whenever
      // `old_text` is empty, which a from-scratch write always is).
      await routeSingleEdit(page, [
        edit(
          "/w/src/api/health.py",
          "src/api/health.py",
          "def health() -> dict[str, str]:\n    return {'status': 'pending'}\n",
          "def health() -> dict[str, str]:\n    return {'status': 'ok'}\n",
          "edit-diff-fixture",
        ),
      ]);
      await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
      await page.getByRole("tab", { name: "Transcript", exact: true }).click();
      const group = page.getByTestId("tool-call-group").first();
      await expect(group).toBeVisible();
      await group.getByRole("button").first().click();
      const step = group.locator('[data-tool-use-id="edit-diff-fixture"]');
      const header = step.getByRole("button").first();
      await expect(step).toBeVisible();
      await expect(header).toHaveAttribute("aria-expanded", "false");
      await expect(step.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(0);
      await expect(header).toContainText("health.py");
      await expect(header).toContainText("+1");
      await expect(header).toContainText("−1");

      await header.click();
      await expect(header).toHaveAttribute("aria-expanded", "true");
      // One unchanged line, one changed line → one normal pair, one del/add pair.
      await expect(step.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(2);
      await expect(step.locator('[data-slot="diff-viewer-split-left"][data-type="del"]')).toContainText("return {'status': 'pending'}");
      await expect(step.locator('[data-slot="diff-viewer-split-right"][data-type="add"]').last()).toContainText("return {'status': 'ok'}");
      const diffBody = step.getByTestId("file-edit-diff");
      await expect(diffBody).toHaveAttribute("data-diff-view", "split");
      const body = step.getByRole("region", { name: "Changes to src/api/health.py" });
      await expect(body).toContainText("return {'status': 'ok'}");
      const geometry = await body.evaluate((element) => {
        const card = element.closest('[data-testid="file-edit-expanded"]')!;
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
      await expect(step.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(0);
      await page.keyboard.press("Space");
      await expect(header).toHaveAttribute("aria-expanded", "true");
      await expect(step.locator('[data-slot="diff-viewer-split-line"]')).toHaveCount(2);
      await step.screenshot({ path: test.info().outputPath(`file-diff-${theme}-${width}.png`) });
    });
  }
}
