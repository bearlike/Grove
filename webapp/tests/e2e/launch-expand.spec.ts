import { expect, test, type Locator } from "@playwright/test";
import { barInset } from "./sharp-surface-probe";

/**
 * A click that lands before React attaches its handler is silently dropped —
 * the DOM looks identical either way, so nothing about the failure names the
 * cause. `onboarding.spec.ts`'s `openTour` documents and fixes the same trap.
 * This is the FIRST interactive click on a freshly navigated landing page in
 * several of this file's tests; a typed `.fill()` or a resolved-value read
 * elsewhere in the file happens to force hydration first, which is why only
 * the true first click ever raced (observed on two different tests across
 * two runs, never the same one twice — a hydration-timing race, not a
 * per-test defect). Retry the click itself rather than waiting longer before
 * it, so a slow hydration costs nothing on the common case.
 */
async function clickUntil(trigger: Locator, ready: () => Promise<void>): Promise<void> {
  await expect(async () => {
    await trigger.click();
    await ready();
  }).toPass({ timeout: 30_000 });
}

/**
 * The expanded task brief.
 *
 * Only a browser can prove this one: the whole contract is that the composer is
 * MOVED into a dialog rather than copied, and "there is exactly one editor" is
 * a statement about the live DOM and the accessibility tree, not about markup a
 * server rendered. The unit suite is SSR-only (node environment, no DOM), so it
 * structurally cannot see a dialog that opens on click.
 */
