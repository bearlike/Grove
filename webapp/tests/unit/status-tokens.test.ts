import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { STATUS_HEX_DARK, STATUS_GLYPH, STATUS_LABEL } from "@/lib/grove/status-tokens";

const PYTHON_SOURCE = path.resolve(
  __dirname,
  "../../../src/grove/core/contracts/status_palette.py",
);

function pythonHex(varName: string, source: string): string {
  // Match e.g. `_DARK_ACTIVE: Final = "#84cc16"` — whitespace-tolerant.
  const re = new RegExp(`${varName}\\s*:\\s*Final\\s*=\\s*"([#0-9a-fA-F]+)"`);
  const m = source.match(re);
  if (!m) throw new Error(`Could not find ${varName} in ${PYTHON_SOURCE}`);
  return m[1].toLowerCase();
}

describe("status hex parity with grove.core.contracts.status_palette", () => {
  const py = readFileSync(PYTHON_SOURCE, "utf8");

  it.each([
    ["_DARK_ACTIVE", "active"],
    ["_DARK_RUNNING", "running"],
    ["_DARK_IDLE", "idle"],
    ["_DARK_OFFLINE", "offline"],
    ["_DARK_PAUSED", "paused"],
    ["_DARK_ORPHANED", "orphaned"],
    ["_DARK_ERROR", "error"],
    ["_DARK_PROVISIONING", "provisioning"],
  ])("%s matches STATUS_HEX_DARK[%s]", (pyName, key) => {
    // Two Python names are ALIASES of another literal rather than literals
    // themselves (`_DARK_RUNNING = _DARK_ACTIVE`, `_DARK_PROVISIONING =
    // _DARK_IDLE`), so the hex regex has nothing to match on them — resolve
    // each to the literal it points at. If an alias is ever given a hex of its
    // own, delete its entry here and the direct match takes over.
    const ALIASES: Record<string, string> = {
      _DARK_RUNNING: "_DARK_ACTIVE",
      _DARK_PROVISIONING: "_DARK_IDLE",
    };
    const target = ALIASES[pyName] ?? pyName;
    expect(STATUS_HEX_DARK[key as keyof typeof STATUS_HEX_DARK].toLowerCase()).toBe(
      pythonHex(target, py),
    );
  });
});

describe("status glyph + label maps", () => {
  it("covers every WorkspaceStatus value", () => {
    const expected = [
      "active",
      "running",
      "idle",
      "offline",
      "paused",
      "orphaned",
      "error",
      "provisioning",
    ];
    for (const s of expected) {
      expect(STATUS_GLYPH[s as keyof typeof STATUS_GLYPH]).toBeDefined();
      expect(STATUS_LABEL[s as keyof typeof STATUS_LABEL]).toBeDefined();
    }
  });
});
