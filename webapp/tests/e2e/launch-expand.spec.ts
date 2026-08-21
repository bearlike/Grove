import { expect, test } from "@playwright/test";

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
  test("moves the one draft into the dialog and back", async ({ page }) => {
    await page.goto("/");

    const inline = page.getByTestId("launch-input");
    await expect(inline).toBeVisible({ timeout: 60_000 });

    const brief = "First line of the brief.\nSecond line, which is why this needs room.";
    await inline.fill(brief);

    await page.getByTestId("launch-expand").click();
    await expect(page.getByTestId("launch-expanded")).toBeVisible();

    // One editor, not two. A clone would leave the inline textarea mounted and
    // give one draft two tab stops and two accessible names.
    const editors = page.getByLabel("Task brief");
    await expect(editors).toHaveCount(1);
    await expect(editors).toHaveValue(brief);

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

    const modelPicker = controls.getByRole("combobox", { name: "Model" });
    await expect(modelPicker).toBeEnabled({ timeout: 60_000 });
    await modelPicker.click();
    await page.getByText("Custom…", { exact: true }).click();

    const customModel = page.getByTestId("launch-custom-model");
    await expect(customModel).toBeVisible();
    await expect(page.getByTestId("launch-custom-model-error")).toHaveText(
      "Enter a custom model id.",
    );

    const send = page.getByRole("button", { name: "Send message" });
    await page.getByTestId("launch-input").fill("Use the custom model");
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

    await page.getByTestId("launch-expand").click();
    await expect(page.getByTestId("launch-expanded")).toBeVisible();
    await page.keyboard.press("Escape");

    await expect(page.getByTestId("launch-expanded")).toBeHidden();
    await expect(page.getByTestId("launch-input")).toBeFocused();
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
