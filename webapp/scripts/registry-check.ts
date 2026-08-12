/**
 * Re-fetch every vendored registry component and diff it against the tree.
 *
 * WHY this exists: `webapp/CLAUDE.md` states "vendored verbatim, never
 * hand-edited" as prose, and prose rots silently. A hand-tweaked colour in
 * `components/elements/*` forfeits every upstream fix from then on and nothing
 * in the build notices. This is the thing that notices.
 *
 * The vendored set is DERIVED from disk, never hand-listed: a name list would
 * go stale the first time someone adds a component and forgets the list.
 * Every file under the four vendored directories must resolve to a registry
 * item, or it fails as unverifiable — a hand-written file in a vendored tree is
 * itself the violation.
 *
 * Requires network. That is the point: the upstream registry is the oracle.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

/** Directories whose contents are upstream source we merely store. */
const VENDORED_DIRS = ["components/assistant-ui", "components/elements", "components/ui", "components/icons"] as const;

const AUI_INDEX = "https://r.assistant-ui.com/registry.json";
const AUI_ITEM = (name: string): string => `https://r.assistant-ui.com/${name}.json`;

/**
 * shadcn serves per-style bundles. `new-york-v4` is what the CLI resolves for
 * this project (components.json: style "new-york", tailwind v4, cssVariables),
 * and that bundle already carries `@/components/...` imports — so contents
 * compare byte-for-byte with no alias rewriting.
 */
const SHADCN_ITEM = (name: string): string => `https://ui.shadcn.com/r/styles/new-york-v4/${name}.json`;

interface RegistryFile {
  path: string;
  content?: string;
}

/**
 * Where a registry item's file actually lands in this project.
 *
 * assistant-ui declares install paths directly. shadcn declares its own
 * internal layout (`registry/new-york-v4/ui/button.tsx`) and the CLI rewrites
 * it on install — so a naive path comparison silently matches nothing and the
 * check passes while verifying zero shadcn files. The coverage assertion below
 * is what makes that failure mode loud.
 */
function installPath(declared: string): string {
  const shadcn = declared.match(/^registry\/[^/]+\/(.+)$/);
  if (!shadcn) return declared;
  const rest = shadcn[1];
  return rest.startsWith("ui/") ? `components/${rest}` : rest;
}

interface RegistryItem {
  name: string;
  files?: RegistryFile[];
}

interface RegistryIndex {
  items: RegistryItem[];
}

type Source = "assistant-ui" | "shadcn";

/**
 * The shadcn CLI rewrites the registry's own `@/registry/<style>/…` imports to
 * the project aliases as it writes each file, so raw registry content never
 * matches disk for any component that imports a sibling. Replaying the rewrite
 * is what makes the diff meaningful instead of uniformly red.
 */
