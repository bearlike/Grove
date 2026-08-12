import { expect, test } from "@playwright/test";

import { FIXTURE_WORKSPACES } from "./_fixtures";

/**
 * One route smoke per surface: it renders its own page marker, its shell is
 * present, and nothing threw on the way.
 *
 * Deliberately shallow. Adapter behaviour is covered by `tests/unit` against
 * the same captured payloads, and re-asserting it through a browser buys
 * latency and flakiness rather than confidence. What ONLY a browser can prove
 * is what this file checks: routing, the auth gate, and that each page's data
 * path survives a real request through the BFF to a bearer-gated daemon.
 */

const WORKSPACE_ID = FIXTURE_WORKSPACES[0].id;

/**
 * Console noise we cannot fix and must not fail on.
 *
 * `components/ui/sidebar.tsx` picks its skeleton width with `Math.random()`
 * (line ~611), so server and client HTML disagree by construction whenever
 * `SidebarMenuSkeleton` renders during SSR. It is VENDORED: patching it would
 * fail `registry:check`, which is exactly the trade this app signed up for.
 * Anything not on this list still fails the smoke.
 */
const UPSTREAM_NOISE = [/hydrat/i, /--skeleton-width/];

function isUpstreamNoise(text: string): boolean {
  return UPSTREAM_NOISE.some((pattern) => pattern.test(text));
}

test.describe("route smoke", () => {
  for (const [name, path, marker] of [
    ["fleet dashboard", "/", "fleet-page"],
    ["workspace", `/w/${WORKSPACE_ID}`, "workspace-page"],
    ["usage", "/usage", "usage-page"],
    ["sessions", "/sessions", "sessions-page"],
  ] as const) {
    test(`${name} renders`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error" && !isUpstreamNoise(message.text())) errors.push(message.text());
      });
      // An uncaught exception is never noise — a thrown render unmounts the tree.
      page.on("pageerror", (error) => errors.push(String(error)));

      await page.goto(path);
      // Generous: the first hit on a dev-server route pays an on-demand
      // compile, which is build latency and not product latency.
      await expect(page.getByTestId(marker)).toBeVisible({ timeout: 60_000 });
      expect(errors, `unhandled errors on ${path}`).toEqual([]);
    });
  }
});

test("login renders for an unauthenticated visitor", async ({ browser }) => {
  // Its own context: storageState would authenticate us straight past /login.
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/, { timeout: 30_000 });
  await expect(page.getByTestId("login-page")).toBeVisible();
  await context.close();
});

test("an unauthenticated API call never reaches the daemon", async ({ playwright }) => {
  const request = await playwright.request.newContext({
    baseURL: test.info().project.use.baseURL,
    // Explicit: the project's storageState would otherwise authenticate us.
    storageState: { cookies: [], origins: [] },
    // Without this the middleware's 307 is FOLLOWED to /login, and the test
    // reads the login page's 200 as a passing API call.
    maxRedirects: 0,
  });

  // `/api/grove/*` is inside the middleware matcher, so the gate that fires
  // first is the redirect — the BFF's own 401 is the second line of defence,
  // reached only once middleware is bypassed. Pin the outer gate.
  const response = await request.get("/api/grove/workspaces");
  expect(response.status()).toBe(307);
  expect(response.headers()["location"]).toContain("/login");

  await request.dispose();
});
