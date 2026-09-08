import { deflateSync } from "node:zlib";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";

/**
 * Annotating a staged image, on both composers.
 *
 * Only a browser can prove this one: the editor is a custom element that
 * builds itself in `connectedCallback`, the split pane is a layout relation
 * between two boxes, "the page did not remount" is a statement about live
 * DOM identity, and the save is a rasterize through a real canvas. The pure
 * halves — the annotated NAME and the card's Annotate gate — are pinned in
 * `tests/unit/attachments.test.ts` and `tests/unit/attachment-file.test.tsx`.
 *
 * Every hook below is a `data-testid`, a `data-slot`, or an accessible name —
 * the marker.js toolbar names its buttons (`aria-label`), and Playwright's
 * locators pierce its open shadow root.
 */

/**
 * A real PNG, big enough to drag a marker across at natural size. Encoded
 * here rather than checked in as a binary fixture: a solid RGB image is a
 * header, one IDAT of filtered scanlines, and CRCs, which is less to review
 * than a base64 blob and says what the pixels are.
 */
function solidPng(width: number, height: number): Buffer {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (bytes: Buffer): number => {
    let c = 0xffffffff;
    for (const byte of bytes) c = crcTable[(c ^ byte) & 0xff]! ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type: string, data: Buffer): Buffer => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const sum = Buffer.alloc(4);
    sum.writeUInt32BE(crc(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB, no interlace
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, 0x9a)]);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const PNG = solidPng(240, 180);

const WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;

async function pickImage(page: Page, trigger: Locator, name: string): Promise<void> {
  // The landing attach button is disabled until the fleet answers whether any
  // project exists; a click on a disabled button opens no chooser and the wait
  // below would run out the test's whole budget.
  await expect(trigger).toBeEnabled({ timeout: 60_000 });
  const chooser = page.waitForEvent("filechooser");
  await trigger.click();
  await (await chooser).setFiles({ name, mimeType: "image/png", buffer: PNG });
}

/** Draw one rectangle marker across the editor's image, so a save has content. */
async function drawRectangle(page: Page, editor: Locator): Promise<void> {
  // The first marker group's default type is Rectangle; its button is a
  // `role=button` div named by the group's current type. The editing target
  // is an `<img>` inside the marker area's open shadow root, which CSS
  // locators pierce.
  await editor.getByRole("button", { name: "Rectangle", exact: true }).first().click();
  const image = editor.locator("img").first();
  await expect(image).toBeVisible();
  const box = (await image.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.8, { steps: 4 });
  await page.mouse.up();
}

