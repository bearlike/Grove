// Capture the Grove web dashboard documentation screenshots.
//
// Driven by tools/screenshots/webapp_capture.py, which stands up a sandbox
// daemon (identity patched to fictional values) + `next start` against the
// synthetic demo fleet and hands this script the base URL plus the sandbox
// env. The script pairs a headless browser through the real /login flow
// (approving with `grove auth approve` under the sandbox env), then captures
// the fleet dashboard and the workspace surface (transcript + work panel,
// split open).
//
// Everything here is fictional (the acme-api / acme-web fleet). Nothing
// touches the real daemon or real repos.
//
// Shot set: webapp-pair-device, webapp-pair-code, webapp-home,
// webapp-workspace, webapp-usage, webapp-usage-detail (the last is the usage
// page scrolled to `By model` / `Recent sessions`, never shown in the first
// shot). Re-targeted against the current `AppShell` rail + `FleetDashboard` +
// `Workspace` split surface (webapp/CLAUDE.md), NOT the composer-first /
// old-dashboard testids this file used to select on.
//
// The desktop captures are 16:9 so `tools/screenshots/frame.py` composites
// them onto its own 16:9 canvas without letterboxing.

import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const BASE = process.env.BASE;
const OUT = process.env.OUT; // docs/img/screenshots
const WORKTREE = process.env.WORKTREE;
const STORAGE = process.env.STORAGE_STATE;

if (!BASE || !OUT || !WORKTREE) {
  throw new Error("BASE, OUT and WORKTREE env vars are required");
}

// Playwright lives in the webapp's node_modules, not next to this script,
// so resolve it from there explicitly.
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(WORKTREE, "webapp", "node_modules", "@playwright", "test"));
mkdirSync(OUT, { recursive: true });

// Env the sandbox `grove auth approve` subprocess needs (forwarded from
// the Python orchestrator).
const sandboxEnv = {
  ...process.env,
  XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
  XDG_STATE_HOME: process.env.XDG_STATE_HOME,
  CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR,
  // CODEX_HOME is sandboxed for the same reason the others are: the Codex
  // adapter falls back to ~/.codex, and an unsandboxed run would read the
  // developer's real rollouts into a committed screenshot.
  CODEX_HOME: process.env.CODEX_HOME,
};

/**
 * How long any one usage card may take to show real data.
 *
 * SIZED OFF THE ENGINE, NOT OFF FEAR. `UsageQuery.activity` used to fan out one
 * query pair per day, which cost 30.7s for tokens and 74.3s for cost over this
 * fixture's corpus and forced a five-minute ceiling here. It is one grouped
 * query per metric now: ~1.9s for tokens/sessions/tool_calls, 2.5s for cost,
 * 3.7s for active_minutes. The page fires roughly eight of these and they
 * serialize on the store's single lock, so the honest worst case is their sum,
 * around 30s. 90s is three times that — enough headroom for a loaded host,
 * while a genuine regression now surfaces in a minute and a half instead of
 * five. Re-derive this number if the engine changes again rather than nudging
 * it: a wait that no longer relates to a measurement is a wait nobody can
 * justify tightening.
 */
const DATA_WAIT_MS = 90_000;

const shot = (target, label, opts = {}) =>
  target.screenshot({ path: path.join(OUT, `${label}.png`), ...opts });

async function settle(page, ms = 900) {
  await page.waitForTimeout(ms);
}

/**
 * Report what the PAGE said, not only what this script saw.
 *
 * A capture failure here is a locator timeout, which is the same message
 * whether the data was late, the route threw during render, or the selector
 * moved. Those have completely different fixes and telling them apart by
 * inference has cost more wall clock than any other failure in this pipeline —
 * one diagnosis went to the engine, was measured there, and the engine turned
 * out to be answering correctly the whole time. A thrown render prints its own
 * message; a 500 prints Next's error page. Both are one run away, but only if
 * something is listening BEFORE the navigation that trips them.
 */
