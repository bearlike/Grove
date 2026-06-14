import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const useLive = process.env.E2E_LIVE_DAEMON === "1";
const fakePort = 8421;
// 3100 is a common Next.js conflict port (one machine here had a root
// next-server already bound). 3101 keeps tests hermetic.
const webPort = 3101;
const daemonUrl = useLive ? "http://127.0.0.1:7421" : `http://127.0.0.1:${fakePort}`;

// Written once by the `setup` project (tests/e2e/auth.setup.ts) after pairing;
// both browser projects start from it so every spec is authenticated.
export const STORAGE_STATE = path.join(__dirname, "tests/e2e/.auth/storage-state.json");

const fakeServer = {
  command: `npx tsx tests/e2e/_run-fake-daemon.ts`,
  url: `http://127.0.0.1:${fakePort}/healthz`,
  reuseExistingServer: false,
  timeout: 20_000,
  env: { FAKE_DAEMON_PORT: String(fakePort) },
};

const webServer = {
  command: `npm run dev -- --port ${webPort}`,
  url: `http://127.0.0.1:${webPort}`,
  reuseExistingServer: false,
  env: {
    GROVE_DAEMON_URL: daemonUrl,
    // Hermetic cookie store: the BFF persists its grove_session ↔ bearer map
    // under $XDG_CONFIG_HOME/grove/webapp-sessions.json — point it at the
    // gitignored test-results dir so runs never touch the real ~/.config.
    XDG_CONFIG_HOME: path.join(__dirname, "test-results", "xdg-config"),
  },
  timeout: 90_000,
};

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 5"], storageState: STORAGE_STATE },
      dependencies: ["setup"],
    },
    {
      name: "desktop-chrome",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 900 },
        storageState: STORAGE_STATE,
      },
      dependencies: ["setup"],
    },
  ],
  webServer: useLive ? webServer : [fakeServer, webServer],
});