test.describe("image annotation", () => {
  test.beforeEach(async ({ page }) => {
    // Wide enough for the split: below 1024 the editor opens maximized.
    await page.setViewportSize({ width: 1440, height: 1000 });
    // The tour opens itself on a first visit, and its mask sits over the
    // Annotate button: every landing test here failed on the click with the
    // reactour rect intercepting pointer events. Mark it seen before boot,
    // the way `onboarding.spec.ts` does for its own non-first-visit cases.
    await page.addInitScript(() => window.localStorage.setItem("grove.onboarding.seen", "true"));
  });

  test("landing: annotate opens a pane beside the page, save overwrites the staged file under an annotated name", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("textbox", { name: "Task brief", exact: true });
    await expect(input).toBeVisible({ timeout: 60_000 });
    await input.fill("Fix the layout in the attached screenshot.");

    // A text file gets no Annotate; an image does.
    const attach = page.getByRole("button", { name: /add attachment/i });
    await expect(attach).toBeEnabled({ timeout: 60_000 });
    const chooser = page.waitForEvent("filechooser");
    await attach.click();
    await (await chooser).setFiles([
      { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") },
      { name: "shot.png", mimeType: "image/png", buffer: PNG },
    ]);
    const cards = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]');
    await expect(cards).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Annotate notes.txt" })).toHaveCount(0);
    const annotate = page.getByRole("button", { name: "Annotate shot.png" });
    await expect(annotate).toHaveCount(1);

    const inputBefore = await input.evaluate((element) => {
      (element as HTMLElement & { __probe?: number }).__probe = 42;
      return true;
    });
    expect(inputBefore).toBe(true);

    await annotate.click();
    const pane = page.getByTestId("annotation-pane");
    await expect(pane).toBeVisible();
    // Beside the page, not over it: the pane's box starts where the page's ends.
    const pageBox = (await page.getByTestId("launch-page").boundingBox())!;
    const paneBox = (await pane.boundingBox())!;
    expect(paneBox.x).toBeGreaterThanOrEqual(pageBox.x + pageBox.width - 2);
    expect(paneBox.width).toBeGreaterThan(300);

    // The page did NOT remount: the draft, the staged files and a probe
    // stamped on the live textarea all survive the pane opening.
    await expect(input).toHaveValue("Fix the layout in the attached screenshot.");
    await expect(cards).toHaveCount(2);
    expect(await input.evaluate((element) => (element as HTMLElement & { __probe?: number }).__probe)).toBe(42);

    const editor = page.locator("mjsui-annotation-editor");
    await expect(editor.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await drawRectangle(page, editor);
    await editor.getByRole("button", { name: "OK", exact: true }).click();

    // Saved: the pane closes, the image row is REPLACED in place — still two
    // cards, the text one untouched, the image renamed — and the bytes changed.
    await expect(pane).toBeHidden();
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0)).toContainText("notes.txt");
    await expect(cards.nth(1)).toContainText("shot.annotated.webp");
    await expect(cards.nth(1)).not.toContainText(`${PNG.length} B`);

    // A re-edit reopens the annotated row and keeps the name stable.
    await page.getByRole("button", { name: "Annotate shot.annotated.webp" }).click();
    await expect(editor.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await editor.getByRole("button", { name: "OK", exact: true }).click();
    await expect(cards.nth(1)).toContainText("shot.annotated.webp");
    await expect(page.getByRole("button", { name: /^Annotate/ })).toHaveCount(1);
  });

  test("maximize moves the one editor into a dialog and back, and close discards", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Task brief", exact: true })).toBeVisible({ timeout: 60_000 });
    await pickImage(page, page.getByRole("button", { name: /add attachment/i }), "shot.png");
    await page.getByRole("button", { name: "Annotate shot.png" }).click();

    const editors = page.locator("mjsui-annotation-editor");
    await expect(editors).toHaveCount(1);
    await expect(editors.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await drawRectangle(page, editors);

    await page.getByTestId("annotation-maximize").click();
    const dialog = page.getByTestId("annotation-expanded");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("annotation-pane")).toHaveCount(0);
    // ONE editor, moved — and the marker drawn before the move is still there
    // (a moved custom element rebuilds itself; the draft has to be carried).
    await expect(editors).toHaveCount(1);
    await expect(dialog.locator("mjsui-annotation-editor")).toHaveCount(1);
    await expect(editors.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    // Carried across the move: the marker area reports one marker.
    await expect
      .poll(() =>
        editors.evaluate((element) => {
          const editor = element as unknown as { markerArea: { getState(): { markers: unknown[] } } };
          return editor.markerArea.getState().markers.length;
        }),
      )
      .toBe(1);

    await page.getByTestId("annotation-maximize").click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId("annotation-pane")).toBeVisible();
    await expect(editors).toHaveCount(1);

    // Grove's close discards: the staged file keeps its original name.
    await page.getByTestId("annotation-close").click();
    await expect(page.getByTestId("annotation-pane")).toHaveCount(0);
    await expect(page.locator('[data-slot="composer-bar"] [data-slot="file-root"]')).toContainText("shot.png");
    await expect(page.getByRole("button", { name: "Annotate shot.png" })).toHaveCount(1);
  });

  test("a large photo comes back SMALLER than it went in, bounded on its long edge", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Task brief", exact: true })).toBeVisible({ timeout: 60_000 });
    const attach = page.getByRole("button", { name: /add attachment/i });
    await expect(attach).toBeEnabled({ timeout: 60_000 });

    // A photo-like 3200x2400 JPEG made in the browser: noise on a gradient,
    // which is the content the first cut rendered as a 19 MB PNG.
    const jpeg = await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 3200;
      canvas.height = 2400;
      const ctx = canvas.getContext("2d")!;
      const pixels = ctx.createImageData(3200, 2400);
      for (let at = 0; at < pixels.data.length; at += 65536) {
        crypto.getRandomValues(pixels.data.subarray(at, Math.min(at + 65536, pixels.data.length)));
      }
      for (let i = 0; i < pixels.data.length; i += 4) {
        const x = (i / 4) % 3200;
        pixels.data[i] = (pixels.data[i]! >> 3) + ((x / 3200) * 200) | 0;
        pixels.data[i + 3] = 255;
      }
      ctx.putImageData(pixels, 0, 0);
      return canvas.toDataURL("image/jpeg", 0.8).split(",")[1]!;
    });
    const source = Buffer.from(jpeg, "base64");
    const chooser = page.waitForEvent("filechooser");
    await attach.click();
    await (await chooser).setFiles([{ name: "photo.jpg", mimeType: "image/jpeg", buffer: source }]);
    const card = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]').first();
    await expect(card).toContainText("photo.jpg");

    const editor = page.locator("mjsui-annotation-editor");
    await page.getByRole("button", { name: "Annotate photo.jpg" }).click();
    await expect(editor.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await drawRectangle(page, editor);
    await editor.getByRole("button", { name: "OK", exact: true }).click();
    await expect(card).toContainText("photo.annotated.webp", { timeout: 30_000 });

    // The bytes the create will carry: intercept the request instead of
    // trusting the card's rounded label.
    let sent: { name: string; bytes: Buffer } | null = null;
    await page.route("**/api/grove/workspaces", async (route) => {
      const body = route.request().postDataJSON() as {
        attachments?: { name: string; content_base64: string }[];
      };
      const [file] = body.attachments ?? [];
      if (file) sent = { name: file.name, bytes: Buffer.from(file.content_base64, "base64") };
      await route.fulfill({ status: 500, json: { detail: "stop here" } });
    });
    // The fixture's first agent has no catalog and the create refuses without
    // one chosen — the same step `shared-composer.spec.ts` takes.
    const agent = page.locator(".composer-shelf").getByRole("combobox", { name: "Agent", exact: true });
    await agent.click();
    await page.locator('[data-slot="model-selector-content"]').getByText("Claude Code (default)", { exact: true }).click();
    await page.getByRole("textbox", { name: "Task brief", exact: true }).fill("Annotated photo attached.");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect.poll(() => sent?.name).toBe("photo.annotated.webp");
    const { bytes } = sent!;
    // Smaller than the source, which a natural-size lossless render never was.
    expect(bytes.length).toBeLessThan(source.length);
    // RIFF....WEBP, and the VP8 header's dimensions read back bounded: the
    // long edge is 2048, so 3200x2400 renders as 2048x1536.
    expect(bytes.subarray(0, 4).toString("ascii")).toBe("RIFF");
    expect(bytes.subarray(8, 12).toString("ascii")).toBe("WEBP");
    const dims = await page.evaluate(async (b64) => {
      const img = new Image();
      img.src = `data:image/webp;base64,${b64}`;
      await img.decode();
      return { w: img.naturalWidth, h: img.naturalHeight };
    }, bytes.toString("base64"));
    expect(dims).toEqual({ w: 2048, h: 1536 });
  });

  test("the editor follows the app's resolved theme into its shadow root", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Task brief", exact: true })).toBeVisible({ timeout: 60_000 });
    await pickImage(page, page.getByRole("button", { name: /add attachment/i }), "shot.png");
    await page.getByRole("button", { name: "Annotate shot.png" }).click();
    const host = page.getByTestId("image-annotator");
    await expect(host).toHaveAttribute("data-theme", "dark");
    // The class the app's theme sets never crosses the shadow boundary, so
    // the element carries its own switch, and that is what must be dark.
    const editor = page.locator("mjsui-annotation-editor");
    await expect(editor.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await expect.poll(() =>
      editor.evaluate((element) => (element as unknown as { theme: string }).theme),
    ).toBe("dark");
  });

  test("workspace: the annotated file is what the composer uploads on send", async ({ page }) => {
    await page.goto(`/w/${WORKSPACE_ID}`);
    const input = page.getByRole("textbox", { name: "Message input", exact: true });
    await expect(input).toBeVisible({ timeout: 60_000 });
    await pickImage(page, page.getByRole("button", { name: /add attachment/i }), "shot.png");

    const cards = page.locator('[data-slot="composer-bar"] [data-slot="file-root"]');
    await expect(cards).toHaveCount(1);
    await page.getByRole("button", { name: "Annotate shot.png" }).click();
    const editor = page.locator("mjsui-annotation-editor");
    await expect(editor.getByRole("button", { name: "OK", exact: true })).toBeVisible({ timeout: 30_000 });
    await drawRectangle(page, editor);
    await editor.getByRole("button", { name: "OK", exact: true }).click();

    await expect(page.getByTestId("annotation-pane")).toHaveCount(0);
    await expect(cards).toHaveCount(1);
    await expect(cards.first()).toContainText("shot.annotated.webp");

    // What leaves the browser is the annotated file, under the annotated name.
    const uploaded: string[] = [];
    await page.route("**/api/grove/workspaces/*/attachments", async (route) => {
      const body = route.request().postDataJSON() as { name: string; content_base64: string };
      uploaded.push(body.name);
      const decoded = Buffer.from(body.content_base64, "base64");
      // RIFF....WEBP: the editor rasterized to WebP rather than echoing the source.
      expect(decoded.subarray(0, 4).toString("ascii")).toBe("RIFF");
      expect(decoded.subarray(8, 12).toString("ascii")).toBe("WEBP");
      expect(decoded.equals(PNG)).toBe(false);
      await route.fulfill({
        json: { id: "att_1", name: body.name, path: `/workspace/.grove/attachments/att_1/${body.name}` },
      });
    });
    await input.fill("Here is the marked-up screenshot.");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect.poll(() => uploaded).toEqual(["shot.annotated.webp"]);
  });
});
