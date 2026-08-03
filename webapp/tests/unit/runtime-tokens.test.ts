import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  RUNTIME_GLYPH,
  RUNTIME_HEX_DARK,
  RUNTIME_LABEL,
  runtimeColor,
  runtimeGlyph,
} from "@/lib/grove/runtime-tokens";

/**
 * The runtime axis is the one whose GLYPH is contract, not convention: a user
 * moving between the TUI and this console mid-task must not have to learn two
 * marks for "can this agent reach my filesystem". So this drift test reads the
 * Python contract for glyph AND label AND hex — the status/agent tests only
 * pin hex, because those glyphs were hand-mirrored before the rule existed.
 */
const PYTHON_SOURCE = path.resolve(
  __dirname,
  "../../../src/grove/core/contracts/runtime_palette.py",
);

const py = readFileSync(PYTHON_SOURCE, "utf8");

/** Pull `Runtime.HOST: "<value>",` out of a named dict literal in the contract. */
function pythonEntry(dictName: string, member: string): string {
  const dict = py.split(`${dictName}: Final`)[1];
  if (!dict) throw new Error(`Could not find ${dictName} in ${PYTHON_SOURCE}`);
  const body = dict.split("}")[0];
  const m = body.match(new RegExp(`Runtime\\.${member}:\\s*"([^"]+)"`));
  if (!m) throw new Error(`Could not find ${member} in ${dictName}`);
  return m[1];
}

/**
 * `DARK_RUNTIME_HEX` composes NAMED atoms rather than literals, so resolve the
 * entry's identifier and then that identifier's own literal — following the
 * wiring means a rename or a re-point is caught, not just a changed hex.
 */
function pythonRuntimeHex(member: string): string {
  const dict = py.split("DARK_RUNTIME_HEX: Final")[1];
  if (!dict) throw new Error(`Could not find DARK_RUNTIME_HEX in ${PYTHON_SOURCE}`);
  const ref = dict.split("}")[0].match(new RegExp(`Runtime\\.${member}:\\s*([A-Za-z_][\\w]*)`));
  if (!ref) throw new Error(`DARK_RUNTIME_HEX[${member}] does not name an atom`);
  const atom = py.match(new RegExp(`${ref[1]}\\s*:\\s*Final\\s*=\\s*"([#0-9a-fA-F]+)"`));
  if (!atom) throw new Error(`Could not resolve atom ${ref[1]} in ${PYTHON_SOURCE}`);
  return atom[1].toLowerCase();
}

describe("runtime vocabulary parity with grove.core.contracts.runtime_palette", () => {
  it.each([
    ["HOST", "host"],
    ["CONTAINER", "container"],
  ] as const)("%s glyph, label and dark hex match the Python contract", (member, key) => {
    expect(RUNTIME_GLYPH[key]).toBe(pythonEntry("RUNTIME_GLYPH", member));
    expect(RUNTIME_LABEL[key]).toBe(pythonEntry("RUNTIME_LABEL", member));
    expect(RUNTIME_HEX_DARK[key].toLowerCase()).toBe(pythonRuntimeHex(member));
  });

  it("marks BOTH runtimes — neither state is silent on this axis", () => {
    for (const key of ["host", "container"] as const) {
      expect(runtimeGlyph(key)).toBeTruthy();
      expect(runtimeColor(key, true)).toMatch(/^#/);
      expect(runtimeColor(key, false)).toMatch(/^#/);
    }
  });

  it("gives the two runtimes distinct glyphs and hexes in both themes", () => {
    expect(runtimeGlyph("host")).not.toBe(runtimeGlyph("container"));
    for (const dark of [true, false]) {
      expect(runtimeColor("host", dark)).not.toBe(runtimeColor("container", dark));
    }
  });

  it("falls back to the host mark for an out-of-contract runtime", () => {
    // A streamed delta can carry a value this client's enum predates; an
    // unresolved lookup would render an empty mark, i.e. the silence this replaces.
    const rogue = "wasm" as unknown as "host";
    expect(runtimeGlyph(rogue)).toBe(RUNTIME_GLYPH.host);
    expect(runtimeColor(rogue, true)).toBe(RUNTIME_HEX_DARK.host);
  });
});
