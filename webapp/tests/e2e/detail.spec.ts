import { test, expect } from "@playwright/test";

test("detail shell: identity popover, work panel, agent surface", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("context-bar")).toBeVisible();
  await expect(page.getByTestId("agent-panel")).toBeVisible();

  // The state + branch detail folds behind ONE state-led identity trigger —
  // w-grove-1 is ahead 1 / dirty 2, so the amber delta dot rides the
  // trigger as the glanceable "something to pull" cue, no click needed.
  await expect(page.getByTestId("branch-delta-dot")).toBeVisible();

  // Opening the identity popover surfaces the branch identity + the change
  // summary (the deleted strip's data, consolidated back behind the title).
  await page.getByTestId("identity-trigger").click();
  const summary = page.getByTestId("branch-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("main"); // base branch, Identity section
  await expect(summary.getByTestId("stat-trio")).toBeVisible();
  // The full commit list is NOT duplicated in the popover — it lives in the
  // work panel's Diff tab. Close the popover before reaching for that tab.
  await expect(summary.getByTestId("commit-list")).toHaveCount(0);
  await page.keyboard.press("Escape");
  // Radix keeps the closing popover mounted through its exit animation — wait
  // for it to actually leave before querying testids it shares with the panel.
  await expect(summary).toBeHidden();

  // Transcript, single pane, is the default at every breakpoint — the work
  // panel needs an explicit tab switch before its own Diff tab is reachable,
  // desktop and mobile alike.
  await page.getByTestId("tab-terminal").click();

  // The stat trio + full commit list live in the work panel's Diff tab. Scope
  // to the panel: the identity popover legitimately carries its own stat-trio
  // (the glance vs. detail duplication is the design), so an unscoped query
  // strict-fails whenever both are in the DOM.
  await page.getByTestId("work-panel-tab-diff").click();
  const diff = page.getByTestId("work-panel-diff-content");
  await expect(diff.getByTestId("stat-trio")).toBeVisible();
  await expect(diff.getByTestId("commit-list")).toBeVisible();
});

