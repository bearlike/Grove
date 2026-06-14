// Capture the Grove web dashboard documentation screenshots.
//
// Driven by tools/screenshots/webapp_capture.py, which stands up a sandbox
// daemon + `next start` against the synthetic demo fleet and hands this
// script the base URL plus the sandbox env. The script pairs a headless
// browser through the real /login flow (approving with `grove auth approve`
// under the sandbox env), then captures the dashboard, the workspace IDE
// shell, the sessions drill-down, and the activity wall.
//
// Everything here is fictional (the acme-api / acme-web fleet). Nothing
// touches the real daemon or real repos.

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

const shot = (page, label, opts = {}) =>
  page.screenshot({ path: path.join(OUT, `${label}.png`), ...opts });

async function settle(page, ms = 900) {
  await page.waitForTimeout(ms);
}

async function main() {
  const browser = await chromium.launch({ headless: true });

  // ── Phase 1: pairing on a phone-sized viewport ───────────────────────
  const pairCtx = await browser.newContext({
    viewport: { width: 480, height: 860 },
    deviceScaleFactor: 2,
  });
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
  });
  const d = await ctx.newPage();

  // Home grid (the list surface rides an SSE stream, so never wait for
  // networkidle — wait for real content instead).
  await d.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("workspace-card").first().waitFor({ timeout: 20_000 });
  await settle(d);
  await shot(d, "webapp-home-grid");

  // Create-workspace dialog.
  await d.getByTestId("create-workspace-button").click();
  await d.getByTestId("create-dialog").waitFor();
  await settle(d, 600);
  await shot(d, "webapp-create-dialog");
  await d.keyboard.press("Escape");
  await settle(d, 300);

  // Sessions drill-down: switch to the acme-api tab, expand its Sessions
  // section, expand one session into its turns.
  try {
    await d.getByRole("tab", { name: /acme-api/ }).click();
    await settle(d, 400);
    await d.getByTestId("project-sessions-toggle").first().click();
    await d.getByTestId("session-row").first().waitFor({ timeout: 10_000 });
    await d.getByTestId("session-row").first().click();
    await d.getByTestId("turns-view").first().waitFor({ timeout: 10_000 });
    await settle(d, 600);
    await shot(d, "webapp-workspace-sessions");
  } catch (err) {
    console.error("sessions drill-down failed:", err.message);
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

    // Terminal tab (live pane).
    try {
      await d.getByTestId("tab-terminal").click();
      await d.getByTestId("terminal-pane").waitFor({ timeout: 10_000 });
      await settle(d, 1200);
      await shot(d, "webapp-workspace-terminal");
    } catch (err) {
      console.error("terminal tab failed:", err.message);
    }
  } else {
    console.error("no workspace found for the detail capture");
  }

  // Activity wall.
  await d.goto(`${BASE}/activity`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("session-card").first().waitFor({ timeout: 20_000 });
  await settle(d, 1000);
  await shot(d, "webapp-activity-wall");

  await ctx.close();

  // ── Phase 3: a mobile home shot for the phone device mockup ──────────
  const mob = await browser.newContext({
    storageState: STORAGE,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
  });
  const m = await mob.newPage();
  await m.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
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
