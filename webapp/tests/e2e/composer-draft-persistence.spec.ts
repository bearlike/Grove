import { expect, test, type Page } from "@playwright/test";

import { STORAGE_STATE } from "../../playwright.config";
import { FIXTURE_WORKSPACES } from "./_fixtures";

const PRIMARY_WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;
const DRAFT_PREFIX = "grove-composer-draft:";
const DRAFT_FILE = "draft-notes.txt";

function draftKey(workspaceId: string): string {
  return `${DRAFT_PREFIX}${workspaceId}`;
}

async function composer(page: Page) {
  const input = page.getByRole("textbox", { name: "Message input", exact: true });
  await expect(input).toBeVisible();
  return input;
}

async function saveDraft(
  page: Page,
  workspaceId: string,
  text: string,
): Promise<void> {
  const input = await composer(page);
  await input.fill(text);
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(workspaceId)),
  ).not.toBeNull();
}

async function showTranscript(page: Page): Promise<void> {
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
}

async function deferMessage(page: Page): Promise<() => Promise<void>> {
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/grove/workspaces/*/message", async (route) => {
    await pending;
    await route.fulfill({ status: 204 });
  });
  return async () => release?.();
}

test("workspace drafts restore text and attachment intent, then clear only after send succeeds", async ({
  page,
}) => {
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  const input = await composer(page);
  await input.fill("Draft survives a workspace round trip");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await chooser).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("draft attachment"),
  });
  await expect(page.getByText(DRAFT_FILE, { exact: true })).toBeVisible();
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toContain(DRAFT_FILE);

  await page.goto("/");
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await expect(await composer(page)).toHaveValue("Draft survives a workspace round trip");
  await expect(page.getByText(DRAFT_FILE, { exact: true })).toBeVisible();

  await page.route("**/api/grove/workspaces/*/attachments", (route) =>
    route.fulfill({
      json: { id: "draft-attachment", name: DRAFT_FILE, path: "/workspace/draft-notes.txt" },
    }),
  );
  await page.route("**/api/grove/workspaces/*/message", (route) =>
    route.fulfill({ status: 204 }),
  );
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(await composer(page)).toHaveValue("");
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toBeNull();
});

test("an acknowledgement before its debounce cannot resurrect the submitted draft", async ({ page }) => {
  await page.route("**/api/grove/workspaces/*/message", (route) =>
    route.fulfill({ status: 204 }),
  );
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await saveDraft(page, PRIMARY_WORKSPACE_ID, "Immediate acknowledgement");

  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toBeNull();
  await page.waitForTimeout(400);
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toBeNull();
});

test("a newer edit during send survives the earlier acknowledgement", async ({ page }) => {
  const release = await deferMessage(page);
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await saveDraft(page, PRIMARY_WORKSPACE_ID, "First send");

  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await (await composer(page)).fill("Newer draft");
  // Release before the new text's 300ms persistence debounce has fired.
  await release();
  await page.waitForTimeout(400);
  await page.reload();
  await showTranscript(page);
  await expect(await composer(page)).toHaveValue("Newer draft");
});

test("a rejected send retains the draft across reload", async ({ page }) => {
  await page.route("**/api/grove/workspaces/*/message", (route) =>
    route.fulfill({ status: 500, body: "send refused" }),
  );
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await saveDraft(page, PRIMARY_WORKSPACE_ID, "Rejected drafts remain retryable");

  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(await composer(page)).toHaveValue("Rejected drafts remain retryable");
  await page.reload();
  await showTranscript(page);
  await expect(await composer(page)).toHaveValue("Rejected drafts remain retryable");
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toContain("Rejected drafts remain retryable");
});

test("re-adding a restored attachment dismisses its reminder immediately, and a send makes that durable", async ({
  page,
}) => {
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await chooser).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("draft attachment"),
  });
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toContain(DRAFT_FILE);

  await page.goto("/");
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await expect(page.getByTestId("composer-pending-re-add")).toContainText(DRAFT_FILE);

  const readd = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await readd).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("replacement"),
  });
  // Re-adding dismisses the on-screen reminder without waiting for the
  // debounce, AND flushes storage immediately — a stale timer must not
  // resurrect it if the reader now navigates away inside that window.
  await expect(page.getByTestId("composer-pending-re-add")).toHaveCount(0);
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).not.toContain("pendingReAdd");

  // Real bytes never survive a navigation, whether they arrived via the
  // original pick or the re-add — leaving without sending genuinely loses the
  // just-restaged file, so its reminder is honestly restored, not suppressed.
  await page.goto("/");
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await expect(page.getByTestId("composer-pending-re-add")).toContainText(DRAFT_FILE);

  // Sending is the only edge that durably retires the reminder: the file is
  // now in the daemon's hands, and no future remount has anything to remind.
  await page.route("**/api/grove/workspaces/*/attachments", (route) =>
    route.fulfill({ json: { id: "draft-attachment-2", name: DRAFT_FILE, path: "/workspace/draft-notes.txt" } }),
  );
  await page.route("**/api/grove/workspaces/*/message", (route) => route.fulfill({ status: 204 }));
  const finalAdd = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await finalAdd).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("sent for real"),
  });
  await (await composer(page)).fill("Sending the re-added file");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toBeNull();

  await page.goto("/");
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await expect(page.getByTestId("composer-pending-re-add")).toHaveCount(0);
});

test("re-adding through a same-runtime remount (the expand dialog) never shows a duplicate stale reminder", async ({
  page,
}) => {
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await chooser).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("draft attachment"),
  });
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
  ).toContain(DRAFT_FILE);

  // A FULL navigation loses the bytes and restores the reminder; an expand
  // dialog remount reuses the SAME runtime (the composer is moved, not
  // cloned), so the file the reader just picked is still genuinely staged —
  // no reminder should appear at all, duplicate or otherwise.
  await page.goto("/");
  await page.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
  await showTranscript(page);
  await expect(page.getByTestId("composer-pending-re-add")).toContainText(DRAFT_FILE);
  const readd = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add Attachment", exact: true }).click();
  await (await readd).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("replacement"),
  });
  await expect(page.getByTestId("composer-pending-re-add")).toHaveCount(0);

  await page.getByTestId("workspace-composer-expand").click();
  await expect(page.getByTestId("workspace-composer-expanded")).toBeVisible();
  await expect(page.getByTestId("composer-pending-re-add")).toHaveCount(0);
  await expect(page.getByText(DRAFT_FILE, { exact: true })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("workspace-composer-expanded")).toBeHidden();
  await expect(page.getByTestId("composer-pending-re-add")).toHaveCount(0);
});

test("landing drafts restore text and attachment intent", async ({ page }) => {
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Task brief", exact: true });
  await input.fill("Landing draft");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add attachment", exact: true }).click();
  await (await chooser).setFiles({
    name: DRAFT_FILE,
    mimeType: "text/plain",
    buffer: Buffer.from("landing attachment"),
  });
  await expect.poll(() =>
    page.evaluate((key) => sessionStorage.getItem(key), `${DRAFT_PREFIX}launch`),
  ).toContain(DRAFT_FILE);

  await page.goto("/fleet");
  await page.goto("/");
  await expect(input).toHaveValue("Landing draft");
  await expect(page.getByText(DRAFT_FILE, { exact: true })).toBeVisible();
  await expect(page.getByText("Re-add file", { exact: true })).toBeVisible();
});

test("workspace draft contexts never overwrite each other", async ({ browser }) => {
  // Explicit storageState rather than relying on the browser fixture's
  // defaults: `browser.newContext()` does not inherit the project's
  // `use.storageState`, only the `page`/`context` fixtures do, so each
  // context needs the paired session handed to it by name.
  const first = await browser.newContext({ storageState: STORAGE_STATE });
  const second = await browser.newContext({ storageState: STORAGE_STATE });
  const firstPage = await first.newPage();
  const secondPage = await second.newPage();
  try {
    await firstPage.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
    await showTranscript(firstPage);
    await saveDraft(firstPage, PRIMARY_WORKSPACE_ID, "First browser context draft");

    await secondPage.goto(`/w/${PRIMARY_WORKSPACE_ID}`);
    await showTranscript(secondPage);
    await expect(await composer(secondPage)).toHaveValue("");
    await saveDraft(secondPage, PRIMARY_WORKSPACE_ID, "Second browser context draft");

    await expect.poll(() =>
      firstPage.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
    ).toContain("First browser context draft");
    await expect.poll(() =>
      secondPage.evaluate((key) => sessionStorage.getItem(key), draftKey(PRIMARY_WORKSPACE_ID)),
    ).toContain("Second browser context draft");
  } finally {
    await Promise.all([first.close(), second.close()]);
  }
});
