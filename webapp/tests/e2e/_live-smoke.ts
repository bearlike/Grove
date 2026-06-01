// Live smoke: drives headless chromium against a running webapp + daemon.
// Useful for autonomous verification on hosts without an X server. Not
// part of the test:e2e run — invoked manually as `npx tsx tests/e2e/_live-smoke.ts`.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const BASE = process.env.LIVE_SMOKE_BASE ?? "http://127.0.0.1:3201";
const OUT = path.resolve(process.cwd(), "test-results", "live-smoke");

async function snap(label: string, fn: (page: import("@playwright/test").Page) => Promise<void>) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await fn(page);
  await page.screenshot({ path: path.join(OUT, `${label}.png`), fullPage: true });
  await browser.close();
  console.log(`✓ ${label}`);
}

async function main() {
  await mkdir(OUT, { recursive: true });

  // 1. Home, desktop
  await snap("home-desktop", async (page) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
  });

  // 2. Home, mobile
  await snap("home-mobile", async (page) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 15_000 });
  });

  // 3. Detail of the workspace with the most commits since fork — gives
  //    the smoke screenshots a real comprehensive history to render.
  const list = await fetch(`${BASE}/api/grove/workspaces`).then((r) => r.json());
  let id: string | undefined;
  if (Array.isArray(list) && list.length > 0) {
    let best: { id: string; n: number } | null = null;
    for (const w of list) {
      const commits = await fetch(`${BASE}/api/grove/workspaces/${w.id}/commits`).then((r) => r.json());
      const n = Array.isArray(commits) ? commits.length : 0;
      if (!best || n > best.n) best = { id: w.id, n };
    }
    id = best?.id;
  }
  if (id) {
    await snap("detail-desktop", async (page) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.goto(`${BASE}/w/${id}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 });
    });
    await snap("detail-mobile", async (page) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(`${BASE}/w/${id}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 });
    });
  } else {
    console.log("(skipping detail screenshots — no workspaces returned by daemon)");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
