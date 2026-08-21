/**
 * Verify every vendored component against the version we deliberately adopted.
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
 * TWO ORACLES, AND THE DEFAULT IS THE PINNED ONE. The upstream registry is
 * UNVERSIONED — `r.assistant-ui.com/<item>.json` always serves current HEAD —
 * so checking against it directly makes this gate fail on days nobody touched
 * the repo, purely because upstream shipped. That is indistinguishable from a
 * real hand-edit, which is the failure the gate exists to catch, and a gate
 * that cries wolf gets ignored or deleted. So:
 *
 *   default      diff disk against `registry.lock.json`, the hash of what we
 *                adopted. Offline, deterministic, and it still catches every
 *                hand-edit — which is the stated purpose.
 *   --upstream   diff disk against the live registry. Answers "is there an
 *                upgrade waiting", and is advisory rather than a gate.
 *   --refresh    same fetch, but rewrite the lockfile. This is the deliberate
 *                upgrade: re-add the components, then run this, and the new
 *                hashes land in the same commit as the new files.
 *
 * The lockfile is what makes an upgrade a reviewable event instead of a thing
 * that happens to you.
 */

import { readdirSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

/** Directories whose contents are upstream source we merely store. */
const VENDORED_DIRS = ["components/assistant-ui", "components/elements", "components/ui", "components/icons"] as const;

/** The adopted-version record. Committed, and only ever moved by `--refresh`. */
const BASELINE_PATH = "registry.lock.json";

interface BaselineEntry {
  sha256: string;
  item: string;
  source: Source;
}
type Baseline = Record<string, BaselineEntry>;

const sha256 = (content: string): string => createHash("sha256").update(content, "utf8").digest("hex");

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

/**
 * The default gate: disk against the adopted hashes. No network.
 *
 * Everything it can catch — a hand-edit, a deleted vendored file, a
 * hand-written file smuggled into a vendored directory — is a fact about THIS
 * repository, so none of it needs the registry to adjudicate.
 */
function checkBaseline(root: string): void {
  const path = join(root, BASELINE_PATH);
  if (!existsSync(path)) {
    console.error(
      `registry:check — no ${BASELINE_PATH}. Run \`npm run registry:refresh\` to adopt\n` +
        "  the current upstream and write the baseline.",
    );
    process.exit(1);
  }
  const baseline = JSON.parse(readFileSync(path, "utf8")) as Baseline;
  const onDisk = scanVendoredTree(root);

  const edited: string[] = [];
  const unverifiable: string[] = [];
  for (const file of onDisk) {
    const held = baseline[file];
    if (!held) {
      unverifiable.push(file);
      continue;
    }
    if (sha256(readFileSync(join(root, file), "utf8")) !== held.sha256) {
      edited.push(`${file}  (${held.source} item "${held.item}")`);
    }
  }
  // A baselined file that left the tree is drift too, in the other direction.
  const absent = Object.keys(baseline).filter((p) => !existsSync(join(root, p)));

  if (edited.length) {
    console.error(`\n✗ ${edited.length} vendored file(s) differ from the adopted version:`);
    for (const e of edited) console.error(`    ${e}`);
    console.error(
      "\n  Vendored components are upstream source. Restore the file (`git checkout`)\n" +
        "  and move the change into components/grove/ composition instead. To adopt a\n" +
        "  NEW upstream on purpose, re-add the item then `npm run registry:refresh`.",
    );
  }
  if (unverifiable.length) {
    console.error(`\n✗ ${unverifiable.length} file(s) in a vendored directory are not in the baseline:`);
    for (const u of unverifiable) console.error(`    ${u}`);
    console.error(
      "\n  Hand-written components live in components/grove/, never in a vendored tree.\n" +
        "  If this file really is vendored, `npm run registry:refresh` to record it.",
    );
  }
  if (absent.length) {
    console.error(`\n✗ ${absent.length} baselined file(s) are no longer in the tree:`);
    for (const a of absent) console.error(`    ${a}`);
  }

  if (edited.length + unverifiable.length + absent.length > 0) process.exit(1);
  console.log(`✓ registry:check — all ${onDisk.length} vendored files match the adopted version.`);
}

async function checkUpstream(root: string, write: boolean): Promise<void> {
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
  const adopted: Baseline = {};

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
      // Hash what is ON DISK, never the registry's copy: --refresh records the
      // version we actually adopted, and those differ whenever a file is
      // drifted. Recording upstream's hash would write a baseline the tree does
      // not satisfy, and the next plain run would fail on a file nobody touched.
      adopted[path] = { sha256: sha256(readFileSync(abs, "utf8")), item: name, source };
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

  // Refuse to write a baseline off an incomplete read. `missing`/`unverifiable`
  // /`unchecked` all mean some vendored file never got a hash, and a partial
  // lockfile silently un-gates exactly the files it omits.
  if (write) {
    if (missing.length + unverifiable.length + unchecked.length > 0) {
      console.error("\n✗ refusing to write the baseline from an incomplete read — fix the above first.");
      process.exit(1);
    }
    const ordered = Object.fromEntries(Object.keys(adopted).sort().map((k) => [k, adopted[k]]));
    writeFileSync(join(root, BASELINE_PATH), `${JSON.stringify(ordered, null, 2)}\n`, "utf8");
    console.log(
      `✓ registry:refresh — adopted ${Object.keys(ordered).length} vendored files into ${BASELINE_PATH}.` +
        (drifted.length ? `\n  ${drifted.length} of them differ from upstream and were adopted AS THEY ARE ON DISK.` : ""),
    );
    return;
  }

  if (failed) process.exit(1);
  console.log(`✓ registry:check — all ${seen.size} vendored files match upstream (${wanted.size} registry items).`);
}

async function main(): Promise<void> {
  const root = process.cwd();
  const argv = process.argv.slice(2);
  const refresh = argv.includes("--refresh");
  if (refresh || argv.includes("--upstream")) return checkUpstream(root, refresh);
  checkBaseline(root);
}

main().catch((err: unknown) => {
  console.error(`registry:check failed: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
