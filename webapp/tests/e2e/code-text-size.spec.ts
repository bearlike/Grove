import { expect, test } from "@playwright/test";
import type { SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

for (const theme of ["light", "dark"]) {
  test(`${theme} Markdown, command, output and diff code share one type step`, async ({ page }) => {
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Inspect the code", started_at: null, entries: [
        { role: "assistant", text: "```python\nprint('hello')\n```", question: null, file_edit: null, todo: null },
        { role: "tool", text: "Bash python check.py", question: null, file_edit: null, todo: null,
          tool: { name: "Bash", input: { command: "python check.py" }, tool_use_id: "command", status: "ok", duration_ms: 20, result: "hello\n", body: "inline" } },
        { role: "file_edit", text: "Edit check.py", question: null, todo: null,
          tool: { name: "Edit", input: { file_path: "/w/check.py" }, tool_use_id: "edit", status: "ok", duration_ms: 10, result: "Applied", body: "inline" },
          file_edit: { path: "/w/check.py", display_path: "check.py", old_text: "print('old')\n", new_text: "print('hello')\n" } },
      ] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();
    await expect(page.locator(".aui-md-pre")).toBeVisible();
    const group = page.getByTestId("tool-call-group");
    await group.getByRole("button").first().click();
    for (const id of ["command", "edit"]) {
      await group.locator(`[data-tool-use-id="${id}"]`).getByRole("button").first().click();
    }
    await expect(page.getByTestId("file-edit-diff")).toBeVisible();
    const selectors = [".aui-md-pre", '[data-testid="tool-call-request"] pre', '[data-testid="tool-call-response"] pre', '[data-testid="file-edit-diff"]'];
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const sizes = await page.evaluate(selectors => selectors.map(selector => {
        const node = document.querySelector(selector)!;
        const css = getComputedStyle(node);
        return { size: css.fontSize, line: css.lineHeight };
      }), selectors);
      expect(new Set(sizes.map(s => s.size)).size).toBe(1);
      expect(sizes[0].line).toBe(sizes[3].line);
    }
    await group.screenshot({ path: test.info().outputPath(`code-text-${theme}.png`) });
  });
}
