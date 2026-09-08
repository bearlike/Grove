/**
 * Freeze the tool catalog's icon artwork into a committed bundle.
 *
 * WHY this exists: `AppIcon` renders Iconify slugs, and Iconify's online loader
 * fetches the artwork from `api.iconify.design` at runtime. That is correct for
 * an arbitrary slug — a user's supplementary MCP mapping can name anything —
 * but the BUILT-IN catalog is a closed set this repo already owns, and measured
 * on the deployed app 2026-09-15 it cost three cross-origin round trips to a
 * third party on EVERY page load, with no HTTP caching surviving a reload.
 *
 * The whole catalog is small enough that shipping it is cheaper than fetching
 * it: 30 distinct slugs across 4 prefixes, ~17 KB of raw JSON before compression.
 * Bundling it removes the third-party dependency from first paint entirely, so
 * a timeline's marks are present in the first render rather than swapping in
 * after a network hop — and a reader on a slow link, an offline laptop, or a
 * network that blocks the API sees real icons instead of a page of fallbacks.
 *
 * DELIBERATELY A BUILD SCRIPT, NOT A RUNTIME FETCH. The output is committed and
 * diffable, so an icon changing upstream is a reviewable event rather than
 * something that happens to a user mid-session — the same argument
 * `registry.lock.json` makes for vendored components. Re-run it after editing
 * `tool-catalog.json`; `icon-bundle:check` fails the gate if the two disagree.
 */

import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const CATALOG = "lib/grove/adapters/tool-catalog.json";
const BUNDLE = "lib/grove/adapters/tool-icon-bundle.json";

/** Slugs Grove renders that no catalog entry names: the two generic fallbacks
 * (an unknown tool, an unmapped MCP server) and the agent brand mark. */
const EXTRA_SLUGS = ["flat-color-icons:services", "flat-color-icons:settings"];

interface IconifyJSON {
  prefix: string;
  icons: Record<string, unknown>;
  aliases?: Record<string, unknown>;
  width?: number;
  height?: number;
  [key: string]: unknown;
}

async function fetchPrefix(prefix: string, names: string[]): Promise<IconifyJSON> {
  const url = `https://api.iconify.design/${prefix}.json?icons=${[...names].sort().join(",")}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText} for ${url}`);
  const data = (await response.json()) as IconifyJSON & { not_found?: string[] };
  // A silently-absent icon would ship a bundle that renders fallbacks forever,
  // which looks exactly like a slug typo at the call site. Fail the build.
  if (data.not_found?.length) {
    throw new Error(`${prefix}: no such icon(s): ${data.not_found.join(", ")}`);
  }
  const missing = names.filter((name) => !(name in (data.icons ?? {})));
  if (missing.length) throw new Error(`${prefix}: missing after fetch: ${missing.join(", ")}`);
  return data;
}

export async function buildBundle(root: string): Promise<Record<string, IconifyJSON>> {
  const catalog = JSON.parse(await readFile(join(root, CATALOG), "utf8")) as {
    tools: { icon: string }[];
  };
  const slugs = new Set([...catalog.tools.map((tool) => tool.icon), ...EXTRA_SLUGS]);

  const byPrefix = new Map<string, string[]>();
  for (const slug of slugs) {
    const [prefix, name] = slug.split(":");
    if (!prefix || !name) throw new Error(`Not a prefix:name slug: ${slug}`);
    byPrefix.set(prefix, [...(byPrefix.get(prefix) ?? []), name]);
  }

  const bundle: Record<string, IconifyJSON> = {};
  for (const prefix of [...byPrefix.keys()].sort()) {
    bundle[prefix] = await fetchPrefix(prefix, byPrefix.get(prefix) ?? []);
  }
  return bundle;
}

async function main(): Promise<void> {
  const root = process.cwd();
  const check = process.argv.includes("--check");
  const bundle = await buildBundle(root);
  const serialized = `${JSON.stringify(bundle, null, 2)}\n`;

  if (check) {
    const onDisk = await readFile(join(root, BUNDLE), "utf8").catch(() => "");
    if (onDisk !== serialized) {
      console.error(
        `\n✗ ${BUNDLE} is stale.\n` +
          "  The catalog names icons the bundle does not carry (or carries differently).\n" +
          "  Run `npm run icon-bundle` and commit the result.",
      );
      process.exit(1);
    }
    const count = Object.values(bundle).reduce((n, set) => n + Object.keys(set.icons).length, 0);
    console.log(`✓ icon-bundle:check — ${count} icons across ${Object.keys(bundle).length} prefixes.`);
    return;
  }

  await writeFile(join(root, BUNDLE), serialized, "utf8");
  const count = Object.values(bundle).reduce((n, set) => n + Object.keys(set.icons).length, 0);
  console.log(`✓ icon-bundle — wrote ${count} icons across ${Object.keys(bundle).length} prefixes.`);
}

main().catch((error: unknown) => {
  console.error(`icon-bundle failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