function reportPageFaults(page, label) {
  page.on("pageerror", (err) => console.error(`[${label}] page error: ${String(err).slice(0, 400)}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") console.error(`[${label}] console: ${msg.text().slice(0, 400)}`);
  });
}

/** What a page IS, when a locator says only what it is not. */
async function describePage(page) {
  const seen = await page.evaluate(() => ({
    path: location.pathname,
    testids: [...document.querySelectorAll("[data-testid]")].map((e) => e.dataset.testid),
    text: document.body.innerText.slice(0, 800),
  }));
  console.error(`path: ${seen.path}`);
  console.error(`testids: ${seen.testids.join(", ") || "(none)"}`);
  console.error(`body: ${seen.text}`);
}

/**
 * Collapse the left rail and wait for BOTH the click to register and the
 * width transition to finish, never just one.
 *
 * `webapp-home` is the one shot that keeps the rail expanded (it exists partly
 * to show it) — every other desktop capture is about its own content, and the
 * rail eats roughly a fifth of the frame. The collapse preference persists to
 * `localStorage` (`sidebar-state.ts`'s `grove.sidebar.collapsed`) and survives
 * navigation within one browser context, so calling this once before the
 * first post-home navigation is enough to keep it collapsed through every
 * shot after — same reasoning as `work-panel-tab-info` below: a persisted
 * value must be asserted after the click, never assumed from the click alone,
 * or a capture silently ships with the rail expanded and nothing looks wrong.
 */
async function collapseSidebar(page) {
  await page.getByTestId("shell-sidebar-toggle").click();
  await page
    .locator('[data-testid="app-sidebar"][data-collapsed="true"]')
    .waitFor({ timeout: 5_000 });
  await settle(page, 400);
}

// Force the space-black DARK theme on every capture. next-themes defaults to
// `system` (so headless Chrome's light preference would otherwise win): emulate
// a dark color scheme AND pin its localStorage key so first paint is already
// dark with no light→dark flip.
async function forceDark(ctx) {
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem("theme", "dark");
    } catch {
      /* storage may be unavailable pre-navigation; colorScheme still applies */
    }
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });

  // ── Phase 1: pairing on a phone-sized viewport (login is unchanged) ───
  const pairCtx = await browser.newContext({
    viewport: { width: 480, height: 860 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  await forceDark(pairCtx);
  const page = await pairCtx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });

  // The device-name field has no placeholder anymore; it is labelled instead.
  const input = page.getByLabel("Device name");
  await input.waitFor();
  await input.fill("Pixel 8 (Chrome)");
  await settle(page, 500);
  await shot(page, "webapp-pair-device");

  const pairResp = page.waitForResponse(
    (r) => r.url().includes("/api/auth/pair") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Pair this device" }).click();
  const resp = await pairResp;
  const { challenge_id: challengeId } = await resp.json();

  // The code renders in an <output> element; wait for it, then capture the
  // code step.
  await page.locator("output").first().waitFor();
  await settle(page, 400);
  await shot(page, "webapp-pair-code");

  // Approve host-side, exactly as a user would on the machine running Grove.
  execFileSync(
    "uv",
    ["run", "--directory", WORKTREE, "grove", "auth", "approve", challengeId],
    { env: sandboxEnv, stdio: "inherit" },
  );

  // The page polls every 2s; the next poll consumes the challenge, sets the
  // session cookie, and redirects to the dashboard.
  await page.waitForURL((url) => url.pathname === "/", { timeout: 30_000 });
  await pairCtx.storageState({ path: STORAGE });
  await pairCtx.close();

  // ── Phase 2: authenticated desktop captures ──────────────────────────
  // 1600x900 is 16:9, which is the canvas `tools/screenshots/frame.py`
  // composites onto — capturing at that ratio means the framer scales the
  // window rather than letterboxing it. Also wide enough to clear the
  // workspace page's 1024px split breakpoint.
  const ctx = await browser.newContext({
    storageState: STORAGE,
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  await forceDark(ctx);
  const d = await ctx.newPage();
  reportPageFaults(d, "desktop");

  // The fleet dashboard: rail + search/filter/create row + the flat,
  // attention-sorted workspace card grid.
  await d.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("app-sidebar").waitFor({ timeout: 20_000 });
  await d.getByTestId("fleet-dashboard").waitFor({ timeout: 20_000 });
  await d.getByTestId("workspace-card").first().waitFor({ timeout: 20_000 });
  await settle(d);
  await shot(d, "webapp-home");

  // Workspace detail (transcript + work panel). Pick the live
  // auth-refactor session so the transcript shows real planted turns.
  const list = await ctx.request
    .get(`${BASE}/api/grove/workspaces`)
    .then((r) => r.json());
  const rows = Array.isArray(list) ? list : (list.workspaces ?? []);
  const target = rows.find((w) => w.title === "auth-refactor") ?? rows[0];
  if (target) {
    await d.goto(`${BASE}/w/${target.id}`, { waitUntil: "domcontentloaded" });
    await d.getByTestId("workspace-page").waitFor({ timeout: 20_000 });
    await settle(d, 1000);

    // This page is about its own content, not the rail — collapse it. It
    // stays collapsed (persisted) through the usage shots below too, since
    // `webapp-home` is the only shot that keeps it expanded and it already
    // ran, above.
    await collapseSidebar(d);

    // The default pane is "transcript" only — the work panel does not mount
    // until "work" or "split" is selected (`workspace/index.tsx`'s
    // `paneView === "work" ? workPanel : transcript`). Click split so the
    // shot shows the transcript AND the work panel together, as the docs
    // caption describes; it is only offered at >= 1024px (SPLIT_MIN_WIDTH).
    await d.getByTestId("pane-split").click();
    await d.getByTestId("work-panel").waitFor({ timeout: 10_000 });

    // Land on Info, not Terminal. `tabOnViewChange` already returns "info"
    // when the view moves into split, but `workTab` also restores from
    // localStorage per workspace, so the tab a capture lands on is only
    // deterministic if the driver states it. The docs caption describes the
    // Info tab, and a terminal pane duplicates what webapp-home already shows.
    await d.getByTestId("work-panel-tab-info").click();
    await settle(d, 800);
    await shot(d, "webapp-workspace");
  } else {
    console.error("no workspace found for the detail capture");
  }

  // The usage audit: what the fleet spent, derived from the same synthetic
  // transcripts the rest of the fleet renders.
  //
  // NO REFRESH IS ISSUED FROM HERE, DELIBERATELY. `webapp_capture.py` projects
  // the corpus in process before it starts the daemon, so the cache is already
  // warm. This used to POST `/api/grove/usage/refresh`, which was fine while
  // the demo history was a month long; at a full year the projection takes
  // minutes and the request died at exactly 300s with a 502
  // `daemon_unreachable`. That ceiling is the Next BFF's own fetch timeout,
  // not this client's, so no timeout set here could ever have reached it.

  // Quota is genuinely populated now, not just the projection-derived
  // sections: `webapp_capture.py`'s daemon wrapper patches the Claude
  // provider's ONE network call to a fixed, plainly synthetic Max 20x
  // window (its credential-store read is real; only the HTTP call is
  // faked), and Codex reads its window for real off the demo fleet's own
  // planted rollout — neither needs live provider credentials. So the
  // capture waits on a real `usage-quota-card` too, alongside the
  // projection-derived sections below.
  const usageNav = await d.goto(`${BASE}/usage`, { waitUntil: "domcontentloaded" });

  // WAIT ON DATA, NEVER ON A SECTION WRAPPER. `usage-totals` and
  // `usage-activity` are the section shells and they render immediately,
  // BEFORE their queries resolve — so waiting on them proves only that the
  // route mounted. Every check after such a wait then runs against a
  // still-loading page: the heatmap was reporting "Not measured" (its
  // scroller only exists once there are points) and quota was showing none of
  // its three states, which read as two unrelated data bugs and were one
  // race. This used to pass only because the browser issued a refresh POST
  // here that took minutes, which incidentally gave react-query all the time
  // it needed. The scroller below IS the data, so waiting on it is the real
  // barrier — generously, because these queries run over a year of history.
  // `state: "attached"`, NOT the default `visible`. All this element is needed
  // for is an assignment to `scrollLeft`, which works the moment it is in the
  // DOM — while "visible" additionally demands a non-empty box, which this
  // scroller does not always have at the instant its data lands. Waiting on
  // visibility timed out at 120s on an element that was present and correct.
  //
  // A LATE ANSWER HERE ONCE READ AS A MISSING ONE, AND IT WAS NEITHER THE DATA
  // NOR THE SELECTOR. Driving `UsageService` directly over this fixture's cache
  // (1,000 sessions / 1.34M events), with no browser involved,
  // `activity(metric="tokens")` returned 365 buckets totalling 1.37e11 —
  // nothing missing — and took 30.7s, beside cost at 74.3s, because the query
  // fanned out per day. The page fires those together, so the wait expired on
  // work that was merely slow. The engine is one grouped query per metric now
  // and the budget above is sized off THAT; see `DATA_WAIT_MS`.
  //
  // IF IT EVER TRIPS AGAIN, MEASURE THE ENGINE FIRST — the branch below prints
  // the navigation status, every testid on the page, the card's own text and
  // what the API answered, because a bare timeout is indistinguishable between
  // "no data", "slow query" and "wrong selector", and guessing between those has
  // cost more wall clock than any other failure in this pipeline.
  const heatmapScroller = d.getByTestId("usage-activity").locator("div.overflow-x-auto").first();
  try {
    await heatmapScroller.waitFor({ state: "attached", timeout: DATA_WAIT_MS });
  } catch (err) {
    // EVERY STEP OF A DIAGNOSTIC MUST SURVIVE THE FAILURE IT DESCRIBES. This
    // block used to open with `getByTestId("usage-activity").innerText()`,
    // which auto-waits — so on the one failure where the whole section was
    // missing rather than merely empty, the diagnostic itself timed out and the
    // run reported a locator error about the diagnostic instead of anything
    // about the page. Enumerate what IS there first, unconditionally, and let
    // each narrower probe fail on its own without taking the rest down.
    console.error(`heatmap absent. navigation: ${usageNav?.status() ?? "unknown"}`);
    await describePage(d).catch((probeErr) => console.error(`describe failed: ${probeErr}`));
    // Distinguishes "the section rendered its other branch" from "the section
    // is not on the page at all" — different bugs with different owners.
    const card = d.getByTestId("usage-activity");
    if ((await card.count()) > 0) {
      console.error(`card says: ${(await card.innerText()).slice(0, 300)}`);
    } else {
      console.error("card says: (usage-activity is not in the DOM)");
    }
    // And what the engine itself answered, so a slow or empty daemon is never
    // mistaken for a broken page.
    const probe = await ctx.request.get(`${BASE}/api/grove/usage/activity?metric=tokens`);
    console.error(`activity api ${probe.status()}: ${(await probe.text()).slice(0, 300)}`);
    throw err;
  }

  // Quota is REPORTED, never waited on as a precondition. `usage-quota-card`
  // renders only when at least one account produced a reading; with none the
  // page renders `usage-quota-empty` instead, which is a perfectly valid
  // state. Blocking on the card turns "no subscription was read" into a hung
  // capture that produces no screenshots and names no cause — which is
  // exactly what it did once. Wait for ANY of the three states so the count
  // is not a race, then say which one we got and carry on.
  await d
    .locator(
      '[data-testid="usage-quota-card"], [data-testid="usage-quota-empty"], [data-testid="usage-quota-failed"]',
    )
    .first()
    .waitFor({ timeout: DATA_WAIT_MS });
  const quotaCards = await d.getByTestId("usage-quota-card").count();
  if (quotaCards > 0) {
    console.log(`usage quota: ${quotaCards} account card(s) rendered`);
  } else {
    const empty = await d.getByTestId("usage-quota-empty").count();
    const failed = await d.getByTestId("usage-quota-failed").count();
    console.error(`usage quota: NO account cards (empty=${empty}, failed=${failed})`);
  }

  // The Weekly mix rides its OWN query (`useUsageSeries`, a 7-day window), so
  // the heatmap landing says nothing about it — and it shipped mid-skeleton
  // twice, which is a loading placeholder published as marketing. Every card on
  // this page loads independently BY DESIGN, so a capture owes each visible one
  // its own barrier.
  //
  // `[data-testid="usage-series"] svg` WAS THAT BARRIER AND IT GATED NOTHING.
  // `UsageSection` renders the card's own header icon (a lucide glyph, i.e. an
  // `<svg>`) INSIDE the element carrying the testid, so the selector matched on
  // first paint, every time, while the card underneath was still a skeleton.
  // That is the wrapper-versus-data trap one level deeper than the one this
  // file already warns about: it is not enough to select inside the card, the
  // thing selected has to be something only DATA can produce. `.recharts-surface`
  // is drawn by the chart itself, which mounts only once `series` has arrived
  // (`loading={!series}` renders the skeleton instead), so it cannot be
  // satisfied by chrome. The two empty-state testids are the card's other
  // honest outcomes.
  await d
    .locator(
      '[data-testid="usage-series"] .recharts-surface, [data-testid="usage-series-empty"], [data-testid="usage-series-filtered"]',
    )
    .first()
    .waitFor({ state: "attached", timeout: DATA_WAIT_MS });

  // The heatmap is a 365-day calendar inside its own `overflow-x-auto`
  // boundary, and it opens at day one. The heaviest days are the most recent,
  // so an unscrolled capture buries them off the right edge. Scroll it to today.
  await heatmapScroller.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
  });

  await settle(d, 1200);
  await shot(d, "webapp-usage");

  // A second usage capture, scrolled to the lower half: `By model` and
  // `Recent sessions` are never shown in the first shot (it opens at the
  // top), so a reader can't see what the page offers below the fold.
  // Each renders from its own independent query (`useUsageBreakdown` /
  // `useUsageSessions`), so wait on their own content rather than assuming
  // the waits above already cover them. Wait on a ROW, not on the card:
  // both cards render their shell immediately and swap in a table only once
  // `rows.length > 0`, so the testid alone is satisfied by an empty card and
  // would shoot the "nothing measured" branch — the same wrapper-versus-data
  // trap that made the heatmap look broken.
  await d.getByTestId("usage-breakdown").locator("tbody tr").first().waitFor({ timeout: DATA_WAIT_MS });
  await d.getByTestId("usage-sessions").locator("tbody tr").first().waitFor({ timeout: DATA_WAIT_MS });
  // Both cards sit in the same grid row, so scrolling one into view brings
  // its sibling along for free.
  await d.getByTestId("usage-breakdown").scrollIntoViewIfNeeded();
  await settle(d, 800);
  await shot(d, "webapp-usage-detail");

  await ctx.close();
  await browser.close();
  console.log("captured web screenshots to", OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
