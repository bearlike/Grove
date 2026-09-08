import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("app/globals.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const card = css.match(/\.attachment-card\s*\{([^}]+)\}/)?.[1] ?? "";

describe("the attachment card theme", () => {
  it("uses a scoped gradient and the existing container corner", () => {
    expect(card).toContain("linear-gradient(");
    expect(card).toContain("var(--radius-lg)");
    const innerRole = [...css.matchAll(/([^{}]+)\{([^{}]+)\}/g)].filter(
      ([, , body]) => /border-radius:\s*var\(--radius-sm\)/.test(body!),
    );
    expect(innerRole.length).toBeGreaterThan(0);
    for (const [, selector] of innerRole) expect(selector).not.toContain('[data-slot="file-root"]');
    const selector = css.match(/([^{}]+)\{[^{}]*background-image:\s*linear-gradient\([^{}]*var\(--attachment-card-start\)[^{}]*\}/)?.[1];
    expect(selector?.trim()).toBe(".attachment-card");
  });

  it("derives both theme ramps from existing semantic colors rather than a new palette", () => {
    expect(card).toContain("var(--attachment-card-start)");
    expect(card).toContain("var(--attachment-card-end)");
    const stops = [...css.matchAll(/--attachment-card-(?:start|end):\s*([^;]+);/g)];
    expect(stops).toHaveLength(4);
    for (const [, value] of stops) {
      expect(value).toMatch(/var\(--surface-(?:raised|overlay|edge(?:-source)?)\)/);
      expect(value).not.toMatch(/#[\da-f]{3,8}|\b(?:oklch|oklab|rgb|hsl)\(/i);
    }
  });
});
