import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  AGENT_STATE_HEX_DARK,
  AGENT_STATE_GLYPH,
  AGENT_STATE_LABEL,
} from "@/lib/grove/agent-state-tokens";

const PYTHON_SOURCE = path.resolve(
  __dirname,
  "../../../src/grove/core/contracts/agent_palette.py",
);

function pythonHex(varName: string, source: string): string {
  const re = new RegExp(`${varName}\\s*:\\s*Final\\s*=\\s*"([#0-9a-fA-F]+)"`);
  const m = source.match(re);
  if (!m) throw new Error(`Could not find ${varName} in ${PYTHON_SOURCE}`);
  return m[1].toLowerCase();
}

describe("agent-state hex parity with grove.core.contracts.agent_palette", () => {
  const py = readFileSync(PYTHON_SOURCE, "utf8");

  it.each([
    ["_DARK_STARTING", "starting"],
    ["_DARK_WORKING", "working"],
    ["_DARK_WAITING", "waiting"],
    ["_DARK_BLOCKED", "blocked"],
    ["_DARK_IDLE", "idle"],
    ["_DARK_ERROR", "error"],
    ["_DARK_UNKNOWN", "unknown"],
  ])("%s matches AGENT_STATE_HEX_DARK[%s]", (pyName, key) => {
    expect(
      AGENT_STATE_HEX_DARK[key as keyof typeof AGENT_STATE_HEX_DARK].toLowerCase(),
    ).toBe(pythonHex(pyName, py));
  });
});

describe("agent-state glyph + label maps", () => {
  it("covers every AgentActivityState value", () => {
    const expected = ["starting", "working", "waiting", "blocked", "idle", "error", "unknown"];
    for (const s of expected) {
      expect(AGENT_STATE_GLYPH[s as keyof typeof AGENT_STATE_GLYPH]).toBeDefined();
      expect(AGENT_STATE_LABEL[s as keyof typeof AGENT_STATE_LABEL]).toBeDefined();
    }
  });
});
