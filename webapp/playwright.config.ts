import { defineConfig, devices } from "@playwright/test";

const useLive = process.env.E2E_LIVE_DAEMON === "1";
const fakePort = 8421;
// 3100 is a common Next.js conflict port (one machine here had a root
// next-server already bound). 3101 keeps tests hermetic.
const webPort = 3101;
const daemonUrl = useLive ? "http://127.0.0.1:7421" : `http://127.0.0.1:${fakePort}`;

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
  env: { GROVE_DAEMON_URL: daemonUrl },
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
    { name: "mobile-chrome", use: { ...devices["Pixel 5"] } },
    { name: "desktop-chrome", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } },
  ],
  webServer: useLive ? webServer : [fakeServer, webServer],
});
