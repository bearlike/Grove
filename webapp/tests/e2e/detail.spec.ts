import { test, expect } from "@playwright/test";

test("detail shell: context bar, branch-summary popover, tabbed agent surface", async ({
  page,
}) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("context-bar")).toBeVisible();
  await expect(page.getByTestId("agent-panel")).toBeVisible();
  await expect(page.getByTestId("agent-tabs")).toBeVisible();

  // The branch summary folds into a popover — opening it must surface the same
  // stat trio + commit list that used to stand in a column.
  await page.getByTestId("branch-summary-trigger").click();
  const summary = page.getByTestId("branch-summary");
  await expect(summary).toBeVisible();
  await expect(summary.getByTestId("stat-trio")).toBeVisible();
  await expect(summary.getByTestId("commit-list")).toBeVisible();
});

test("missing workspace shows error message", async ({ page }) => {
  await page.goto("/w/does-not-exist");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("transcript is the default tab when sessions exist; terminal stays reachable", async ({
  page,
}) => {
  // w-grove-1 has recorded sessions in the fake daemon → Transcript wins.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("data-state", "active");
  await expect(page.getByTestId("chat-panel")).toBeVisible();

  await page.getByTestId("tab-terminal").click();
  await expect(page.getByTestId("tab-terminal")).toHaveAttribute("data-state", "active");
  await expect(page.getByTestId("peek-snapshot")).toContainText("npm run dev");
  // The terminal pane carries its own live-capture badge (the freshness cue).
  await expect(page.getByTestId("terminal-capture-badge")).toBeVisible();
});

test("the page never scrolls horizontally — wide content scrolls inside its pane", async ({
  page,
}) => {
  // Pins the min-w-0 chain: the fixture transcript carries a long unbroken
  // token and the fixture terminal a 400-char grid line; both must stay
  // inside their panes, never widen the document.
  const pageOverflow = () =>
    page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );

  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-message").first()).toBeVisible();
  expect(await pageOverflow()).toBeLessThanOrEqual(0);

  await page.getByTestId("tab-terminal").click();
  const pre = page.getByTestId("peek-snapshot");
  await expect(pre).toBeVisible();
  // Guard against a vacuous pass: the grid line really is wider than the
  // pane — it scrolls inside the overflow-auto wrapper…
  const widths = await pre.evaluate((el) => ({
    pre: el.scrollWidth,
    pane: el.parentElement!.clientWidth,
  }));
  expect(widths.pre).toBeGreaterThan(widths.pane);
  // …while the page itself still does not scroll horizontally.
  expect(await pageOverflow()).toBeLessThanOrEqual(0);
});

test("a long transcript opens at the tail and re-sticks after a tab round-trip", async ({
  page,
}) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-message").first()).toBeVisible();

  // StickToBottom's scroll viewport is the immediate child of the role=log
  // root (the lib drives scrollTop on it directly).
  const scrollState = () =>
    page.evaluate(() => {
      const el = document.querySelector('[role="log"] > div') as HTMLElement;
      return {
        overflows: el.scrollHeight > el.clientHeight,
        atBottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 2,
      };
    });

  await expect.poll(async () => (await scrollState()).atBottom).toBe(true);
  // Guard against a vacuous pass: the fixture transcript genuinely overflows.
  expect((await scrollState()).overflows).toBe(true);

  // Switching away unmounts the tab content; coming back must land at the
  // tail again (initial="instant"), not at the top.
  await page.getByTestId("tab-terminal").click();
  await expect(page.getByTestId("peek-snapshot")).toBeVisible();
  await page.getByTestId("tab-transcript").click();
  await expect(page.getByTestId("chat-message").first()).toBeVisible();
  await expect.poll(async () => (await scrollState()).atBottom).toBe(true);
});

test("active background subagents surface as a badge; hidden at zero", async ({ page }) => {
  // w-grove-1's head session is working with 2 in-flight subagents.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("subagent-badge")).toHaveText("2 background agents");

  // w-grove-2's head session is idle (0 subagents) — no badge.
  await page.goto("/w/w-grove-2");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("subagent-badge")).toHaveCount(0);
});

test("a workspace with no recorded sessions defaults to the terminal tab", async ({ page }) => {
  // w-other-1 is the fake daemon's sessionless fixture.
  await page.goto("/w/w-other-1");
  await expect(page.getByTestId("tab-terminal")).toHaveAttribute("data-state", "active");
  // Its synthetic peek has no agent snapshot — the terminal empty state shows.
  await expect(page.getByTestId("peek-snapshot-empty")).toBeVisible();
  // A user's explicit click is never yanked away by later refetches.
  await page.getByTestId("tab-transcript").click();
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("data-state", "active");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
});

test("split view (lg+) shows transcript and terminal side by side", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chrome", "split is a lg+ affordance");
  await page.goto("/w/w-grove-1");
  await page.getByTestId("view-split").click();
  // Both panes are mounted together — the headline of the revamp.
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-pane")).toBeVisible();
  // …and the document still never scrolls horizontally.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("the split toggle is unreachable below lg", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "tabs-only below lg");
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("agent-tabs")).toBeVisible();
  await expect(page.getByTestId("view-split")).toHaveCount(0);
});