test.describe("launch composer expand", () => {
  // The first-visit tour auto-opens over the landing composer and its mask
  // intercepts every click on the row it walks — `launch-expand`, the Agent
  // combobox, `launch-attach` all sit inside its steps. Every other landing
  // spec suppresses it before the first navigation; this file's tests each
  // reproducibly hung 90s on a masked click without it.
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("grove.onboarding.seen", "true"));
  });

  test("moves the one draft into the dialog and back", async ({ page }) => {
    await page.goto("/");

    const inline = page.getByTestId("launch-input");
    await expect(inline).toBeVisible({ timeout: 60_000 });

    const brief = "First line of the brief.\nSecond line, which is why this needs room.";
    await inline.fill(brief);
    const assertSendGeometry = async () => {
      const send = await page.locator('[data-slot="composer-send"]').boundingBox();
      const shell = await page.locator('[data-slot="composer-bar"]').boundingBox();
      expect(send!.width).toBeCloseTo(28, 0);
      expect(send!.height).toBeCloseTo(28, 0);
      // Send sits IN the bar's corner: its own padding plus its border, the
      // same on both edges — the vendored geometry, not a Grove constant.
      const inset = await barInset(page);
      expect(shell!.x + shell!.width - send!.x - send!.width).toBeCloseTo(inset, 0);
      expect(shell!.y + shell!.height - send!.y - send!.height).toBeCloseTo(inset, 0);
    };
    await assertSendGeometry();

    await page.getByTestId("launch-expand").click();
    await expect(page.getByTestId("launch-expanded")).toBeVisible();
    await page.getByTestId("launch-expanded").evaluate(async (element) => {
      await Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
    });

    // One editor, not two. A clone would leave the inline textarea mounted and
    // give one draft two tab stops and two accessible names.
    const editors = page.getByLabel("Task brief");
    await expect(editors).toHaveCount(1);
    await expect(editors).toHaveValue(brief);
    await assertSendGeometry();

    // Typing in the expanded editor is the same draft, not a fork of it.
    await editors.fill(`${brief}\nThird line, typed while expanded.`);
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("launch-expanded")).toBeHidden();
    await expect(page.getByTestId("launch-input")).toHaveValue(
      `${brief}\nThird line, typed while expanded.`,
    );
  });

  test("rejects an invalid custom model before launch", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("launch-controls")).toBeVisible({ timeout: 60_000 });

    const controls = page.getByTestId("launch-controls");
    await controls.getByRole("combobox", { name: "Agent" }).click();
    await page.getByText("Claude Code (default)", { exact: true }).click();

    // Scoped to the PAGE, not to the shelf: the model pill moved into the
    // composer's own toolbar beside send, because it configures the message's
    // agent rather than the workspace the shelf describes.
    await page.getByTestId("launch-input").fill("Use the custom model");
    const modelPicker = page.getByRole("combobox", { name: "Model", exact: true });
    await expect(modelPicker).toBeEnabled({ timeout: 60_000 });
    await modelPicker.click();
    await page.getByText("Custom…", { exact: true }).click();

    const customModel = page.getByTestId("launch-custom-model");
    await expect(customModel).toBeVisible();
    await expect(page.getByTestId("launch-custom-model-error")).toHaveText(
      "Enter a custom model id.",
    );

    const send = page.getByRole("button", { name: "Send message" });
    await expect(send).toBeDisabled();

    await customModel.fill("anthropic/claude-opus-5.1:beta_test");
    await expect(page.getByTestId("launch-custom-model-error")).toBeHidden();
    await expect(send).toBeEnabled();

    await customModel.fill("invalid model");
    await expect(page.getByTestId("launch-custom-model-error")).toContainText("ASCII letters");
    await expect(send).toBeDisabled();
  });

  test("returns focus to the restored inline editor, not to the body", async ({ page }) => {
    // Radix restores focus to the trigger, and the trigger is inside the
    // composer that gets unmounted — so without an explicit handoff focus falls
    // to the body and the keyboard user loses their place.
    await page.goto("/");
    await expect(page.getByTestId("launch-input")).toBeVisible({ timeout: 60_000 });

    await clickUntil(page.getByTestId("launch-expand"), () =>
      expect(page.getByTestId("launch-expanded")).toBeVisible({ timeout: 2_000 }),
    );
    const editorCountDuringClose = await page.getByTestId("launch-expanded").evaluate(async (dialog) => {
      (dialog.querySelector('[data-slot="dialog-close"]') as HTMLButtonElement).click();
      await new Promise(requestAnimationFrame);
      return document.querySelectorAll('textarea[aria-label="Task brief"]').length;
    });
    expect(editorCountDuringClose).toBe(1);

    await expect(page.getByTestId("launch-expanded")).toBeHidden();
    await expect(page.getByTestId("launch-input")).toBeFocused();
  });

  test("closes the project menu when the model menu opens, across the split", async ({ page }) => {
    // The two halves of one composer now live in different parents — the model
    // pill in the toolbar, the rest on the shelf below the bar — and they are
    // only one control group because `LaunchPillGroup` encloses both. If that
    // ever becomes two groups, each keeps its own `openKind`, both menus stay
    // open at once and the second one covers the first. Nothing throws; the
    // control underneath simply cannot be clicked.
    //
    // Browser-only by construction: the defect is two popovers open
    // SIMULTANEOUSLY, which is a statement about live DOM after two clicks.
    await page.goto("/");
    const shelf = page.getByTestId("launch-controls");
    await expect(shelf).toBeVisible({ timeout: 60_000 });

    const agent = shelf.getByRole("combobox", { name: "Agent", exact: true });
    await clickUntil(agent, () => expect(agent).toHaveAttribute("aria-expanded", "true", { timeout: 2_000 }));
    await page.getByText("Claude Code (default)", { exact: true }).click();
    const project = shelf.getByRole("combobox", { name: "Project" });
    await clickUntil(project, () => expect(project).toHaveAttribute("aria-expanded", "true", { timeout: 2_000 }));

    const model = page.getByRole("combobox", { name: "Model", exact: true });
    await model.click();
    await expect(model).toHaveAttribute("aria-expanded", "true");
    await expect(project).toHaveAttribute("aria-expanded", "false");
  });

  test("stages a pasted file as one chip above the editor", async ({ page }) => {
    // Chips render ABOVE the brief now. Asserted geometrically rather than by
    // source order, because the thing that broke before was the visual
    // relationship and not the markup: a chip row that paints below the
    // textarea pushes the send corner and reads as a footer.
    await page.goto("/");
    const input = page.getByTestId("launch-input");
    await expect(input).toBeVisible({ timeout: 60_000 });

    // The attach button renders `disabled` until its handler is wired, and
    // the landing composer forwards the click to a hidden `<input
    // type="file">` sibling — a click landing before both are ready opens no
    // chooser at all and spends the whole 90s test budget on an event that
    // never arrives (same race `shared-composer.spec.ts`'s `pickFile`
    // documents and fixes). Drive the input directly instead of the chooser.
    const attach = page.getByTestId("launch-attach");
    await expect(attach).toBeEnabled({ timeout: 60_000 });
    await page.locator('input[type="file"]').last().setInputFiles({
      name: "brief-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("a staged file"),
    });

    const chips = page.getByTestId("launch-attachments");
    await expect(chips).toBeVisible();
    await expect(chips).toContainText("brief-notes.txt");

    const chipBox = await chips.boundingBox();
    const inputBox = await input.boundingBox();
    expect(chipBox!.y + chipBox!.height).toBeLessThanOrEqual(inputBox!.y + 1);

    // Removing takes the chip with it, and leaves the draft alone.
    await page.getByRole("button", { name: "Remove brief-notes.txt" }).click();
    await expect(chips).toBeHidden();
  });

  test("keeps the control selections made before expanding", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("launch-controls")).toBeVisible({ timeout: 60_000 });

    // Read the SELECTED VALUES, never the row's rendered text. The dialog is a
    // different width, so a pill legitimately truncates its label differently
    // in the two places — asserting the text would pin layout and fail on a
    // difference that is not a defect. What must survive is the choice.
    const project = page.getByTestId("launch-controls").getByRole("combobox", { name: "Project" });
    // The pill paints its own label until `/defaults` and the fleet snapshot
    // land. Capturing before then compares a placeholder against a real value
    // and reports a resolved default as a lost selection.
    await expect(project).not.toHaveText("Project");
    const before = await project.textContent();

    await page.getByTestId("launch-expand").click();
    await expect(page.getByTestId("launch-expanded")).toBeVisible();

    await expect(
      page.getByTestId("launch-controls").getByRole("combobox", { name: "Project" }),
    ).toHaveText(before ?? "");
  });
});
