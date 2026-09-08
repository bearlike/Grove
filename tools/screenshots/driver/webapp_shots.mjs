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
// Shot set: webapp-pair-device, webapp-pair-code, webapp-home (the fleet, at
// `/fleet`), webapp-composer (the launch surface, at `/`), webapp-sessions,
// webapp-workspace, webapp-annotate, webapp-diagram-split, webapp-diagram,
// webapp-diagram-palette, webapp-usage,
// webapp-usage-detail (the last is the usage page scrolled to `By model` /
// `Recent sessions`, never shown in the first shot). The two diagram shots
// need the hosted draw.io embed to load, so they are the only ones that
// depend on the network; an offline run skips them and says so.
//
// `webapp-home` is the ONLY shot that keeps the rail expanded; it exists partly
// to show it. Everything after it runs collapsed, and the order below is
// therefore load-bearing rather than incidental.
//
// The desktop captures are 16:9 so `tools/screenshots/frame.py` composites
// them onto its own 16:9 canvas without letterboxing.

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
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

/**
 * How long the hosted draw.io embed may take to load and acknowledge the
 * document. It is a third-party page over the network, so the bound is a
 * network budget rather than an engine one: measured cold loads on this
 * pipeline's host sit under 10s, and 45s leaves room for a slow link without
 * turning an offline run into a minute-long stall per shot.
 */
const DIAGRAM_WAIT_MS = 45_000;

/**
 * The image the annotation shot stages: the onboarding tour's own sample, a
 * stock crosswalk photo with every pedestrian boxed and labelled. One asset
 * for both because they answer the same question — what an annotated file
 * looks like in the pane — and a real annotated file rather than a fixture
 * drawn on the fly, because the point of the shot is a pane with work in it.
 * Its name is the file card's caption, so it is staged under a short one.
 */
