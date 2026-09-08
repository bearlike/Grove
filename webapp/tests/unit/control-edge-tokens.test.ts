import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
const block = (theme: string) => css.slice(css.indexOf(`${theme} {`)).split("\n}")[0];
const lightness = (theme: string, name: string) => {
  const match = block(theme).match(new RegExp(`--${name}: oklch\\(([^ ]+)`));
  if (!match) throw new Error(`Missing ${theme} ${name}`);
  return Number(match[1]);
};

describe("resting control edges do not borrow the focus contrast", () => {
  it("softens light borders without weakening input and focus boundaries", () => {
    expect(lightness(":root", "edge-control")).toBeGreaterThan(0.594);
    expect(lightness(":root", "input")).toBe(0.594);
    expect(lightness(":root", "ring")).toBe(0.594);
  });
  it("softens dark borders without weakening input and focus boundaries", () => {
    expect(lightness(".dark", "edge-control")).toBeLessThan(0.56);
    expect(lightness(".dark", "input")).toBe(0.56);
    expect(lightness(".dark", "ring")).toBe(0.56);
  });
});
