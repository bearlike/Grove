import { chromium } from "@playwright/test";
import path from "node:path";
import { mkdir } from "node:fs/promises";

const BASE = "http://127.0.0.1:3000";
const OUT = path.resolve(process.cwd(), "test-results", "prod-smoke");

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  for (const [label, viewport] of [
    ["mobile", { width: 390, height: 844 }],
    ["desktop", { width: 1280, height: 900 }],
  ] as const) {
    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    await page.goto(BASE, { waitUntil: "networkidle", timeout: 15_000 });
    await page.screenshot({ path: path.join(OUT, `home-${label}.png`), fullPage: true });
    await ctx.close();
    console.log(`✓ home-${label}`);
  }
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
