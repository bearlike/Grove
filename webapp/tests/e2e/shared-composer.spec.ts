import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";
import { openSharpSurface } from "./sharp-surface-probe";

/**
 * THE SHARED NATIVE COMPOSER, on both surfaces that mount one: a `composer-bar`
 * holding attachment chips ABOVE the editor and a toolbar below it, with the
 * launch surface's configuration pills moved OUT of that toolbar into an inset
 * `composer-shelf` underneath. Containment is a statement about boxes and every
 * keyboard defect here is a statement about which element received a key, so
 * both need a browser.
 *
 * Draft restore, refusal wording, send geometry and the expand dialog's
 * one-editor rule have owners already (`composer-draft-persistence`,
 * `launch-expand`, `workspace-composer`) and are not re-asserted. Beyond
 * `composer-shelf` every hook is a `data-slot` or an accessible name.
 *
 * THE ATTACHMENT CARD LIVES HERE BECAUSE IT IS A SET OF RELATIONSHIPS, and
 * every one of them is invisible to a static render: a gradient only exists
 * once a stylesheet cascades, "lighter than the composer" is a comparison
 * between two computed fills, "the same card staged and sent" needs both
 * surfaces mounted at once, and a row that wraps off the right edge of a phone
 * is `toBeVisible` the whole time. The card's own markup and its theme's
 * SOURCE are pinned in `tests/unit/attachment-file.test.tsx` and
 * `tests/unit/attachment-card-theme.test.ts`; nothing is asserted twice, and
 * no exact colour is asserted anywhere.
 */

test("composer focus uses one inset line rather than an outer frame", async ({ page }) => {
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Task brief", exact: true });
  const bar = page.getByTestId("launch-composer").locator('[data-slot="composer-bar"]');
  const restingShadow = await bar.evaluate(el => getComputedStyle(el).boxShadow);
  await input.click();
  await expect(bar).toHaveCSS("outline-width", "1px");
  await expect(bar).toHaveCSS("outline-offset", "-1px");
  await expect(bar).toHaveCSS("outline-style", "solid");
  await expect(bar).toHaveCSS("box-shadow", restingShadow);
});

const WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;
/** The fixture's FIRST agent (`shell`) has no catalog, so Model needs this one. */
const AGENT_WITH_MODELS = "Claude Code (default)";
const CONTENT = '[data-slot="model-selector-content"]';

function landing(page: Page) {
  const composer = page.getByTestId("launch-composer");
  const bar = composer.locator('[data-slot="composer-bar"]');
  return {
    bar,
    input: page.getByRole("textbox", { name: "Task brief", exact: true }),
    attachments: bar.locator('[data-slot="composer-attachments"]'),
    chips: bar.locator('[data-slot="file-root"]'),
    toolbar: bar.locator('[data-slot="composer-toolbar"]'),
    shelf: composer.locator(".composer-shelf"),
  };
}

async function chooseAgent(page: Page): Promise<void> {
  const agent = landing(page).shelf.getByRole("combobox", { name: "Agent", exact: true });
  await expect(agent).toBeEnabled({ timeout: 60_000 });
  await agent.click();
  await page.locator(CONTENT).getByText(AGENT_WITH_MODELS, { exact: true }).click();
  await expect(agent).toContainText(AGENT_WITH_MODELS);
}

async function pickFile(page: Page, trigger: Locator, name: string, body: string): Promise<void> {
  // The button renders `disabled` until its handler is wired, and the landing
  // composer's handler forwards to a hidden `<input type="file">` through a
  // ref. A click landing before either is ready opens no chooser at all, which
  // surfaces as an unexplained 90s timeout rather than as a mount race.
  //
  // The two composers mount that input differently — the landing one renders it
  // as the button's own sibling, the workspace one gets it from assistant-ui's
  // AddAttachment primitive — so the input is located page-wide rather than
  // relative to the trigger, and the chooser stays the fallback for any mount
  // that has no input to drive.
  await expect(trigger).toBeEnabled({ timeout: 60_000 });
  const picker = page.locator('input[type="file"]').last();
  if (await picker.count()) {
    await picker.setInputFiles({ name, mimeType: "text/plain", buffer: Buffer.from(body) });
    return;
  }
  const chooser = page.waitForEvent("filechooser");
  await trigger.click();
  await (await chooser).setFiles({ name, mimeType: "text/plain", buffer: Buffer.from(body) });
}

