import { expect, test as setup } from "@playwright/test";

import { STORAGE_STATE } from "../../playwright.config";

/**
 * Runs the cookie → BFF → bearer pairing once and saves the resulting HttpOnly
 * `grove_session` cookie as storageState, so every spec starts authenticated.
 *
 * It goes through the BFF (`/api/auth/pair*`) — the same path the login page
 * takes — so the handshake itself stays covered and the daemon token never
 * appears here (the BFF strips it and sets the cookie server-side). The fake
 * daemon auto-approves on the first poll; against a live daemon
 * (E2E_LIVE_DAEMON=1) the loop leaves time to run `grove auth approve`.
 */
setup("pair device and persist storageState", async ({ request }) => {
  setup.setTimeout(90_000);

  const init = await request.post("/api/auth/pair", { data: { label: "playwright-e2e" } });
  expect(init.ok()).toBeTruthy();
  const challenge = (await init.json()) as { challenge_id: string; code: string };

  if (process.env.E2E_LIVE_DAEMON === "1") {
    console.log(`live pairing pending — approve code ${challenge.code} via \`grove auth approve\``);
  }

  const deadline = Date.now() + 60_000;
  for (;;) {
    const poll = await request.get(`/api/auth/pair/${challenge.challenge_id}`);
    expect(poll.ok()).toBeTruthy();
    const { state } = (await poll.json()) as { state: string };
    if (state === "consumed") break;
    // Denied and expired are terminal — fail fast rather than polling them out.
    expect(["pending", "approved"]).toContain(state);
    expect(Date.now()).toBeLessThan(deadline);
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }

  // The consuming poll set the HttpOnly cookie on this request context.
  await request.storageState({ path: STORAGE_STATE });
});
