/**
 * Auth setup project: performs the cookie → BFF → bearer pairing flow
 * once and saves the resulting HttpOnly `grove_session` cookie as Playwright
 * storageState, so every spec starts authenticated and never lands on /login.
 *
 * Goes through the BFF (`/api/auth/pair*`) — the same path the login page
 * takes — so the handshake itself stays covered, and the daemon token never
 * appears here (the BFF strips it and sets the cookie server-side). Against
 * the fake daemon the first poll consumes (auto-approve); against a live
 * daemon (E2E_LIVE_DAEMON=1) the poll loop leaves time to run
 * `grove auth approve` in another terminal.
 */
import { expect, test as setup } from "@playwright/test";
import { STORAGE_STATE } from "../../playwright.config";

setup("pair device and persist storageState", async ({ request }) => {
  setup.setTimeout(90_000); // live mode: leave room for a human approve

  const init = await request.post("/api/auth/pair", {
    data: { label: "playwright-e2e" },
  });
  expect(init.ok()).toBeTruthy();
  const challenge = (await init.json()) as { challenge_id: string; code: string };

  if (process.env.E2E_LIVE_DAEMON === "1") {
    console.log(
      `live pairing pending — approve code ${challenge.code} via \`grove auth approve\``,
    );
  }

  const deadline = Date.now() + 60_000;
  let state = "pending";
  for (;;) {
    const poll = await request.get(`/api/auth/pair/${challenge.challenge_id}`);
    expect(poll.ok()).toBeTruthy();
    state = ((await poll.json()) as { state: string }).state;
    if (state === "consumed") break;
    // Denied / expired are terminal — fail fast instead of polling them out.
    expect(["pending", "approved"]).toContain(state);
    expect(Date.now()).toBeLessThan(deadline);
    await new Promise((r) => setTimeout(r, 1_000));
  }

  // The consume poll set the HttpOnly cookie on this request context.
  await request.storageState({ path: STORAGE_STATE });
});