/** A file-bearing paste, which both surfaces must route to their ONE staging path. */
async function pasteFile(target: Locator, name: string, body: string): Promise<void> {
  await target.evaluate((element, file) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([file.body], file.name, { type: "text/plain" }));
    element.dispatchEvent(
      new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: transfer }),
    );
  }, { name, body });
}

async function openFromKeyboard(page: Page, trigger: Locator): Promise<Locator> {
  await trigger.focus();
  await trigger.press("ArrowDown");
  const content = page.locator(CONTENT);
  await expect(content).toBeVisible();
  return content;
}

/**
 * Walk the highlight to ONE NAMED option, then commit it — a bare ArrowDown +
 * Enter takes whatever is next, which on the Agent list can be `shell`, whose
 * empty catalog then disables Model and leaves the loop asserting nothing.
 * `opensPanel` names an option that only REVEALS A FIELD, and so keeps its menu.
 */
async function commitByKeyboard(
  page: Page, content: Locator, label: string, opensPanel: boolean,
): Promise<void> {
  const active = content.locator('[data-slot="model-selector-item"][data-selected="true"]');
  for (let step = 0; step < 12; step += 1) {
    if ((await active.count()) === 1 && (await active.innerText()).includes(label)) break;
    await page.keyboard.press("ArrowDown");
  }
  await expect(active, `keyboard never reached "${label}"`).toContainText(label);
  await page.keyboard.press("Enter");
  if (opensPanel) {
    await expect(content, `"${label}" reveals a field and must keep its menu`).toBeVisible();
    await page.keyboard.press("Escape");
  }
  await expect(content).toBeHidden();
}

const centre = async (locator: Locator): Promise<number> => {
  const box = (await locator.boundingBox())!;
  return box.x + box.width / 2;
};

const below = async (upper: Locator, lower: Locator): Promise<void> => {
  const above = (await upper.boundingBox())!;
  expect(above.y + above.height).toBeLessThanOrEqual((await lower.boundingBox())!.y + 1);
};

async function attachShot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await testInfo.attach(name, { body: await page.screenshot(), contentType: "image/png" });
}

/**
 * ON SCREEN, not merely `toBeVisible`.
 *
 * A row inside a wrapping list can be perfectly "visible" to Playwright — non-
 * empty box, no `display:none`, no zero opacity — while sitting past the right
 * edge of the panel that holds it, which is exactly what a file list that
 * refuses to wrap does. The box against the viewport is the only thing that
 * distinguishes the two.
 */
async function onScreen(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  const box = (await locator.boundingBox())!;
  const view = locator.page().viewportSize()!;
  expect(box.x, "row starts left of the viewport").toBeGreaterThanOrEqual(-1);
  expect(box.x + box.width, "row runs past the right edge").toBeLessThanOrEqual(view.width + 1);
  expect(box.y + box.height).toBeGreaterThan(0);
}

/** One card's measured surface: what it paints and what corner it paints it on. */
async function cardSurface(row: Locator): Promise<{ image: string; radius: string }> {
  return row.evaluate((element) => {
    const style = getComputedStyle(element);
    return { image: style.backgroundImage, radius: style.borderTopLeftRadius };
  });
}

/** Let the browser convert CSS colors to sRGB before comparing luminance. */
async function surfaceLuminances(locator: Locator): Promise<number[]> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const colors = style.backgroundImage === "none"
      ? [style.backgroundColor]
      : style.backgroundImage.match(/(?:oklch|oklab|rgba?|color|lab|lch)\([^)]*\)/g);
    if (!colors?.length) throw new Error("No resolved surface colors");
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    return colors.map((color) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      if (pixel[3] !== 255) throw new Error("Surface must be opaque");
      const linear = Array.from(pixel).slice(0, 3).map((channel) => {
        const unit = channel / 255;
        return unit <= 0.04045 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
    });
  });
}

