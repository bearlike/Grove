import { test, expect } from "@playwright/test";

// w-grove-1 is the fixtures' active workspace (sessions[0] is "working"), so
// it gets the full steer surface; w-grove-2 is idle, so the fake daemon
// refuses steering with the typed 409 envelope.

test("chat panel renders the latest session's transcript", async ({ page }) => {
  // The chat panel lives in the agent card's Transcript tab — default-active
  // here because w-grove-1 has recorded sessions.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-panel")).toBeVisible();

  // The fake transcript: a continued head, two user prompts, assistant
  // replies, and tool entries grouped into collapsed "N tool calls" rows.
  const messages = page.getByTestId("chat-message");
  await expect(messages.filter({ hasText: "run the tests" })).toBeVisible();
  await expect(
    page.locator('[data-testid="chat-message"][data-role="assistant"]').filter({
      hasText: "All green.",
    }),
  ).toBeVisible();

  // Three single-call runs (each broken by a rendered non-tool row) → three
  // borderless "Used N tools" expanders, collapsed by default; expanding the
  // first reveals its digest row.
  const groups = page.getByTestId("tool-group");
  await expect(groups).toHaveCount(3);
  await expect(groups.first()).toContainText("Used 1 tool");
  await expect(page.getByTestId("chat-tool")).toHaveCount(0);

  await groups.first().getByRole("button", { name: "Used 1 tool" }).click();
  await expect(page.getByTestId("chat-tool").first()).toBeVisible();
  await expect(page.getByTestId("chat-tool").first()).toContainText("Edit");

  // Subagent spawns stay legible in the expanded digest row: the full digest
  // line ("Agent(Explore): map the webapp"), not just a parsed name.
  await groups.nth(2).getByRole("button", { name: "Used 1 tool" }).click();
  const spawn = page.getByTestId("chat-tool").filter({ hasText: "Agent(Explore):" });
  await expect(spawn).toContainText("map the webapp");
});

test("a long user prompt clamps behind Show more and expands (#127)", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  const long = page.getByTestId("chat-message").filter({ hasText: "requirements dump" });
  const clamp = long.getByTestId("user-message-collapse");

  // Collapsed by default at the 6-line cap (9.6rem = 153.6px at text-prose 16px)…
  await expect(clamp).toHaveAttribute("data-collapsed", "true");
  await expect.poll(() => clamp.evaluate((el) => el.clientHeight)).toBeLessThanOrEqual(156);
  // …with genuinely hidden text (guard against a vacuous pass: the fixture
  // paste really overflows the clamp in every project viewport).
  expect(await clamp.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeGreaterThan(0);

  // Expand: full prompt shows, seam flips, label swaps.
  const toggle = long.getByTestId("user-message-toggle");
  await expect(toggle).toHaveText(/Show more/);
  await toggle.click();
  await expect(clamp).toHaveAttribute("data-collapsed", "false");
  await expect.poll(() => clamp.evaluate((el) => el.clientHeight)).toBeGreaterThan(156);
  await expect(toggle).toHaveText(/Show less/);

  // Re-collapse round-trip.
  await toggle.click();
  await expect(clamp).toHaveAttribute("data-collapsed", "true");
  await expect.poll(() => clamp.evaluate((el) => el.clientHeight)).toBeLessThanOrEqual(156);

  // Short prompts never grow a toggle.
  const short = page.getByTestId("chat-message").filter({ hasText: "iterate 1" });
  await expect(short.getByTestId("user-message-toggle")).toHaveCount(0);
});

test("a background-task notification renders as a quiet expandable row", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  const note = page.getByTestId("chat-notification").first();
  await expect(note).toBeVisible();
  await expect(note).toContainText("Background task completed: Explore");
  // The subagent's full result stays behind the disclosure…
  await expect(note).not.toContainText("BFF proxy");
  await note.getByRole("button").click();
  await expect(note).toContainText("BFF proxy");
});

test("send hits the BFF and the optimistic user row appears", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  const composer = page.getByTestId("chat-composer");
  await expect(composer).toBeVisible();

  const posted = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().includes("/api/grove/workspaces/w-grove-1/message"),
  );
  await composer.getByRole("textbox").fill("ship the panel");
  await composer.getByRole("button", { name: "Send message" }).click();

  const req = await posted;
  expect(req.postDataJSON()).toEqual({ text: "ship the panel" });

  // Optimistic: the sent text shows as a user bubble before any refetch.
  await expect(
    page.getByTestId("chat-message").filter({ hasText: "ship the panel" }),
  ).toBeVisible();
  await expect(page.getByTestId("chat-notice")).toHaveCount(0);
});

test("interrupt is WORKING-gated and POSTs through the BFF", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  const stop = page.getByTestId("chat-interrupt");
  await expect(stop).toBeVisible();

  const posted = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().includes("/api/grove/workspaces/w-grove-1/interrupt"),
  );
  await stop.click();
  await posted;
  await expect(page.getByTestId("chat-notice")).toHaveCount(0);

  // The idle workspace's head session is not working — no interrupt affordance.
  await page.goto("/w/w-grove-2");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("chat-interrupt")).toHaveCount(0);
});

test("the session rail pins a chosen session as the workspace's primary (#121/#132, rehomed to the rail #140)", async ({
  page,
}) => {
  // Session management lives in the persistent session rail, not the header
  // popover. Rail listing + `?s=` transcript switching + inert metadata rows
  // are covered in sidebar.spec.ts; this owns the one flow unique to the
  // picker — "make primary", the durable remap.
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-panel")).toBeVisible();

  // The rail is a persistent column on desktop, a Sheet on mobile — open the
  // drawer when the hamburger is present. The rail mounts in BOTH the (CSS-
  // hidden below lg) desktop aside AND the mobile Sheet, so match the VISIBLE
  // copy (`:visible`) to keep the row locator unique on both projects.
  const trigger = page.getByTestId("sidebar-trigger");
  if (await trigger.isVisible().catch(() => false)) await trigger.click();

  const row = page.locator(
    '[data-testid="session-rail-row"][data-session-id="s-w-grove-1"]:visible',
  );
  await expect(row).toBeVisible();

  // "Make primary" from the row overflow durably repins the daemon's tracked
  // session — a remap POST carrying the row's ref (confirmation arrives over the
  // activity stream, never the response body — see useRemapSession).
  const posted = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/api/grove/workspaces/w-grove-1/session"),
  );
  await row.getByTestId("session-rail-row-menu").click();
  await page.getByTestId("session-rail-make-primary").click();
  const req = await posted;
  expect(req.postDataJSON()).toEqual({ session_ref: "s-w-grove-1" });
});

test("a steering refusal renders as a quiet inline notice, not a crash", async ({ page }) => {
  await page.goto("/w/w-grove-2");
  const composer = page.getByTestId("chat-composer");
  await expect(composer).toBeVisible();

  await composer.getByRole("textbox").fill("are you there?");
  await composer.getByRole("button", { name: "Send message" }).click();

  const notice = page.getByTestId("chat-notice");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("Steering unavailable");
  // The page survives — no white-screen, the panel is still interactive.
  await expect(page.getByTestId("chat-panel")).toBeVisible();
});
