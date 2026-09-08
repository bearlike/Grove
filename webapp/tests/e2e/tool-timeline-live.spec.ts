import { expect, test } from "@playwright/test";
import type { DigestEntryView, SessionDetailView, SessionTurnView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

function tool(id: string, status: "running" | "ok"): DigestEntryView {
  return {
    role: "tool", text: `Bash ${id}`, question: null, file_edit: null, todo: null,
    tool: { name: "Bash", input: { command: `echo ${id}` }, tool_use_id: id, status,
      duration_ms: status === "running" ? null : 120,
      result: status === "running" ? null : `${id} complete` },
  };
}

function run(entries: DigestEntryView[]): SessionTurnView {
  return { user_text: "Run the checks", started_at: null, entries };
}

const text: DigestEntryView = {
  role: "assistant", text: "All checks passed.", question: null, file_edit: null, todo: null,
};

test("live groups survive idle gaps and manual toggles until a following message arrives", async ({ page }) => {
  test.setTimeout(300_000);
  let turns = [run([tool("history", "ok"), text]), run([tool("first", "running")])];
  let generation = 0;
  let delivered = -1;
  await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
  // No stream means the production turns backstop refetches. Never reload:
  // a reload remounts state and cannot test an open/close transition.
  await page.route("**/api/grove/events", route => route.abort());
  await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
    const payload: SessionDetailView = await (await route.fetch()).json();
    await route.fulfill({ json: { ...payload, turns, total_turns: turns.length, first_turn_index: 0, incremental: false } });
    delivered = generation;
  });
  await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
  const groups = page.getByTestId("tool-call-group");
  await expect(groups).toHaveCount(2);
  await expect(groups.first().getByRole("button").first()).toHaveAttribute("aria-expanded", "false");
  const group = groups.nth(1);
  const trigger = group.getByRole("button").first();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await group.evaluate(element => { element.setAttribute("data-test-identity", "original"); });

  async function update(entries: DigestEntryView[]): Promise<void> {
    turns = [turns[0], run(entries)];
    generation++;
    await expect.poll(() => delivered, { timeout: 40_000 }).toBe(generation);
    await expect(group).toHaveAttribute("data-test-identity", "original");
  }

  await update([tool("first", "ok")]);
  await expect(trigger).not.toContainText("running");
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await update([tool("first", "ok"), tool("second", "running")]);
  await expect(group.locator('[data-tool-use-id="second"]')).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await trigger.click();
  await update([tool("first", "ok"), tool("second", "ok")]);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await update([tool("first", "ok"), tool("second", "ok"), tool("third", "running")]);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await update([tool("first", "ok"), tool("second", "ok"), tool("third", "ok")]);
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await update([tool("first", "ok"), tool("second", "ok"), tool("third", "ok"), text]);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
});