// The first-visit tour is its own e2e contract. It would otherwise overlay the
// launch composer after its 600ms delay and turn these component assertions into
// unrelated intercepted clicks.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
});

test.describe("the shared native composer", () => {
  test("landing writes in one native bar with its configuration shelf outside it", async ({ page }, testInfo) => {
    await page.goto("/");
    const { bar, input, toolbar, shelf } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    // Nested, the shelf would look almost identical and put configuration
    // back inside the writing surface.
    await expect(bar).toHaveCount(1);
    await expect(bar.locator(".composer-shelf")).toHaveCount(0);
    await expect(shelf).toHaveCount(1);
    await below(bar, shelf);

    // Both halves: Model leaving and Project staying are different failures.
    for (const name of ["Project", "Agent", "Branch and placement", "Runtime"]) {
      await expect(shelf.getByRole("combobox", { name, exact: true })).toHaveCount(1);
      await expect(toolbar.getByRole("combobox", { name, exact: true })).toHaveCount(0);
    }
    await expect(toolbar.getByRole("combobox", { name: "Model", exact: true })).toHaveCount(1);

    // Attach left, model · expand · send right. Sides are the contract.
    const toolbarBox = (await toolbar.boundingBox())!;
    const middle = toolbarBox.x + toolbarBox.width / 2;
    const model = await centre(toolbar.getByRole("combobox", { name: "Model", exact: true }));
    const expand = await centre(page.getByTestId("launch-expand"));
    expect(await centre(bar.locator('[data-slot="composer-attach"]'))).toBeLessThan(middle);
    expect(model).toBeGreaterThan(middle);
    expect(model).toBeLessThan(expand);
    expect(expand).toBeLessThan(await centre(bar.locator('[data-slot="composer-send"]')));

    await attachShot(page, testInfo, "landing-light.png");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await attachShot(page, testInfo, "landing-dark.png");
  });

  test("a picked file and a pasted file each stage once, as a chip above the editor", async ({ page }) => {
    await page.goto("/");
    const { bar, input, attachments, chips } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    await pickFile(page, bar.locator('[data-slot="composer-attach"]'), "brief.txt", "picked");
    await expect(chips).toHaveCount(1);
    // Paste is the SAME staging path: a competing handler stages twice.
    await pasteFile(input, "pasted.txt", "pasted once");
    await expect(chips).toHaveCount(2);
    await expect(chips.filter({ hasText: "pasted.txt" })).toHaveCount(1);
    await below(attachments, input);
  });

  test("removing one of two identically named files removes the one that was clicked", async ({ page }) => {
    await page.goto("/");
    const { bar, input, chips } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    // One name, DIFFERENT sizes: a removal keyed on the filename fails
    // only here, since the size is all that tells the survivor apart.
    const attach = bar.locator('[data-slot="composer-attach"]');
    await pickFile(page, attach, "notes.txt", "x".repeat(1024));
    await pickFile(page, attach, "notes.txt", "y".repeat(8192));
    await expect(chips).toHaveCount(2);
    const survivor = await chips.first().innerText();
    expect(survivor).not.toEqual(await chips.nth(1).innerText());

    await chips.nth(1).getByRole("button", { name: /^Remove\b/ }).click();
    await expect(chips).toHaveCount(1);
    await expect(chips.first()).toHaveText(survivor, { useInnerText: true });
  });

  test("plain Enter sends the brief and Shift+Enter keeps writing", async ({ page }) => {
    let creates = 0;
    await page.route("**/api/grove/workspaces", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      creates += 1;
      await route.fulfill({ json: FIXTURE_WORKSPACES[0] });
    });
    await page.goto("/");
    const { input } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    await chooseAgent(page);

    await input.fill("First line");
    await input.press("Shift+Enter");
    await input.pressSequentially("second line");
    await expect(input).toHaveValue("First line\nsecond line");
    expect(creates).toBe(0);

    await input.press("Enter");
    await expect.poll(() => creates).toBe(1);
    await page.waitForURL(`**/w/${WORKSPACE_ID}`);
  });

  test("every picker names its own entity and commits a named choice from the keyboard", async ({ page }) => {
    await page.goto("/");
    const { bar, input, shelf } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    // ORDER IS LOAD-BEARING: the controls cascade, so a project clears the
    // agent and an agent the model; any other order re-empties an asserted
    // field. `Work in place` discloses a field, so it keeps its menu open.
    const pickers: readonly [string, RegExp, string, Locator, boolean][] = [
      ["Project", /project/i, "Agents", shelf, false],
      ["Agent", /agent/i, AGENT_WITH_MODELS, shelf, false],
      ["Model", /model/i, "Opus", bar, false],
      ["Branch and placement", /branch/i, "Work in place", shelf, true],
      ["Runtime", /runtime/i, "Container", shelf, false],
    ];
    for (const [name, noun, choice, scope, opensPanel] of pickers) {
      const trigger = scope.getByRole("combobox", { name, exact: true });
      await expect(trigger).toBeEnabled({ timeout: 60_000 });
      const content = await openFromKeyboard(page, trigger);
      // The vendored anchor is named "Model": right upstream, wrong noun here.
      const search = content.locator("input[cmdk-input]");
      await expect(search).toHaveCount(1);
      const announced = await search.evaluate((element: HTMLInputElement) =>
        `${element.getAttribute("aria-label") ?? ""} ${element.placeholder ?? ""}`);
      expect(announced, `${name}'s search input announces itself`).toMatch(noun);
      await commitByKeyboard(page, content, choice, opensPanel);
      await expect(trigger, `${name} lost its keyboard choice`).toContainText(choice);
    }
  });

  test("branch text fields keep editing keys out of the command list", async ({ page }) => {
    await page.goto("/");
    const { input, shelf } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    // ONE open, not two: `new` discloses its field inside this popover, so a
    // second trigger click would dismiss the panel.
    const branch = shelf.getByRole("combobox", { name: "Branch and placement", exact: true });
    await branch.click();
    const content = page.locator(CONTENT);
    await content.getByText("New branch…", { exact: true }).click();
    await expect(content).toBeVisible();
    const name = content.getByLabel("Branch name", { exact: true });
    await expect(name).toBeVisible();
    await name.fill("feature/payment-v2");

    // Each is an EDITING key in a field and a SELECTION key to cmdk. Leaked,
    // Enter commits a row mid-name and arrows move the highlight, not the caret.
    for (const key of ["Home", "End", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"]) {
      await name.press(key);
      await expect(content, `${key} closed the branch popover`).toBeVisible();
    }
    await expect(name).toHaveValue("feature/payment-v2");
    await expect(branch).toContainText("New branch");

    // Escape is deliberately NOT captured: Radix dismisses on the document.
    await name.press("Escape");
    await expect(content).toBeHidden();
    await expect(branch).toContainText("New branch");
  });

  test("a custom model id is typed inside the model popover", async ({ page }) => {
    await page.goto("/");
    const { bar, input } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    await chooseAgent(page);
    await input.fill("Use a model the catalog does not list");

    await bar.getByRole("combobox", { name: "Model", exact: true }).click();
    const content = page.locator(CONTENT);
    await content.getByText("Custom…", { exact: true }).click();
    // `Custom…` is half an answer, so it keeps its menu open.
    await expect(content).toBeVisible();
    await expect(content.getByTestId("launch-custom-model")).toBeVisible();

    const send = page.getByRole("button", { name: "Send message", exact: true });
    await expect(page.getByTestId("launch-custom-model-error")).toHaveText("Enter a custom model id.");
    await expect(send).toBeDisabled();
    await content.getByTestId("launch-custom-model").fill("anthropic/claude-opus-5.1:beta_test");
    await expect(page.getByTestId("launch-custom-model-error")).toBeHidden();
    await expect(send).toBeEnabled();
  });

  test("the workspace composer reuses the same bar and chip slots", async ({ page }, testInfo) => {
    await openSharpSurface(page, "info", 300, "dark");
    const bar = page.locator('[data-slot="composer-bar"]');
    const input = page.getByRole("textbox", { name: "Message input", exact: true });
    await expect(bar).toHaveCount(1);
    await expect(input).toBeVisible();
    // Shared PRESENTATION, not plumbing: this uploads and sends ids, landing
    // carries bytes. No shelf — a session cannot change where it already runs.
    await expect(page.locator(".composer-shelf")).toHaveCount(0);
    await pickFile(page, page.getByRole("button", { name: /add attachment/i }), "trace.log", "run");
    const chips = bar.locator('[data-slot="file-root"]');
    await expect(chips).toHaveCount(1);
    await expect(chips.first()).toContainText("trace.log");
    await below(bar.locator('[data-slot="composer-attachments"]'), input);

    const toolbar = bar.locator('[data-slot="composer-toolbar"]');
    await expect(toolbar.getByTestId("composer-model-trigger")).toBeVisible();
    await expect(page.getByTestId("workspace-composer-expand")).toBeVisible();
    await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeVisible();
    await attachShot(page, testInfo, "workspace-composer-dark.png");
  });

  test("a sent message's files sit above the bubble and survive its collapse, as the same File rows", async ({ page }, testInfo) => {
    // The fixture's first turn is a long brief with two attached files. The
    // rows must be the SAME vendored element the composer stages a file as —
    // one look before and after a send — and they must be visible while the
    // prompt beneath them is clamped, which a `file` content part inside the
    // bubble could never be.
    await openSharpSurface(page, "info", 300, "light");
    const message = page.locator('[data-slot="aui_user-message-root"]').first();
    const rows = message.locator('[data-slot="file-root"]');
    const bubble = message.getByTestId("user-message-collapse");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("openapi.yaml");
    await expect(rows.nth(0)).toContainText("2.0 KB");
    await expect(rows.nth(1)).toContainText("health-check.png");
    // A file nobody weighed says SO, in the same place the weighed one states
    // its size — a blank second line reads as a zero-byte file, and an absent
    // one makes the two rows different shapes.
    await expect(rows.nth(1).locator('[data-slot="file-size"]')).toHaveCount(0);
    await expect(rows.nth(1).locator('[data-slot="file-metadata"]')).toContainText("Size not recorded");
    await expect(rows.nth(0).locator('[data-slot="file-metadata"]')).toContainText("2.0 KB");
    await expect(rows.first()).toHaveAttribute("data-size", "sm");
    // A delivered file cannot be un-attached, so the transcript row carries no
    // removal control — the one prop the three surfaces may differ on.
    await expect(rows.first().getByRole("button", { name: /^Remove\b/ })).toHaveCount(0);

    await expect(bubble).toHaveAttribute("data-collapsed", "true");
    await expect(message.getByTestId("user-message-toggle")).toBeVisible();
    for (const row of [rows.nth(0), rows.nth(1)]) {
      await onScreen(row);
      await below(row, bubble);
    }

    // The composer stages a file as the identical element, at the identical size.
    await pickFile(page, page.getByRole("button", { name: /add attachment/i }), "trace.log", "run");
    const staged = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]');
    await expect(staged).toHaveCount(1);
    await expect(staged).toHaveAttribute("data-size", "sm");
    await expect(staged).toContainText("trace.log");
    await expect(staged).toContainText("3 B");
    // Composer and transcript carry the removal control differently and must
    // still be one card: same box, same paint.
    await expect(staged.getByRole("button", { name: /^Remove\b/ })).toHaveCount(1);
    await attachShot(page, testInfo, "sent-attachments-above-bubble.png");
  });

  test("the card is a gradient surface on the container corner, in both themes", async ({ page }) => {
    for (const theme of ["light", "dark"] as const) {
      await openSharpSurface(page, "info", 300, theme);
      const row = page.locator('[data-slot="aui_user-message-root"]')
        .first().locator('[data-slot="file-root"]').first();
      await expect(row).toBeVisible();
      const { image, radius } = await cardSurface(row);
      // NO EXACT COLOUR. A card that is flat paints `none` here, whatever its
      // fill; the gradient is the decision, and the channel values belong to
      // the theme file (`tests/unit/attachment-card-theme.test.ts` reads them).
      expect(image, `${theme} card paints no gradient`).toContain("linear-gradient(");

      // The container role (5.75px at the default scale), not the 2.3px inner
      // cell the row used to take — which is what made it read as a chip. The
      // composer bar is the documented 20%-softer 6.9px exception, so it is
      // intentionally not this card's geometry peer.
      expect(radius, `${theme} card corner`).toBe("5.75px");
    }
  });

  test("a dark card is LIGHTER than the composer it is staged in", async ({ page }) => {
    // Dark-mode elevation runs the other way round: the card separates by
    // being lighter, so a card that merely inherits the bar's fill vanishes
    // into it. A ratio, never a value — the tokens are free to be retuned.
    await openSharpSurface(page, "info", 300, "dark");
    await pickFile(page, page.getByRole("button", { name: /add attachment/i }), "trace.log", "run");
    const bar = page.locator('[data-slot="composer-bar"]');
    const card = bar.locator('[data-slot="file-root"]').first();
    await expect(card).toBeVisible();
    // Theme changes transition the bar's background. Compare only once its
    // paint has settled, not a light-to-dark intermediate frame.
    await expect.poll(async () => {
      const barY = Math.max(...await surfaceLuminances(bar));
      return Math.min(...await surfaceLuminances(card)) - barY;
    }, { message: "both gradient stops must clear the composer" }).toBeGreaterThan(0.002);
    const cardStops = await surfaceLuminances(card);
    expect(cardStops).toHaveLength(2);
    expect(Math.abs(cardStops[0]! - cardStops[1]!), "the gradient must not be flat").toBeGreaterThan(0.001);
  });

  test("one short file is the same card staged and sent", async ({ page }) => {
    // The whole reason the row is one module. A card that is one size in the
    // composer and another in the transcript is two components wearing one
    // name, and only a browser can see it — the unit suite renders each in
    // isolation, where nothing disagrees.
    await openSharpSurface(page, "info", 300, "light");
    const sent = page.locator('[data-slot="aui_user-message-root"]').first()
      .locator('[data-slot="file-root"]').filter({ hasText: "openapi.yaml" });
    await expect(sent).toHaveCount(1);
    await pickFile(page, page.getByRole("button", { name: /add attachment/i }), "openapi.yaml", "x".repeat(2048));
    const staged = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]');
    await expect(staged).toHaveCount(1);
    await expect(staged).toContainText("2.0 KB");

    // SAME FILE, SAME NAME, SAME BYTES — so any difference is the card's, not
    // the content's. The removal control the composer adds is the one allowed
    // difference, so height is asserted exactly and width within its glyph.
    const [a, b] = [(await sent.boundingBox())!, (await staged.boundingBox())!];
    expect(Math.abs(a.height - b.height), "same file, two heights").toBeLessThanOrEqual(1);
    expect(b.width).toBeGreaterThanOrEqual(a.width - 1);
    expect(b.width - a.width, "the remove control is the only width the staged card may add").toBeLessThan(48);

    // Typography follows: a filename that is one step bigger in the composer
    // is the same defect stated in type rather than in boxes.
    const size = (row: Locator) => row.locator('[data-slot="file-name"]')
      .evaluate((element) => getComputedStyle(element).fontSize);
    expect(await size(staged)).toBe(await size(sent));
  });

  test("a long filename fills its card without widening the page", async ({ page }) => {
    await openSharpSurface(page, "info", 300, "light");
    const name = "a-deeply-nested-generated-openapi-specification-for-the-health-endpoint.yaml";
    await pickFile(page, page.getByRole("button", { name: /add attachment/i }), name, "x".repeat(2048));
    const card = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]').first();
    await expect(card).toHaveCount(1);
    await onScreen(card);
    // The document may not grow sideways, and neither may the card outrun the
    // bar that holds it — `File.Root` is `inline-flex` and sizes to its name,
    // so the cap is the only thing making the name's own `truncate` bite.
    await expect.poll(async () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
    ).toBeLessThanOrEqual(1);
    const bar = (await page.locator('[data-slot="composer-bar"]').boundingBox())!;
    expect((await card.boundingBox())!.width).toBeLessThanOrEqual(bar.width + 1);

    // Truncated for the eye, WHOLE for a keyboard reader: `title` is a hover
    // affordance and reaches nobody holding no mouse, so the row's own
    // accessible name is what has to carry the rest.
    const label = await card.getAttribute("aria-label");
    expect(label, "the card's accessible name drops the full filename").toContain(name);
    const nameTrigger = card.locator('[data-slot="tooltip-trigger"]').first();
    await nameTrigger.focus();
    await expect(page.getByRole("tooltip")).toContainText(name);
  });

  test("every staged file stays on screen on a phone, not only the first", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const { bar, input, chips } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    const attach = bar.locator('[data-slot="composer-attach"]');
    // Four cards at this width cannot sit on one line, so a list that does not
    // wrap pushes three of them past the right edge — where each is still
    // `toBeVisible`, which is precisely why that assertion is not the test.
    for (const file of ["alpha.txt", "bravo.txt", "charlie.txt", "delta.txt"]) {
      await pickFile(page, attach, file, "x".repeat(2048));
    }
    await expect(chips).toHaveCount(4);
    for (let index = 0; index < 4; index += 1) await onScreen(chips.nth(index));
    await expect.poll(async () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
    ).toBeLessThanOrEqual(1);
    // Wrapped, not scrolled sideways inside its own row: more than one line.
    const tops = await chips.evaluateAll((rows) => [...new Set(rows.map((row) => Math.round(row.getBoundingClientRect().top)))]);
    expect(tops.length, "four cards on one line at 390px").toBeGreaterThan(1);
  });

  test("the composer holds its shape on a phone and under long configuration labels", async ({ page }) => {
    // Through the route the picker reads, so the width arrives as a real
    // host's would rather than by a style override.
    await page.route("**/api/grove/agents*", (route) =>
      route.fulfill({
        json: [{
          name: "Claude Code (via Synthetic Gateway, extended reasoning profile)",
          kind: "claude_code",
          description: "A deliberately long configuration label",
          models: ["opus", "sonnet"],
        }],
      }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const { bar, input, shelf, chips } = landing(page);
    await expect(input).toBeVisible({ timeout: 60_000 });
    await pickFile(page, bar.locator('[data-slot="composer-attach"]'), "phone-note.txt", "note");
    await expect(chips).toHaveCount(1);

    // No horizontal scroll: the shelf wraps or truncates, but neither it nor
    // a chip may widen the page. No exact pixels — containment is the contract.
    await expect.poll(async () =>
      page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
    ).toBeLessThanOrEqual(1);
    const barBox = (await bar.boundingBox())!;
    expect((await shelf.boundingBox())!.width).toBeLessThanOrEqual(barBox.width + 1);
    await below(bar, shelf);
    await below(chips.first(), input);

    const editor = (await input.boundingBox())!;
    const send = (await bar.locator('[data-slot="composer-send"]').boundingBox())!;
    expect(send.x).toBeGreaterThan(barBox.x + barBox.width / 2);
    expect(send.y).toBeGreaterThan(editor.y);
  });
});
