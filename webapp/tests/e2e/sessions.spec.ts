import { test, expect } from "@playwright/test";

// `/sessions` is the host-wide Session Catalog — the one surface that
// reaches a session with no Grove workspace, including in repos Grove has never
// managed. The fake daemon's host-scope `GET /sessions` (no `repo`) serves four
// rows: a foreign live codex session, a Grove-managed one, a repo-less one, and
// one whose head read recovered no cwd.

test("the header nav reaches the catalog from any route", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-sessions").click();
  await expect(page).toHaveURL(/\/sessions$/);
  await expect(page.getByTestId("sessions-page")).toBeVisible();
});

test("lists sessions across every repo on the host, grouped by project", async ({ page }) => {
  await page.goto("/sessions");
  await expect(page.getByTestId("session-catalog-row")).toHaveCount(4);

  // Grouped by project, newest group first — including a repo Grove has never
  // managed, which the rail's per-known-project fan-out could never surface.
  const groupNames = page.getByTestId("session-catalog-group-name");
  await expect(groupNames).toHaveText(["untracked-lab", "Grove", "No git repository"]);
});

test("marks a live session with a text label, not colour alone", async ({ page }) => {
  await page.goto("/sessions");
  const liveRow = page.locator("[data-testid='session-catalog-row'][data-live='true']");
  await expect(liveRow).toHaveCount(1);
  await expect(liveRow.getByTestId("session-catalog-live")).toContainText("live");
});

test("a cwd-less row is listed but not openable, and says why", async ({ page }) => {
  await page.goto("/sessions");
  const row = page.locator("[data-testid='session-catalog-row'][data-drillable='false']");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("location unknown");
  await expect(row.locator("a")).toHaveCount(0);
});

test("opening a non-Grove session renders its turns read-only", async ({ page }) => {
  await page.goto("/sessions");
  // Wait for the list to SETTLE before clicking: the catalog swaps skeletons
  // for rows when its one fetch resolves, and a click landing inside that
  // commit can be swallowed by the node replacement.
  await expect(page.getByTestId("session-catalog-row")).toHaveCount(4);

  // The foreign codex session — never launched by Grove, so it is reachable
  // only through the workspace-less `(kind, cwd, id)` drill-in.
  await page.locator("[data-session-id='cat-foreign-1'] a").click();

  await expect(page).toHaveURL(/\/sessions\/cat-foreign-1\?/);
  await expect(page.getByTestId("session-detail")).toBeVisible();
  // The transcript is a `next/dynamic` leaf (it pulls streamdown), so the FIRST
  // visit to this route in a dev server pays an on-demand chunk compile that can
  // outrun the 5 s expect default. That is build latency, not product latency.
  await expect(page.getByTestId("read-only-transcript")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("This is an archived conversation.")).toBeVisible();

  // Read-only is the whole contract: no composer, no interrupt, no lifecycle.
  await expect(page.getByTestId("chat-composer")).toHaveCount(0);
  await expect(page.getByTestId("chat-interrupt")).toHaveCount(0);
  await expect(page.getByTestId("identity-trigger")).toHaveCount(0);

  await page.getByTestId("session-detail-back").click();
  await expect(page).toHaveURL(/\/sessions$/);
});

test("the filter narrows the catalog without reading as an empty host", async ({ page }) => {
  await page.goto("/sessions");
  await page.getByTestId("sessions-search").fill("untracked");
  await expect(page.getByTestId("session-catalog-row")).toHaveCount(1);

  await page.getByTestId("sessions-search").fill("no-such-session");
  await expect(page.getByTestId("session-catalog-empty")).toContainText(
    "No sessions match your search.",
  );
});
