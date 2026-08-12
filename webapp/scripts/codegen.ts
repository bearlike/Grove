/**
 * Regenerate `lib/grove/api/types.gen.ts` from the daemon's OpenAPI, or (with
 * `--check`) fail if the committed file has drifted from it. The daemon's
 * schema is the contract; a stale local copy is a lie the typechecker enforces.
 *
 * THE SCHEMA COMES FROM THIS CHECKOUT, NOT FROM A RUNNING DAEMON, and that is
 * the whole point of the `build_app(...).openapi()` call below. This script used
 * to fetch `http://127.0.0.1:7421/openapi.json`, which answers for whatever code
 * the *installed* daemon booted with — so a contract field added in the working
 * tree came back ABSENT while the script reported success and rewrote a hundred
 * unrelated lines. That is the dangerous shape: the file changes, so it looks
 * like it worked, and `codegen:check` passes green against the stale daemon too.
 * A generator must read the source it is generating from.
 *
 * WHY openapi-typescript IS SPAWNED FROM A NEUTRAL cwd: it peers on
 * `typescript@^5` and dies on this project's TypeScript 7 with
 * `Cannot read properties of undefined (reading 'createKeywordTypeNode')` —
 * TS 7 dropped the `ts.factory` shape it builds nodes with. Running it from the
 * project root always resolves the project's TS, so we spawn it from a neutral
 * cwd against a pinned TS 5. Delete this workaround once openapi-typescript
 * supports TypeScript 7.
 */

import { spawnSync } from "node:child_process";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const TARGET = "lib/grove/api/types.gen.ts";

/**
 * Build the app the same way `grove daemon serve` does, with bare defaults.
 *
 * Defaults rather than the real config cascade because the schema must not
 * depend on the machine generating it — a developer whose `~/.config/grove`
 * enables an extra surface would otherwise commit routes nobody else has.
 */
const SCHEMA_SCRIPT = `
import json
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

print(json.dumps(build_app(cfg=GroveConfig(), store=JsonWorkspaceStore()).openapi()))
`;

function schemaPath(repoRoot: string): string {
  const out = join(tmpdir(), "grove-openapi.json");
  const result = spawnSync("uv", ["run", "python", "-c", SCHEMA_SCRIPT], {
    cwd: repoRoot,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.status !== 0) {
    console.error(result.stderr);
    console.error(`\n✗ codegen — could not build the daemon's OpenAPI from ${repoRoot}.`);
    process.exit(1);
  }
  writeFileSync(out, result.stdout);
  return out;
}

function main(): void {
  const check = process.argv.includes("--check");
  const root = process.cwd();
  const scratch = join(tmpdir(), "webapp-types.gen.ts");
  const schema = schemaPath(dirname(root));

  const result = spawnSync(
    "npx",
    ["-y", "-p", "typescript@5.9", "-p", "openapi-typescript@7", "openapi-typescript", schema, "-o", scratch],
    { cwd: tmpdir(), stdio: "inherit" },
  );
  if (result.status !== 0) {
    console.error(`\n✗ codegen — openapi-typescript failed on ${schema}.`);
    process.exit(1);
  }

  const target = join(root, TARGET);
  if (!check) {
    copyFileSync(scratch, target);
    console.log(`✓ codegen — ${TARGET} regenerated from the daemon source in ${dirname(root)}.`);
    return;
  }

  if (readFileSync(scratch, "utf8") !== readFileSync(target, "utf8")) {
    const diff = spawnSync("diff", ["-u", target, scratch], { encoding: "utf8" });
    console.error(diff.stdout);
    console.error(`\n✗ codegen:check — ${TARGET} has drifted from the daemon's OpenAPI.`);
    console.error(`  Run \`npm run codegen\` and commit the result with the change that caused it.`);
    process.exit(1);
  }
  console.log(`✓ codegen:check — ${TARGET} matches the daemon's OpenAPI.`);
}

main();