test("all three status axes + the linked refs survive opening a workspace (#330)", async ({
  page,
}) => {
  // Pins: every axis (agent activity, task phase, linked refs) must render
  // when a workspace is opened, not only on the overview grid card, and an
  // issue-only workspace (w-grove-1 has an issue, no PR) must show linkage.
  await page.goto("/w/w-grove-1");
  const bar = page.getByTestId("context-bar");

  // Axis 2 (agent activity) leads the cluster; axis 3 (task phase) rides beside
  // the title as the same compact badge the grid card wears.
  await expect(bar.getByTestId("state-mark")).toBeVisible();
  await expect(bar.getByTestId("phase-badge")).toHaveAttribute("data-phase", "implementing");
  await expect(bar.getByTestId("phase-badge")).toContainText("3/6");

  // The popover opens onto Task (the meter, named + noted) then Links.
  await page.getByTestId("identity-trigger").click();
  const summary = page.getByTestId("branch-summary");
  await expect(summary.getByTestId("phase-meter")).toContainText("implementing");
  await expect(summary.getByTestId("phase-note")).toContainText("wiring the third axis");
  await expect(summary.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "3");
  await expect(summary.getByTestId("ticket-linkage").getByRole("link", { name: /issue #330/ })).toHaveAttribute(
    "href",
    "https://git.example/bearlike/Grove/issues/330",
  );
  await page.keyboard.press("Escape");
  await expect(summary).toBeHidden();

  // …and the same two reads have a permanent home on the work panel's Info tab.
  await page.getByTestId("tab-terminal").click();
  await page.getByTestId("work-panel-tab-info").click();
  const info = page.getByTestId("work-panel-info-content");
  await expect(info.getByTestId("phase-meter")).toContainText("implementing");
  await expect(info.getByTestId("ticket-linkage")).toContainText("#330");
});

test("the identity cluster rides the app header at every width, never doubling", async ({
  page,
}) => {
  // The cluster is compact enough (state glyph + title + ⋯ + view well) to live
  // in the header at ALL breakpoints — there is no below-lg band anymore, so
  // exactly ONE context-bar mounts and it sits inside the header, wide or narrow.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("context-bar")).toHaveCount(1);
  // Portaled into the shared header's middle slot — still a DOM descendant of
  // <header>, still exactly one mount.
  await expect(page.locator('header [data-testid="context-bar"]')).toBeVisible();
  // The live agent state rides an sr-only aria-live region on the cluster.
  await expect(page.getByTestId("session-state-live")).toBeAttached();
});

test("the header stays one non-wrapping row on a narrow phone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "narrow-phone fit is the mobile case");
  // The whole point of the unify: the identity cluster + actions + view switcher
  // fit the header at 360px without wrapping or overflowing the document.
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/w/w-grove-1");
  const header = page.locator("header").first();
  await expect(header).toBeVisible();
  const box = await header.boundingBox();
  expect(box).not.toBeNull();
  // A wrapped header would grow past a single ~48px row (h-12). Allow a little
  // slack for sub-pixel rounding but well under a doubled row.
  expect(box!.height).toBeLessThan(64);
  // …and the document itself never scrolls horizontally.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("missing workspace shows error message", async ({ page }) => {
  await page.goto("/w/does-not-exist");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("transcript, single pane, is the default tab at every breakpoint when sessions exist; terminal stays reachable", async ({
  page,
}) => {
  // Single-pane transcript is the default everywhere — desktop does not
  // auto-open split, so this contract holds on desktop and mobile alike.
  // w-grove-1 has recorded sessions in the fake daemon → Transcript wins.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  // Split does not auto-mount — only the transcript pane is present.
  await expect(page.getByTestId("terminal-pane")).toHaveCount(0);

  await page.getByTestId("tab-terminal").click();
  await expect(page.getByTestId("tab-terminal")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("peek-snapshot")).toContainText("npm run dev");
  // The terminal pane carries its own live-capture badge (the freshness cue).
  await expect(page.getByTestId("terminal-capture-badge")).toBeVisible();
});

test("the page never scrolls horizontally — wide content scrolls inside its pane", async ({
  page,
}, testInfo) => {
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

  // Single pane is the default everywhere, so desktop opts into split
  // explicitly to exercise the harder overflow case (both panes mounted
  // side by side); mobile just tabs over to the terminal.
  if (testInfo.project.name === "mobile-chrome") {
    await page.getByTestId("tab-terminal").click();
  } else {
    await page.getByTestId("view-split").click();
  }
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
  // Single-pane tabs mode is the default everywhere — no view switch
  // needed to reach it, on desktop or mobile.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-message").first()).toBeVisible();

  // assistant-ui's ThreadPrimitive.Viewport IS the scroll container (the lib
  // drives scrollTop on it directly) — it carries role="log", so the scroller
  // is the `[role="log"]` element itself, not an inner child.
  const scrollState = () =>
    page.evaluate(() => {
      const el = document.querySelector('[role="log"]') as HTMLElement;
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

test("the header's live region announces the session state word only — no task text or bg count (#136/#153)", async ({
  page,
}) => {
  // w-grove-1's selected session is working — the sr-only live region carries
  // ONLY the state word, never the raw prompt/task text or a subagent count.
  await page.goto("/w/w-grove-1");
  const live = page.getByTestId("session-state-live");
  await expect(live).toContainText("working");
  await expect(live).not.toContainText("bg");
  await expect(live).not.toContainText("ai:"); // the session title never leaks in

  // w-grove-2's selected session is idle — the word flips, still no task/bg.
  await page.goto("/w/w-grove-2");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("session-state-live")).toContainText("idle");
  await expect(page.getByTestId("session-state-live")).not.toContainText("bg");
});

test("a workspace with no recorded sessions defaults to the terminal tab", async ({ page }) => {
  // w-other-1 is the fake daemon's sessionless fixture — split must never
  // flash without a session, so this stays tabs-mode on every project.
  await page.goto("/w/w-other-1");
  await expect(page.getByTestId("tab-terminal")).toHaveAttribute("aria-selected", "true");
  // Its synthetic peek has no agent snapshot — the terminal empty state shows.
  await expect(page.getByTestId("peek-snapshot-empty")).toBeVisible();
  // A user's explicit click is never yanked away by later refetches.
  await page.getByTestId("tab-transcript").click();
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
});

test("transcript, single pane, is the default even on lg+ desktop; split is opt-in and explicit choice wins both ways (#159)", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chrome", "split is a lg+ affordance");
  await page.goto("/w/w-grove-1");

  // Default: Transcript, single pane — split does NOT auto-mount even though
  // a session resolves and the viewport is lg+.
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-pane")).toHaveCount(0);

  // Opting into split mounts both panes side by side, still with no
  // horizontal overflow.
  await page.getByTestId("view-split").click();
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-pane")).toBeVisible();
  await expect(page.getByTestId("terminal-capture-badge")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  // Explicit choice wins both ways: dropping back to single-pane tabs sticks —
  // the Transcript tab reads selected and the terminal pane unmounts…
  await page.getByTestId("view-tabs").click();
  await expect(page.getByTestId("tab-transcript")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-pane")).toHaveCount(0);
  // …and so does going back to split.
  await page.getByTestId("view-split").click();
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-pane")).toBeVisible();
});

test("the split toggle is unreachable below lg", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chrome", "tabs-only below lg");
  await page.goto("/w/w-grove-1");
  // The view switcher rides the mobile band, tabs only — no split toggle.
  await expect(page.getByTestId("view-switcher")).toBeVisible();
  await expect(page.getByTestId("tab-transcript")).toBeVisible();
  await expect(page.getByTestId("view-split")).toHaveCount(0);
});

test("the composer stays fully within the fixed-height page, above the viewport bottom (#138)", async ({
  page,
}) => {
  // There is no status bar; the page column ends at the viewport bottom.
  // Invariant: the fixed-height layout keeps the composer fully on screen
  // (its bottom never pushed below the fold) with pb-2 breathing room.
  await page.goto("/w/w-grove-1");
  const composer = page.getByTestId("chat-composer");
  // The composer is the last node of a heavy transcript (dynamic streamdown
  // chunk + full fixture history); give the initial mount headroom.
  await expect(composer).toBeVisible({ timeout: 15000 });
  // No status bar remains to collide with.
  await expect(page.locator('footer[role="contentinfo"]')).toHaveCount(0);

  const cBox = await composer.boundingBox();
  const viewport = page.viewportSize();
  expect(cBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  // The composer's bottom edge sits on screen (a clipped composer would land
  // past the viewport's bottom).
  expect(cBox!.y + cBox!.height).toBeLessThanOrEqual(viewport!.height);
});
