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
 * The whole catalog — builtin tools and the MCP servers it recognizes — is small
 * enough that shipping it is cheaper than fetching it. Bundling it removes the third-party dependency from first paint entirely, so
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

import { UNKNOWN_SERVER_ICON, UNKNOWN_TOOL_ICON } from "../lib/grove/adapters/tool-catalog";

const CATALOG = "lib/grove/adapters/tool-catalog.json";
const BUNDLE = "lib/grove/adapters/tool-icon-bundle.json";

/** Slugs Grove renders that no catalog entry names: the two generic fallbacks. */
const EXTRA_SLUGS = [UNKNOWN_TOOL_ICON, UNKNOWN_SERVER_ICON];

/**
 * Grove's own mark is no public icon set's, so it is its own prefix, read from
 * the artwork of record rather than a copy that could drift from it. The logo
 * is one `<svg>` whose children are the whole picture — attributes included,
 * since its `fill-rule` is load-bearing geometry (see `brand-mark.test.tsx`).
 */
const LOCAL_SETS: Record<string, { file: string; name: string }> = {
  grove: { file: "../docs/img/grove-logo.svg", name: "grove" },
};

async function localSet(root: string, prefix: string): Promise<IconifyJSON> {
  const { file, name } = LOCAL_SETS[prefix];
  const svg = await readFile(join(root, file), "utf8");
  const viewBox = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const body = svg.match(/<svg[^>]*>([^]*)<\/svg>/)?.[1].trim();
  if (!viewBox || !body) throw new Error(`${file}: expected one <svg> with a viewBox`);
  return { prefix, icons: { [name]: { body } }, width: Number(viewBox[1]), height: Number(viewBox[2]) };
}

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
  // A mark that names no paint at all falls back to SVG's default black, which
  // disappears on a dark coin and a dark row (`logos:devin` ships this way).
  // Monochrome sets say `currentColor` for exactly this reason, so an unpainted
  // body is given the same instruction rather than a colour of its own.
  for (const icon of Object.values(data.icons) as { body: string }[]) {
    if (!/fill=|stroke=|currentColor/.test(icon.body)) icon.body = `<g fill="currentColor">${icon.body}</g>`;
  }
  return data;
}

export async function buildBundle(root: string): Promise<Record<string, IconifyJSON>> {
  const catalog = JSON.parse(await readFile(join(root, CATALOG), "utf8")) as {
    tools: { icon: string }[];
    servers: { icon: string }[];
  };
  const slugs = new Set([...catalog.tools, ...catalog.servers].map((entry) => entry.icon).concat(EXTRA_SLUGS));

  const byPrefix = new Map<string, string[]>();
  for (const slug of slugs) {
    const [prefix, name] = slug.split(":");
    if (!prefix || !name) throw new Error(`Not a prefix:name slug: ${slug}`);
    byPrefix.set(prefix, [...(byPrefix.get(prefix) ?? []), name]);
  }

  const bundle: Record<string, IconifyJSON> = {};
  for (const prefix of [...byPrefix.keys()].sort()) {
    bundle[prefix] = Object.hasOwn(LOCAL_SETS, prefix)
      ? await localSet(root, prefix)
      : await fetchPrefix(prefix, byPrefix.get(prefix) ?? []);
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
