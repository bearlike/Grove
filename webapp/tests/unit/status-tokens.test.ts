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
  ])("%s matches STATUS_HEX_DARK[%s]", (pyName, key) => {
    // _DARK_RUNNING is aliased to _DARK_ACTIVE in Python; resolve via _DARK_ACTIVE.
    const target = pyName === "_DARK_RUNNING" ? "_DARK_ACTIVE" : pyName;
    expect(STATUS_HEX_DARK[key as keyof typeof STATUS_HEX_DARK].toLowerCase()).toBe(
      pythonHex(target, py),
    );
  });
});

describe("status glyph + label maps", () => {
  it("covers every WorkspaceStatus value", () => {
    const expected = ["active", "running", "idle", "offline", "paused", "orphaned", "error"];
    for (const s of expected) {
      expect(STATUS_GLYPH[s as keyof typeof STATUS_GLYPH]).toBeDefined();
      expect(STATUS_LABEL[s as keyof typeof STATUS_LABEL]).toBeDefined();
    }
  });
});