function normalizeImports(content: string): string {
  return content.replace(/@\/(registry\/[^/]+\/[^"'`\s]+)/g, (_, p: string) => `@/${installPath(p)}`);
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${url}`);
  return (await res.json()) as T;
}

/** Every file currently sitting in a vendored directory, project-relative. */
function scanVendoredTree(root: string): string[] {
  const out: string[] = [];
  for (const dir of VENDORED_DIRS) {
    const abs = join(root, dir);
    if (!existsSync(abs)) continue;
    for (const entry of readdirSync(abs, { withFileTypes: true })) {
      if (entry.isFile()) out.push(`${dir}/${entry.name}`);
    }
  }
  return out.sort();
}

/** Bounded-concurrency map: 60-odd item fetches, politely. */
async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (next < items.length) {
        const i = next++;
        results[i] = await fn(items[i]);
      }
    }),
  );
  return results;
}

async function main(): Promise<void> {
  const root = process.cwd();
  const onDisk = scanVendoredTree(root);

  const index = await fetchJson<RegistryIndex>(AUI_INDEX);

  // Declared path → owning item. The assistant-ui index declares the path each
  // file BELONGS at, which is why the elements-surfaces / elements-range
  // relocation is not drift: the manifest says `components/elements/`, and only
  // shadcn's `registry:lib` alias routing disagrees. We follow the manifest.
  //
  // A path can be claimed twice, because whole-app templates (`eve-chat`, 14
  // files) re-ship the same components the single-file items own. Fewest-files
  // wins picks the canonical item; the template's copy is a snapshot that
  // drifts from it.
  const auiOwner = new Map<string, { name: string; fileCount: number }>();
  for (const item of index.items) {
    const fileCount = (item.files ?? []).length;
    for (const file of item.files ?? []) {
      const held = auiOwner.get(file.path);
      if (!held || fileCount < held.fileCount) auiOwner.set(file.path, { name: item.name, fileCount });
    }
  }

  // Keyed by source AND name: `badge`, `select` and `tabs` exist in both
  // registries as different components, and a name-only key silently drops one.
  const wanted = new Map<string, { name: string; source: Source }>();
  const want = (name: string, source: Source): void => void wanted.set(`${source}:${name}`, { name, source });
  const unverifiable: string[] = [];
  for (const path of onDisk) {
    // `components/ui/` is shadcn's namespace by definition. assistant-ui
    // templates also vendor copies of those primitives, but the CLI that
    // installed them here was shadcn's, so shadcn is the oracle.
    if (path.startsWith("components/ui/")) {
      want(path.slice("components/ui/".length).replace(/\.tsx?$/, ""), "shadcn");
      continue;
    }
    const aui = auiOwner.get(path);
    if (aui) want(aui.name, "assistant-ui");
    else unverifiable.push(path);
  }

  const fetched = await mapLimit([...wanted.values()], 8, async ({ name, source }) => {
    const url = source === "assistant-ui" ? AUI_ITEM(name) : SHADCN_ITEM(name);
    try {
      return { name, source, item: await fetchJson<RegistryItem>(url) };
    } catch {
      // A name neither registry serves is unverifiable, not absent-and-fine.
      unverifiable.push(`${name}  (no ${source} registry item at ${url})`);
      return { name, source, item: { name, files: [] } satisfies RegistryItem };
    }
  });

  const drifted: string[] = [];
  const missing: string[] = [];
  const seen = new Set<string>();

  for (const { name, source, item } of fetched) {
    for (const file of item.files ?? []) {
      if (file.content === undefined) continue;
      const path = installPath(file.path);
      const abs = join(root, path);
      if (!existsSync(abs)) {
        // Only flag files inside a vendored dir; items legitimately ship
        // examples and app routes we never installed.
        if (VENDORED_DIRS.some((d) => path.startsWith(`${d}/`))) {
          missing.push(`${path}  (declared by ${source} item "${name}")`);
        }
        continue;
      }
      seen.add(path);
      if (readFileSync(abs, "utf8") !== normalizeImports(file.content)) {
        drifted.push(`${path}  (${source} item "${name}")`);
      }
    }
  }

  // A file we resolved to an item but never actually diffed is a hole in the
  // gate, and a hole is indistinguishable from a pass. Fail on it.
  const unchecked = onDisk.filter((p) => !seen.has(p));

  const failed = drifted.length + missing.length + unverifiable.length + unchecked.length > 0;

  if (drifted.length) {
    console.error(`\n✗ ${drifted.length} vendored file(s) differ from the registry:`);
    for (const d of drifted) console.error(`    ${d}`);
    console.error(
      "\n  Vendored components are upstream source. Re-add the item to restore it\n" +
        "  (`npx shadcn@latest add …` / `npx assistant-ui@latest add …`) and move the\n" +
        "  change into components/grove/ composition instead.",
    );
  }
  if (missing.length) {
    console.error(`\n✗ ${missing.length} registry file(s) declared but absent from the tree:`);
    for (const m of missing) console.error(`    ${m}`);
  }
  if (unverifiable.length) {
    console.error(`\n✗ ${unverifiable.length} file(s) in a vendored directory belong to no registry item:`);
    for (const u of unverifiable) console.error(`    ${u}`);
    console.error("\n  Hand-written components live in components/grove/, never in a vendored tree.");
  }

  if (unchecked.length) {
    console.error(`\n✗ ${unchecked.length} vendored file(s) were resolved to an item but never diffed:`);
    for (const u of unchecked) console.error(`    ${u}`);
    console.error("\n  This is a bug in registry-check.ts, not in the tree — its path mapping is wrong.");
  }

  if (failed) process.exit(1);
  console.log(`✓ registry:check — all ${seen.size} vendored files match upstream (${wanted.size} registry items).`);
}

main().catch((err: unknown) => {
  console.error(`registry:check failed: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
