import { expect, test } from "@playwright/test";
import { dp } from "./density";
import { barInset, openSharpSurface } from "./sharp-surface-probe";

test("workspace composer moves one runtime draft, fills the dialog, and restores focus", async ({ page }) => {
  await openSharpSurface(page, "info", 300, "dark");
  const input = page.getByRole("textbox", { name: "Message input", exact: true });
  await input.fill("A synthetic draft that survives expansion");
  const before = await input.boundingBox();
  let streams = 0;
  page.on("request", request => { if (request.url().includes("/events")) streams++; });
  await page.getByRole("button", { name: "Expand composer", exact: true }).click();
  const dialog = page.getByTestId("workspace-composer-expanded");
  await expect(dialog).toBeVisible();
  await expect(input).toHaveCount(1);
  await expect(input).toHaveValue("A synthetic draft that survives expansion");
  expect((await input.boundingBox())!.height).toBeGreaterThan(before!.height + 200);
  // Dialog zoom/translate animations move bounding boxes between reads.
  await dialog.evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
  });
  const sendBox = await dialog.getByRole("button", { name: "Send message", exact: true }).boundingBox();
  // The shared composer body IS the vendored bar, so the slot is upstream's.
  // Scoped to the dialog because the inline composer renders the same slot.
  const shellBox = await dialog.locator('[data-slot="composer-bar"]').boundingBox();
  expect(sendBox!.width).toBeCloseTo(28, 0);
  expect(sendBox!.height).toBeCloseTo(28, 0);
  // Send sits IN the bar's corner: its own padding plus its border, the
      // same on both edges — the vendored geometry, not a Grove constant.
      const inset = await barInset(page);
      expect(shellBox!.x + shellBox!.width - sendBox!.x - sendBox!.width).toBeCloseTo(inset, 0);
  expect(shellBox!.y + shellBox!.height - sendBox!.y - sendBox!.height).toBeCloseTo(inset, 0);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(input).toHaveCount(1);
  await expect(input).toHaveValue("A synthetic draft that survives expansion");
  await expect(input).toBeFocused();
  expect(streams).toBe(0);
});

test("composer reports the model, delivers catalog choices, and does not claim a switch", async ({ page }) => {
  const reported = "provider/reported-model-with-a-very-long-name-not-in-the-catalog";
  await page.route("**/api/grove/workspaces/*/controls", route => route.fulfill({ json: {
    current_model: reported, models: ["catalog-model"], commands: [], skills: [], mcp_servers: [], permission_mode: "default",
  } }));
  const requests: unknown[] = [];
  await page.route("**/api/grove/workspaces/*/controls/model", async route => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({ status: 204 });
  });
  await openSharpSurface(page, "info", 300, "light");
  const trigger = page.getByTestId("composer-model-trigger");
  await expect(trigger).toHaveAttribute("title", `Reported current model: ${reported}`);
  await trigger.click();
  await expect(page.getByRole("dialog", { name: "Choose model" }).getByRole("combobox")).toBeVisible();
  await page.getByTestId("composer-model-item").click();
  expect(requests).toEqual([{ model: "catalog-model" }]);
  const pending = page.getByTestId("composer-model-pending");
  await expect(pending).toBeVisible();
  expect((await pending.boundingBox())!.width).toBeCloseTo(6, 0);
  expect((await pending.boundingBox())!.height).toBeCloseTo(6, 0);
  // The label is `modelLabel`'s folded spelling; the id itself rides `title`.
  await expect(trigger).toContainText(/catalog-model/i);
  await expect(trigger).not.toContainText("Running");
  await trigger.click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("composer-model-menu")).not.toBeVisible();
});

test("composer marks only an exact reported catalog model as selected", async ({ page }) => {
  const currentModel = "vendor-model-fast";
  await page.route("**/api/grove/workspaces/*/controls", (route) =>
    route.fulfill({
      json: {
        current_model: currentModel,
        models: [currentModel, "vendor-model-deep"],
        commands: [],
        skills: [],
        mcp_servers: [],
        permission_mode: "default",
      },
    }),
  );
  await openSharpSurface(page, "info", 300, "light");
  const trigger = page.getByTestId("composer-model-trigger");
  await expect(trigger).toHaveAttribute("aria-label", "Model");
  // The visible label folds the shared namespace away; the id is the title.
  await expect(trigger).toHaveAttribute("title", `Reported current model: ${currentModel}`);
  await trigger.click();

  const selected = page.getByTestId("composer-model-item").filter({ hasText: "fast" });
  await expect(selected).toHaveAttribute("aria-selected", "true");
  // The vendor's own `text-sm` at the density root — the size every other menu
  // in the app uses, so a picker is never the largest text on the page.
  await expect(selected).toHaveCSS("font-size", `${dp(13)}px`);
  await expect(page.getByTestId("composer-model-pending")).toHaveCount(0);
});

/**
 * ONE EVENT, ONE TOAST — the regression that motivated consolidating the hosts.
 *
 * Two `<Toaster>` mounts coexisted (the root layout's and Providers'), so every
 * notification rendered and was announced twice. Nothing caught it: each mount
 * is individually correct, and sonner renders no host at all until something is
 * toasted, so an idle page looks identical either way. That is why the count is
 * asserted against a REAL toast rather than against the source.
 *
 * The model route is fulfilled locally, so this exercises the delivery path
 * without any request reaching a daemon or a live session.
 */
test("one model request raises exactly one toast, at the app's own text size", async ({ page }) => {
  await page.route("**/api/grove/workspaces/*/controls", (route) =>
    route.fulfill({
      json: {
        current_model: "vendor-model-fast",
        models: ["vendor-model-fast", "vendor-model-deep"],
        commands: [],
        skills: [],
        mcp_servers: [],
        permission_mode: "default",
      },
    }),
  );
  let requests = 0;
  await page.route("**/api/grove/workspaces/*/controls/model", (route) => {
    requests += 1;
    return route.fulfill({ status: 204, body: "" });
  });

  await openSharpSurface(page, "info", 540, "dark");
  await page.getByTestId("composer-model-trigger").click();
  await page.getByTestId("composer-model-item").filter({ hasText: "deep" }).click();
  await expect.poll(() => requests).toBe(1);

  const toasts = page.locator("[data-sonner-toast]");
  await expect(toasts).toHaveCount(1);
  await expect(page.locator("[data-sonner-toaster]")).toHaveCount(1);

  // The wording stays truthful: delivered, never "switched". The agent's own
  // answer arrives in the Terminal tab, and this control never observed it.
  const title = toasts.locator("[data-title]");
  await expect(title).toHaveText("Model request delivered");
  await expect(toasts.locator("[data-description]")).toContainText("Waiting for the agent");

  // Compact type: the app's `text-sm` step, inherited by title and body from
  // the toast root. Sonner's own default is a flat 13px on a 16px assumption,
  // which rendered LARGER than every surface behind it at this density root.
  await expect(toasts).toHaveCSS("font-size", `${dp(13)}px`);
  await expect(title).toHaveCSS("font-weight", "500");

  // Dismissible, and the close control actually closes it.
  await toasts.locator("[data-close-button]").click();
  await expect(toasts).toHaveCount(0);
});
