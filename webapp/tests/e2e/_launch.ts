import { expect, type Page } from "@playwright/test";

const CONTENT = '[data-slot="model-selector-content"]';
const ITEM = '[data-slot="model-selector-item"]';

/**
 * One picker row by its visible label. A row also carries its brand mark, an
 * SVG whose `<title>` repeats the tool's name, so a bare text match finds two
 * nodes for "Claude". The row is the click target, so match the row.
 */
function row(page: Page, label: string) {
  return page
    .locator(CONTENT)
    .locator(ITEM)
    .filter({ has: page.locator("span", { hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) });
}

/**
 * Choose a roster entry the way the creation flow asks for it: the TOOL first,
 * then, only when that tool has more than one configuration, the profile.
 *
 * The fixture roster has two Claude Code entries, so picking Claude reveals
 * the Profile pill; a tool with one entry never shows it, and the profile step
 * is skipped. One helper so every spec drives the flow identically.
 */
export async function chooseProfile(page: Page, tool: string, profile: string): Promise<void> {
  const shelf = page.getByTestId("launch-controls");
  const agent = shelf.getByRole("combobox", { name: "Agent", exact: true });
  await expect(agent).toBeEnabled({ timeout: 60_000 });
  await agent.click();
  await row(page, tool).click();
  await expect(agent).toContainText(tool);

  const pill = shelf.getByRole("combobox", { name: "Profile", exact: true });
  if ((await pill.count()) === 0) return;
  await pill.click();
  await row(page, profile).click();
  await expect(pill).toContainText(profile);
}
