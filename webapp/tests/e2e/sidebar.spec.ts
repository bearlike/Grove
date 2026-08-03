import { test, expect } from "@playwright/test";

// The rail filters (hiddenStates/hiddenProjects/attentionOnly/showUnmapped) +
// landing view persist to the `grove:ui` localStorage key, so a filter one test
// sets could otherwise leak into the next. Reset it before every navigation so
// each test starts at the defaults.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.removeItem("grove:ui"));
});

// The rail is a FLAT, cross-project session list ordered `modified_at`
// DESC — no project sections, no date groups, no attention pin. Each row carries
// a provenance meta line (project · branch · ±change). Only "mapped" rows (whose
// workspace is live in the /activity snapshot) show by default; unmapped
// metadata-only rows are hidden behind a note. The filter menu is the single
// organizing instrument. Fixtures: Grove has a working session (s-w-grove-1) and
// a waiting one the live snapshot flags for attention (s-w-grove-2), both mapped,
// plus a hand-staged row; website has only a hand-staged (unmapped) row.

test.describe("session rail (desktop)", () => {
  test("is a flat list with provenance meta and no group/section chrome", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("workspace-sidebar")).toBeVisible();
    await expect(page.getByTestId("session-rail")).toBeVisible();

    // The v2 group machinery is gone: no project sections, date groups, or pin.
    await expect(page.getByTestId("session-rail-project")).toHaveCount(0);
    await expect(page.getByTestId("session-rail-group")).toHaveCount(0);
    await expect(page.getByTestId("session-rail-attention")).toHaveCount(0);
    await expect(page.getByTestId("sidebar-repo-all")).toHaveCount(0);
    await expect(page.getByTestId("sidebar-state-chip")).toHaveCount(0);

    // Both mapped Grove sessions show; the waiting one signals attention inline.
    const working = page.locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-1"]');
    const waiting = page.locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-2"]');
    await expect(working).toBeVisible();
    await expect(waiting).toHaveAttribute("data-attention", "true");

    // Provenance meta: project name (dotted-underline cue) · branch · ±lines (30/4),
    // plus a visible created-ago on the row (not tooltip-only).
    await expect(working.getByTestId("session-rail-project-name")).toContainText("Grove");
    await expect(working).toContainText("dev/feat-dashboard");
    await expect(working.getByTestId("session-rail-changes")).toContainText("+30");
    await expect(working.getByTestId("session-rail-age")).toBeVisible();

    // …and the task phase as one more quiet sigil on that same line,
    // absent on the row whose workspace reports none.
    await expect(working.getByTestId("phase-badge")).toContainText("3/6");
    await expect(waiting.getByTestId("phase-badge")).toHaveCount(0);
  });

  test("hides unmapped rows by default; the hidden-note reveals them inert", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    // Hand-staged (null workspace) rows are hidden by default.
    const handGrove = page.locator('[data-testid="session-rail-row"][data-session-id="s-hand-Grove"]');
    const handSite = page.locator('[data-testid="session-rail-row"][data-session-id="s-hand-website"]');
    await expect(handGrove).toHaveCount(0);
    await expect(handSite).toHaveCount(0);

    const note = page.getByTestId("session-rail-hidden-note");
    await expect(note).toContainText("2 unmapped sessions hidden");
    await note.click();

    // Revealed: the note is gone and the metadata-only rows render inert (no link).
    await expect(page.getByTestId("session-rail-hidden-note")).toHaveCount(0);
    await expect(handSite).toHaveAttribute("data-navigable", "false");
    await expect(handSite.locator("a")).toHaveCount(0);
  });

  test("the filter menu hides a state and clears", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    const working = page.locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-1"]');
    const waiting = page.locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-2"]');
    await expect(working).toBeVisible();
    await expect(waiting).toBeVisible();

    // Uncheck "working" → the working session drops; the waiting one stays.
    await page.getByTestId("sidebar-filter-trigger").click();
    await page.getByTestId("filter-state-working").click();
    await expect(working).toHaveCount(0);
    await expect(waiting).toBeVisible();
    await expect(page.getByTestId("sidebar-filter-active")).toBeVisible();

    await page.getByTestId("filter-clear").click();
    await expect(working).toBeVisible();
  });

  test("the filter menu hides a whole project and clears", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    const working = page.locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-1"]');
    await expect(working).toBeVisible();

    // Hiding the Grove project drops its rows; the empty state acknowledges it.
    await page.getByTestId("sidebar-filter-trigger").click();
    await page.getByTestId("filter-project-/repos/Grove").click();
    await expect(working).toHaveCount(0);
    await expect(page.getByTestId("sidebar-filter-active")).toBeVisible();

    await page.getByTestId("filter-clear").click();
    await expect(working).toBeVisible();
  });

  test("a mapped row navigates to its transcript via ?s=", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await page
      .locator('[data-testid="session-rail-row"][data-session-id="s-w-grove-1"] a')
      .click();
    await expect(page).toHaveURL(/\/w\/w-grove-1\?s=s-w-grove-1$/);
  });

  test("search narrows the session list", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    await expect(page.getByTestId("session-rail-row").first()).toBeVisible();
    await page.getByTestId("sidebar-search").fill("feat dashboard");
    // Only the s-w-grove-1 session (title "ai: feat dashboard") survives.
    const rows = page.getByTestId("session-rail-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toHaveAttribute("data-session-id", "s-w-grove-1");
  });

  test("the collapse toggle fully hides the rail (modern-chat behavior)", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop-chrome", "rail is lg+");
    await page.goto("/");
    const wrapper = page.getByTestId("workspace-sidebar").locator("xpath=..");
    const toggle = page.getByTestId("sidebar-collapse-toggle");
    await expect(toggle).toHaveAttribute("aria-label", "Collapse sidebar");
    await expect(page.getByTestId("session-rail")).toBeVisible();
    expect((await wrapper.boundingBox())?.width ?? 0).toBeGreaterThan(100);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", "Expand sidebar");
    // Collapses ALL the way to w-0 — no icon strip survives.
    await expect(page.getByTestId("session-rail-icons")).toHaveCount(0);
    await expect
      .poll(async () => (await wrapper.boundingBox())?.width ?? 0)
      .toBeLessThan(10);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", "Collapse sidebar");
    await expect
      .poll(async () => (await wrapper.boundingBox())?.width ?? 0)
      .toBeGreaterThan(100);
    await expect(page.getByTestId("session-rail")).toBeVisible();
  });
});

test.describe("mobile drawer", () => {
  test("the rail opens as a drawer carrying the session list", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chrome", "drawer is sub-lg");
    await page.goto("/");
    const trigger = page.getByTestId("sidebar-trigger");
    await expect(trigger).toBeVisible();
    await trigger.click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByTestId("session-rail")).toBeVisible();
    await expect(drawer.getByTestId("sidebar-search")).toBeVisible();
  });
});
