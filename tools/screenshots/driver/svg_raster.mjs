// Rasterize Textual's SVG terminal captures into PNGs the framer can composite.
//
// Driven by tools/screenshots/driver/raster.py, which hands over a JSON job
// list on SOURCES. Each job is {src, out}: one `.svg` in, one `.png` out.
//
// WHY A BROWSER AND NOT IMAGEMAGICK. ImageMagick's rsvg delegate renders these
// files badly — it drops cell background fills, which is most of the TUI (the
// peek rail, every status colour, the selected row) and leaves stray titles
// behind. Chromium is the renderer the docs site itself uses on these exact
// files, so it is the only one whose output we can reason about.
//
// WHY THE MARKUP IS INLINED RATHER THAN LOADED THROUGH <img>. An SVG referenced
// by an <img> is a sandboxed, resource-restricted document in Chromium:
// external resources are blocked, and these captures pull Fira Code from a CDN
// via @font-face. Through an <img> the webfont silently never loads and every
// glyph falls back. Inlined into the host document it is an ordinary
// same-document font load and it resolves.
//
// The fallback is not a layout hazard either way — Rich writes every run with
// an explicit `textLength`, so glyphs are fitted to the cell grid whatever font
// wins — but it IS a legibility difference, so the run reports which font it
// actually got instead of assuming.

import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const WORKTREE = process.env.WORKTREE;
const SOURCES = process.env.SOURCES;
const SCALE = Number(process.env.SCALE ?? "2");

if (!WORKTREE || !SOURCES) {
  throw new Error("WORKTREE and SOURCES env vars are required");
}

const jobs = JSON.parse(SOURCES);
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(WORKTREE, "webapp", "node_modules", "@playwright", "test"));

/**
 * Wait for webfonts to settle, then report whether the one we wanted arrived.
 *
 * `document.fonts.ready` resolves on settle, INCLUDING on failure, so it proves
 * only that nothing is still in flight. `check()` is the question that has an
 * answer worth acting on: an offline or CDN-blocked run still produces a
 * perfectly usable PNG, just in a fallback mono face, and the operator should
 * know which one they committed.
 */
async function fontState(page) {
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(() => document.fonts.check('20px "Fira Code"'));
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ deviceScaleFactor: SCALE });
  const page = await ctx.newPage();

  let fellBack = 0;
  for (const { src, out } of jobs) {
    const markup = readFileSync(src, "utf8");
    // `background: transparent` + omitBackground keeps everything outside the
    // window's own rounded rect unpainted. The framer drops that alpha to black
    // and then cuts those pixels away under its own, larger corner radius, so
    // the transparency never reaches the published image.
    await page.setContent(
      `<!doctype html><html><body style="margin:0;background:transparent">${markup}</body></html>`,
      { waitUntil: "load" },
    );
    if (!(await fontState(page))) fellBack += 1;

    const svg = page.locator("svg").first();
    await svg.waitFor();
    mkdirSync(path.dirname(out), { recursive: true });
    await svg.screenshot({ path: out, omitBackground: true });
  }

  await ctx.close();
  await browser.close();

  if (fellBack > 0) {
    console.error(`Fira Code unavailable for ${fellBack}/${jobs.length} capture(s); used a fallback mono face`);
  }
  console.log(`rasterized ${jobs.length} SVG capture(s) at ${SCALE}x`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
