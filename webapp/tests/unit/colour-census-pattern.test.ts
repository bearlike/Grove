import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Exercise the browser census's actual expression, not a second copy that
// could stay green while the browser test regresses.
const source = readFileSync(new URL("../e2e/colour-census.spec.ts", import.meta.url), "utf8");
const expression = source.match(/\.filter\(\(className\) => \/([^\n]+)\/\.test\(className\)\)/)?.[1];
if (!expression) throw new Error("Colour census matcher not found");
const RAW_PALETTE_UTILITY = new RegExp(expression);

describe("workspace colour census pattern", () => {
  it.each([
    "bg-blue-500",
    "text-emerald-500",
    "border-amber-500",
    "ring-red-500",
    "fill-blue-500",
    "stroke-emerald-500",
    "from-amber-500",
    "via-red-500",
    "to-blue-500",
    "outline-emerald-500",
    "decoration-red-500",
    "dark:bg-blue-400",
    "hover:text-emerald-400",
  ])("detects raw palette utility %s", (utility) => {
    expect(RAW_PALETTE_UTILITY.test(utility)).toBe(true);
  });

  it.each([
    "bg-primary",
    "text-content-primary",
    "border-border",
    "fill-current",
    "chart-blue-500",
  ])("does not reject semantic utility %s", (utility) => {
    expect(RAW_PALETTE_UTILITY.test(utility)).toBe(false);
  });
});
