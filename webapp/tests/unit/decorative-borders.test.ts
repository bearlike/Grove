import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

const theme = (selector: string) => css.slice(css.indexOf(`${selector} {`)).split("\n}")[0];

const token = (selector: string, name: string): string => {
  const match = theme(selector).match(new RegExp(`${name}:\\s*([^;]+);`));
  if (!match) throw new Error(`Missing ${name} in ${selector}`);
  return match[1].trim();
};

describe("decorative border tokens", () => {
  it("makes decorative edge output an exact 60:40 source-to-surface mix", () => {
    for (const selector of [":root", ".dark"]) {
      expect(token(selector, "--border")).toBe(
        "color-mix(in srgb, var(--border-source) 60%, transparent)",
      );
      expect(token(selector, "--surface-edge")).toBe(
        "color-mix(in srgb, var(--surface-edge-source) 60%, transparent)",
      );
      expect(token(selector, "--sidebar-border")).toBe("var(--border)");
    }
  });

  it("keeps decorative source colors outside the transparent output", () => {
    expect(css).toContain("--border-source: oklch(0.83 0.006 286);");
    expect(css).toContain("--surface-edge-source: oklch(0.82 0.007 286);");
    expect(css).toContain("--border-source: oklch(0.36 0.008 286);");
    expect(css).toContain("--surface-edge-source: oklch(0.36 0.008 286);");
    expect(token(":root", "--attachment-card-end")).toBe(
      "color-mix(in oklab, var(--surface-edge-source) 28%, var(--surface-raised))",
    );
    expect(token(".dark", "--attachment-card-start")).toBe(
      "color-mix(in oklab, var(--surface-edge-source) 94%, var(--foreground))",
    );
    expect(token(".dark", "--attachment-card-end")).toBe("var(--surface-edge-source)");
  });

  it("restores original border tokens inside interactive controls", () => {
    expect(token(":root", "--edge-control")).toBe("oklch(0.80 0.009 286)");
    expect(token(".dark", "--edge-control")).toBe("oklch(0.40 0.009 286)");
    expect(token(":root", "--input")).toBe("oklch(0.594 0.008 286)");
    expect(token(".dark", "--input")).toBe("oklch(0.56 0.008 286)");
    expect(token(":root", "--ring")).toBe("oklch(0.594 0.008 286)");
    expect(token(".dark", "--ring")).toBe("oklch(0.56 0.008 286)");

    const controls = css.slice(
      css.indexOf(':is(button, input, select, textarea, summary,'),
      css.indexOf("/* Native outline buttons add"),
    );
    expect(controls).toContain("--border: var(--border-source);");
    expect(controls).toContain("--surface-edge: var(--surface-edge-source);");
    expect(controls).not.toContain("border-color:");
    for (const selector of [
      "button",
      "input",
      "select",
      "textarea",
      "summary",
      '[role="button"]',
      '[role="tab"]',
      '[role="switch"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="combobox"]',
      'a[data-slot="badge"]',
    ]) {
      expect(controls).toContain(selector);
    }
  });

  it("leaves line tabs without a border-color override", () => {
    const tabs = readFileSync(new URL("../../components/assistant-ui/tabs.tsx", import.meta.url), "utf8");

    expect(tabs).toContain('line: "border-border gap-1 border-b bg-transparent pb-2"');
    expect(css).not.toContain('[data-variant="line"]');
  });
});
