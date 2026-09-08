import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(
  new URL("../../app/globals.css", import.meta.url),
  "utf8",
);

const ROLE_BLOCKS = [
  { role: "row", value: "0" },
  { role: "inner", value: "var(--radius-sm)" },
  { role: "control", value: "var(--radius-md)" },
  { role: "container", value: "var(--radius-lg)" },
  { role: "circle", value: "9999px" },
] as const;

const THEME_RADIUS_TOKENS = [
  "--radius-xs",
  "--radius-sm",
  "--radius-md",
  "--radius-lg",
  "--radius-xl",
  "--radius-2xl",
  "--radius-3xl",
  "--radius-4xl",
] as const;

const DOCUMENTED_ROLES = [
  "0     list rows, table cells",
  "2.3   keycaps, badges, inner cells, code blocks",
  "3.45  buttons, inputs, selects, tab triggers, chips",
  "5.75  cards, composer, popovers, dialogs, the page panel",
  "full  circles only — status dots, avatars, brand marks, spinners",
] as const;

function escaped(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function roleBlocks(value: string): readonly RegExpMatchArray[] {
  const expression = new RegExp(
    `((?:\\s*\\[data-slot="[^"]+"\\],?)+)\\s*\\{\\s*border-radius:\\s*${escaped(value)}\\s*;\\s*\\}`,
    "g",
  );
  return [...CSS.matchAll(expression)];
}

function slotsInRole(value: string): string[] {
  return roleBlocks(value).flatMap((match) =>
    [...match[1].matchAll(/\[data-slot="([^"]+)"\]/g)].map((slot) => slot[1]!),
  );
}

describe("radius role declarations", () => {
  it("documents every approved role exactly once", () => {
    for (const role of DOCUMENTED_ROLES) {
      expect(CSS.split(role)).toHaveLength(2);
    }
  });

  it("collapses Tailwind's radius scale onto the five approved roles", () => {
    expect(CSS).toMatch(/--radius-xs:\s*calc\(max\(5.75px, var\(--radius\)\) \* 0\.4\)/);
    expect(CSS).toMatch(/--radius-sm:\s*calc\(max\(5.75px, var\(--radius\)\) \* 0\.4\)/);
    expect(CSS).toMatch(/--radius-md:\s*calc\(max\(5.75px, var\(--radius\)\) \* 0\.6\)/);
    for (const token of THEME_RADIUS_TOKENS) {
      expect([...CSS.matchAll(new RegExp(`${token}:`, "g"))], token).toHaveLength(1);
    }
    for (const token of ["--radius-lg", "--radius-2xl", "--radius-3xl", "--radius-4xl"]) {
      expect(CSS).toMatch(new RegExp(`${token}:\\s*max\\(5.75px, var\\(--radius\\)\\)`));
    }
  });

  it("declares every data-slot role exactly once", () => {
    for (const { role, value } of ROLE_BLOCKS) {
      const slots = slotsInRole(value);
      expect(slots, `${role} role`).not.toHaveLength(0);
      expect(roleBlocks(value), `${role} role declaration`).toHaveLength(1);
    }

    expect([...CSS.matchAll(/border-radius:\s*0\s*;/g)]).toHaveLength(1);
  });

  it("never assigns a data-slot to two radius roles", () => {
    const owners = new Map<string, string>();
    for (const { role, value } of ROLE_BLOCKS.filter(({ role }) => role !== "row")) {
      for (const slot of slotsInRole(value)) {
        expect(owners.get(slot), `${slot} is assigned to ${role} and ${owners.get(slot)}`).toBeUndefined();
        owners.set(slot, role);
      }
    }

    for (const slot of slotsInRole("0")) {
      expect(owners.get(slot), `${slot} is assigned to row and ${owners.get(slot)}`).toBeUndefined();
      owners.set(slot, "row");
    }
    expect(owners.size).toBeGreaterThan(0);
  });
});
