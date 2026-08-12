import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

/**
 * Ports are a SHARED SINGLETON on a dev host, and `webapp/`'s harness already
 * owns 3101 (web) and 8421 (fake daemon). These are deliberately the next pair
 * up so both suites can run without one silently attaching to the other's
 * server and reporting a mass regression that is really a port collision.
 */
const FAKE_DAEMON_PORT = 8422;
const WEB_PORT = 3102;

const useLive = process.env.E2E_LIVE_DAEMON === "1";
const daemonUrl = useLive ? "http://127.0.0.1:7421" : `http://127.0.0.1:${FAKE_DAEMON_PORT}`;

/** Written once by the `setup` project after pairing; every spec starts from it. */
export const STORAGE_STATE = path.join(import.meta.dirname, "tests/e2e/.auth/storage-state.json");

const fakeServer = {
  command: "npx tsx tests/e2e/_run-fake-daemon.ts",
  url: `http://127.0.0.1:${FAKE_DAEMON_PORT}/healthz`,
  reuseExistingServer: false,
  timeout: 20_000,
  env: { FAKE_DAEMON_PORT: String(FAKE_DAEMON_PORT) },
};

const webServer = {
  command: `npm run dev -- --port ${WEB_PORT}`,
  url: `http://127.0.0.1:${WEB_PORT}/login`,
  reuseExistingServer: false,
  env: {
    GROVE_DAEMON_URL: daemonUrl,
    // Hermetic cookie store: the BFF persists its grove_session ↔ bearer map
    // under $XDG_CONFIG_HOME/grove, so point it at the gitignored test-results
    // dir and a run can never touch the real ~/.config.
    XDG_CONFIG_HOME: path.join(import.meta.dirname, "test-results", "xdg-config"),
  },
  timeout: 120_000,
};

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    // `localhost`, NOT 127.0.0.1: Next 16 refuses to serve dev chunks to a Host
    // outside its default `allowedDevOrigins` (localhost), so a 127.0.0.1
    // origin loads the HTML, blocks every script, and never hydrates — which
    // surfaces as every testid missing rather than as a network error.
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "desktop-chrome",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 }, storageState: STORAGE_STATE },
      dependencies: ["setup"],
    },
  ],
  webServer: useLive ? [webServer] : [fakeServer, webServer],
});
