// Capture the Grove web dashboard documentation screenshots.
//
// Driven by tools/screenshots/webapp_capture.py, which stands up a sandbox
// daemon + `next start` against the synthetic demo fleet and hands this
// script the base URL plus the sandbox env. The script pairs a headless
// browser through the real /login flow (approving with `grove auth approve`
// under the sandbox env), then captures the composer-first unified surface,
// the composer's create affordance, a live focused pane, and the workspace
// IDE shell (transcript + terminal).
//
// Everything here is fictional (the acme-api / acme-web fleet). Nothing
// touches the real daemon or real repos.
//
// Shot set tracks the post-#94/#96/#99 redesign: `/activity` folded into `/`
// (#89), create is the always-present Composer (#96), and the home is ONE
// composer-first surface. There is no create dialog, no sessions drill-down,
// and no standalone activity wall to capture anymore.

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
};

const shot = (target, label, opts = {}) =>
  target.screenshot({ path: path.join(OUT, `${label}.png`), ...opts });

async function settle(page, ms = 900) {
  await page.waitForTimeout(ms);
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

  const input = page.getByPlaceholder("iPhone, Office Mac, …");
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

  // Wait for the big mono code to render, then capture the code step.
  await page.locator("p.text-3xl").first().waitFor();
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
  const ctx = await browser.newContext({
    storageState: STORAGE,
    viewport: { width: 1366, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  await forceDark(ctx);
  const d = await ctx.newPage();

  // The unified composer-first home (the list rides an SSE stream, so never
  // wait for networkidle — wait for real content instead): the always-present
  // composer hero plus the repo-grouped workspace grid.
  await d.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("composer-prompt").waitFor({ timeout: 20_000 });
  await d.getByTestId("workspace-card").first().waitFor({ timeout: 20_000 });
  await settle(d);
  await shot(d, "webapp-home-grid");

  // The composer as the create surface: type a task and reveal the advanced
  // context chips (repo · base · branch mode), then clip just the composer.
  try {
    const prompt = d.getByTestId("composer-prompt");
    await prompt.click();
    await prompt.fill("Add OAuth device-flow login to the API");
    const advanced = d.getByTestId("composer-advanced-toggle");
    if (await advanced.count()) {
      await advanced.first().click();
    }
    await settle(d, 500);
    const composer = d.getByRole("region", { name: "Create a workspace" });
    await shot(composer, "webapp-composer");
    // Clear the draft so it can't leak into later captures.
    await prompt.fill("");
    await settle(d, 200);
  } catch (err) {
    console.error("composer capture failed:", err.message);
  }

  // A live focused pane on the home surface: click a working card's Live
  // toggle (WORKING-gated — the fleet's auth-refactor session is working).
  try {
    const liveToggle = d.getByTestId("live-toggle").first();
    await liveToggle.waitFor({ timeout: 10_000 });
    await liveToggle.click();
    await d.getByTestId("focused-pane").waitFor({ timeout: 10_000 });
    await d.evaluate(() => window.scrollTo(0, 0));
    await settle(d, 1000);
    await shot(d, "webapp-focused-pane");
  } catch (err) {
    console.error("focused-pane capture failed:", err.message);
  }

  // Workspace detail (the IDE shell). Pick the live auth-refactor session.
  const list = await ctx.request
    .get(`${BASE}/api/grove/workspaces`)
    .then((r) => r.json());
  const target =
    (Array.isArray(list) ? list : list.workspaces ?? []).find(
      (w) => w.title === "auth-refactor",
    ) ?? (Array.isArray(list) ? list[0] : list.workspaces?.[0]);
  if (target) {
    await d.goto(`${BASE}/w/${target.id}`, { waitUntil: "domcontentloaded" });
    await d.getByTestId("agent-panel").waitFor({ timeout: 20_000 });
    await d.getByTestId("chat-message").first().waitFor({ timeout: 20_000 }).catch(() => {});
    await settle(d, 1000);
    await shot(d, "webapp-workspace-detail");

    // Terminal tab (the polled live pane).
    try {
      await d.getByTestId("tab-terminal").click();
      await d.getByTestId("terminal-pane").waitFor({ timeout: 10_000 });
      await d.getByTestId("peek-snapshot").first().waitFor({ timeout: 10_000 }).catch(() => {});
      await settle(d, 1200);
      await shot(d, "webapp-workspace-terminal");
    } catch (err) {
      console.error("terminal tab failed:", err.message);
    }

    // Split view (lg only): transcript + terminal side by side, scrolled to the
    // structured question card so the shot shows both the split AND a question.
    try {
      await d.getByTestId("view-split").click();
      await d.getByTestId("chat-panel").waitFor({ timeout: 10_000 });
      await d.getByTestId("terminal-pane").waitFor({ timeout: 10_000 });
      await d.getByTestId("question-card").first().scrollIntoViewIfNeeded({ timeout: 5_000 })
        .catch(() => {});
      await settle(d, 1000);
      await shot(d, "webapp-workspace-split");
    } catch (err) {
      console.error("split view failed:", err.message);
    }
  } else {
    console.error("no workspace found for the detail capture");
  }

  await ctx.close();

  // ── Phase 3: a mobile home shot for the phone device mockup ──────────
  const mob = await browser.newContext({
    storageState: STORAGE,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    colorScheme: "dark",
  });
  await forceDark(mob);
  const m = await mob.newPage();
  await m.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await m.getByTestId("composer-prompt").waitFor({ timeout: 20_000 });
  await m.getByTestId("workspace-card").first().waitFor({ timeout: 20_000 });
  await settle(m);
  await shot(m, "webapp-home-mobile");
  await mob.close();

  await browser.close();
  console.log("captured web screenshots to", OUT);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