const ANNOTATE_ASSET = path.join(WORKTREE, "webapp", "public", "onboarding", "tour-sample-annotated-photo.webp");
const ANNOTATE_NAME = "crosswalk.webp";

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
      // The onboarding tour opens itself for a browser that has never seen
      // it, which a fresh headless context always is — so without this every
      // landing capture carries "1 of 20" over the composer. Same seam as the
      // theme: a persisted flag the app reads before first paint.
      localStorage.setItem("grove.onboarding.seen", "true");
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
  //
  // IT IS `/fleet`, NOT `/`. The root route is the launch composer now, and
  // this shot used to navigate to `/` and wait for `fleet-dashboard` — which
  // would simply time out. Route ownership moved under the composer landing
  // work; the testid never changed, which is what makes the staleness look
  // like a data problem rather than a routing one.
  await d.goto(`${BASE}/fleet`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("app-sidebar").waitFor({ timeout: 20_000 });
  await d.getByTestId("fleet-dashboard").waitFor({ timeout: 20_000 });
  await d.getByTestId("workspace-card").first().waitFor({ timeout: 20_000 });
  await settle(d);
  await shot(d, "webapp-home");

  // Everything from here on is about its own content rather than the rail, and
  // the collapse persists for the rest of the context — so this is the single
  // place it happens.
  await collapseSidebar(d);

  // The app's own landing page: the launch composer, which is what `/` serves.
  // Wait on the INPUT, not on `launch-page` — the page shell renders before the
  // composer's controls resolve their cascade defaults, and a shot taken on the
  // shell catches the row mid-populate.
  //
  // SHOT IN ITS OWN CONTEXT, AT A SMALLER VIEWPORT. The composer is one control
  // centred in a page that is otherwise empty, so at 1600x900 it reads as a
  // small box in a dark field. 800x450 is the same 16:9 at half the width,
  // which the framer scales onto the same canvas — the control fills the
  // frame instead of a quarter of it. `deviceScaleFactor` doubles to 4 so the
  // capture stays 3200x1800 and the framer still DOWNsamples. The rail's
  // collapsed state rides localStorage, so the init script pins it here the
  // way `forceDark` pins the theme; a fresh context inherits neither.
  const composerCtx = await browser.newContext({
    storageState: STORAGE,
    viewport: { width: 800, height: 450 },
    deviceScaleFactor: 4,
    colorScheme: "dark",
  });
  await forceDark(composerCtx);
  await composerCtx.addInitScript(() => {
    try {
      localStorage.setItem("grove.sidebar.collapsed", "true");
    } catch {
      /* same fallback as forceDark */
    }
  });
  const c = await composerCtx.newPage();
  reportPageFaults(c, "composer");
  await c.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await c.getByTestId("launch-composer").waitFor({ timeout: 20_000 });
  await c.getByTestId("launch-input").waitFor({ timeout: 20_000 });
  await c.getByTestId("launch-controls").waitFor({ timeout: 20_000 });
  // The controls row mounts with each pill's NOUN as its label and swaps in
  // the resolved default when the cascade answers — so a shot on the row's
  // testid alone caught "Agent" and "Runtime" as literal placeholders. Wait on
  // the two that resolve last, by their accessible name and resolved text.
  await c.locator('[data-pill="agent"]').filter({ hasText: /claude|codex/ }).waitFor({
    timeout: 20_000,
  });
  await c.locator('[data-pill="runtime"]').filter({ hasText: /Host|Container/ }).waitFor({
    timeout: 20_000,
  });
  await settle(c, 1200);
  await shot(c, "webapp-composer");
  await composerCtx.close();

  // The host-wide session catalog. Wait on a ROW: `sessions-page` is the shell
  // and renders immediately, so it would shoot the skeleton.
  await d.goto(`${BASE}/sessions`, { waitUntil: "domcontentloaded" });
  await d.getByTestId("session-row").first().waitFor({ timeout: DATA_WAIT_MS });
  await settle(d, 800);
  await shot(d, "webapp-sessions");

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
    // WAIT ON CONTENT, NOT ON THE TAB. The transcript renders a skeleton until
    // the activity snapshot has named a primary session AND `/turns` has
    // answered, and the Info tab's Activity card reads the same snapshot —
    // so a shot taken on the tab click alone captured grey bars on the left
    // and "No live session to measure" on the right, twice, in two published
    // runs. The first message and the metrics grid are the two facts the
    // caption describes; wait for both.
    await d.getByTestId("user-message-collapse").first().waitFor({ timeout: DATA_WAIT_MS });
    await d.locator('[data-testid="metrics"] [data-testid="metric-turns"]').waitFor({
      timeout: DATA_WAIT_MS,
    });
    await settle(d, 800);
    await shot(d, "webapp-workspace");

    // Annotating a staged image, on the same workspace: the transcript alone
    // on the left, the marker.js editor in the shell's split pane on the
    // right. The image is a committed asset that already carries markers, so
    // the shot shows the editor with content rather than a blank canvas
    // waiting for a first stroke — and the editor reopens a picked file as
    // pixels, so nothing here depends on marker state surviving a reload.
    //
    // THE PANE IS DRAGGED TO THE WIDER HALF. The host opens it at 45% so the
    // page keeps its draft in view; the docs shot is about the editor, so the
    // handle is dragged to 40% of the window and the pane takes the rest.
    // Dragging rather than seeding a layout: the group persists nothing, and
    // a drag is what a reader does. The click on `pane-transcript` collapses
    // the work panel first so the page half is the transcript and its
    // composer, which is where the staged file card sits.
    await d.getByTestId("pane-transcript").click();
    await d.getByTestId("work-panel").waitFor({ state: "hidden", timeout: 10_000 });
    const chooser = d.waitForEvent("filechooser");
    await d.getByRole("button", { name: /add attachment/i }).click();
    await (await chooser).setFiles({
      name: ANNOTATE_NAME,
      mimeType: "image/webp",
      buffer: readFileSync(ANNOTATE_ASSET),
    });
    await d.getByRole("button", { name: `Annotate ${ANNOTATE_NAME}` }).click();
    const pane = d.getByTestId("annotation-pane");
    await pane.waitFor({ timeout: 10_000 });
    // The editor is a custom element that builds itself after a dynamic
    // import; its toolbar's OK button is the first thing that proves it did.
    await d
      .locator("mjsui-annotation-editor")
      .getByRole("button", { name: "OK", exact: true })
      .waitFor({ timeout: 30_000 });
    const handle = d.locator('[data-testid="annotation-split"] > [data-slot="resizable-handle"]');
    const grip = await handle.boundingBox();
    const { width: viewportWidth } = d.viewportSize();
    await d.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await d.mouse.down();
    await d.mouse.move(viewportWidth * 0.4, grip.y + grip.height / 2, { steps: 12 });
    await d.mouse.up();
    // Assert the drag landed rather than trusting it: the pane must hold at
    // least half the window, which is what the docs caption promises.
    const paneBox = await pane.boundingBox();
    if (paneBox.width < viewportWidth / 2) {
      throw new Error(`annotation pane is ${paneBox.width}px of ${viewportWidth}px, under half`);
    }
    // The editor opens at 100%, which for a 2048px portrait is one corner of
    // the picture and no sense of the whole. Its toolbar has no Zoom to Fit
    // (only the read-only viewer does), so press Zoom Out, in the editor's
    // own 0.1 steps, until the picture fits the MARKER AREA's box. Not the
    // editor's: that includes the toolbars above and below the canvas, and a
    // fit computed against it stops one step too large, with the picture
    // clipped at the bottom and no scroller to blame (measured 953x763 for
    // the area against the pane's 1200x1050).
    const editor = d.locator("mjsui-annotation-editor");
    const zoomOut = editor.getByRole("button", { name: "Zoom Out", exact: true });
    const fit = await editor.evaluate((element) => {
      const image = element.targetImage;
      const area = element.markerArea.getBoundingClientRect();
      return Math.min(area.width / image.naturalWidth, area.height / image.naturalHeight);
    });
    // Bounded: the editor floors its zoom at 0.2 and a pane narrower than
    // that would otherwise loop forever on a click that changes nothing.
    for (let step = 0; step < 8; step += 1) {
      if ((await editor.evaluate((element) => element.markerArea.zoomLevel)) <= fit) break;
      await zoomOut.click();
    }
    await settle(d, 1200);
    await shot(d, "webapp-annotate");
  } else {
    console.error("no workspace found for the detail capture");
  }

  // The diagram workspace: the fixture's `arch-diagram` entry carries a
  // `.drawio` the planter opened through the engine, so the work panel offers
  // a Diagram tab whose editor is the REAL hosted draw.io embed. Two shots:
  // the split (transcript beside the rendered diagram) and the diagram alone
  // in the work pane. Both wait on the "Saved" badge rather than on the
  // iframe, because the frame mounts long before the editor has loaded the
  // document and the first paint is draw.io's own spinner.
  //
  // THE EDITOR IS A THIRD-PARTY PAGE FETCHED OVER THE NETWORK. An offline
  // host, or a run where the embed host is slow, times out here; it is
  // reported and the other shots are still written, so a regeneration on a
  // disconnected machine degrades to the shot set that existed before the
  // diagram pair rather than failing wholesale.
  const diagramTarget = rows.find((w) => w.title === "arch-diagram");
  if (diagramTarget) {
    await d.goto(`${BASE}/w/${diagramTarget.id}`, { waitUntil: "domcontentloaded" });
    await d.getByTestId("workspace-page").waitFor({ timeout: 20_000 });
    await settle(d, 1000);
    await d.getByTestId("pane-split").click();
    await d.getByTestId("work-panel").waitFor({ timeout: 10_000 });
    await d.getByTestId("work-panel-tab-diagram").click();
    try {
      await d
        .getByTestId("diagram-save-state")
        .filter({ hasText: "Saved" })
        .waitFor({ timeout: DIAGRAM_WAIT_MS });
      // The tab fits the drawing to the frame 250ms after the last resize; the
      // split just opened, so give the fit and the editor's own render a beat.
      await settle(d, 2500);
      await shot(d, "webapp-diagram-split");

      await d.getByTestId("pane-work").click();
      await settle(d, 2500);
      // The tab's own fit lands the drawing with its lower third clipped in
      // the full-width pane: the fit is computed for the split's frame, and
      // the format panel draw.io opens past its own width threshold then
      // takes canvas after the fit ran. Press the editor's own Fit Window
      // shortcut on the settled canvas, the keystroke a person would use
      // rather than a message the tab sends.
      // The click gives the editor keyboard focus and selects whatever cell
      // sat under it, and Fit Window fits the SELECTION when there is one —
      // measured as one text cell at 765%. Select None (Ctrl+Shift+A) first;
      // Escape does not clear a selection in draw.io, it only cancels an edit.
      const editor = d.frameLocator('[data-testid="diagram-frame"]');
      await editor.locator("body").click({ position: { x: 600, y: 700 } });
      await d.keyboard.press("Control+Shift+A");
      await settle(d, 300);
      await d.keyboard.press("Control+Shift+H");
      await settle(d, 1500);
      await shot(d, "webapp-diagram");
    } catch (error) {
      console.error(`diagram shots skipped: ${error instanceof Error ? error.message : error}`);
      await describePage(d);
    }
  } else {
    console.error("no arch-diagram workspace found for the diagram captures");
  }

  // A second diagram workspace, work pane only: a colour palette board, the
  // quick-mockup shape of the feature rather than the architecture-spec one.
  // Same wait, same fit, no split — the docs page that shows it is about the
  // picture the agent drew, not the transcript beside it.
  const paletteTarget = rows.find((w) => w.title === "palette-diagram");
  if (paletteTarget) {
    await d.goto(`${BASE}/w/${paletteTarget.id}`, { waitUntil: "domcontentloaded" });
    await d.getByTestId("workspace-page").waitFor({ timeout: 20_000 });
    await settle(d, 1000);
    // ASSERT the pane after the click, never assume it: the view restores from
    // localStorage per workspace, and one shot came back split with the
    // editor still on draw.io's "Loading..." page because neither the pane
    // nor the paint had been waited on.
    // The page restores the visit's pane from localStorage in an effect that
    // runs AFTER first paint, and a click that lands before it is folded in —
    // so a click on the first frame is authoritative, but the previous
    // workspace's `split` can still win when the transcript arrives later and
    // the view resolves from `hasTranscript`. Click AFTER the transcript has
    // rendered, then assert the pane, so the sequence is fixed rather than
    // raced: one shot came back split with draw.io still on its loading page.
    await d.getByTestId("user-message-collapse").first().waitFor({ timeout: DATA_WAIT_MS });
    await d.getByTestId("pane-work").click();
    await d
      .locator('[data-testid="pane-work"][data-state="active"]')
      .waitFor({ timeout: 10_000 });
    await d.getByTestId("work-panel").waitFor({ timeout: 10_000 });
    await d.getByTestId("work-panel-tab-diagram").click();
    try {
      await d
        .getByTestId("diagram-save-state")
        .filter({ hasText: "Saved" })
        .waitFor({ timeout: DIAGRAM_WAIT_MS });
      const editor = d.frameLocator('[data-testid="diagram-frame"]');
      // The badge says the DOCUMENT is acknowledged; the canvas class says the
      // editor has painted. Wait on the second, then let the fit settle.
      await editor.locator(".geDiagramContainer").first().waitFor({ timeout: DIAGRAM_WAIT_MS });
      await settle(d, 2500);
      await editor.locator("body").click({ position: { x: 600, y: 700 } });
      await d.keyboard.press("Control+Shift+A");
      await settle(d, 300);
      await d.keyboard.press("Control+Shift+H");
      await settle(d, 1500);
      await d
        .locator('[data-testid="pane-work"][data-state="active"]')
        .waitFor({ timeout: 5_000 });
      await shot(d, "webapp-diagram-palette");
    } catch (error) {
      console.error(`palette shot skipped: ${error instanceof Error ? error.message : error}`);
      await describePage(d);
    }
  } else {
    console.error("no palette-diagram workspace found for the palette capture");
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
