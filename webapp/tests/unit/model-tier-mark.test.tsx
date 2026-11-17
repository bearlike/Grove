import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModelMark } from "@/components/grove/model-option";

/**
 * A routing tier draws a TIER glyph, not a vendor mark and not the "no idea"
 * key: the four tiers are an ordinal ladder (like the effort marks) so a
 * reader tells `small` from `xlarge` at a glance in the picker.
 */
describe("ModelMark for a proxy tier", () => {
  const render = (id: string) => renderToStaticMarkup(<ModelMark id={id} />);

  it("draws one distinct glyph per tier, carrying the tier for the theme", () => {
    const seen = new Set<string>();
    for (const tier of ["small", "medium", "large", "xlarge"]) {
      const html = render(`anthropic-${tier}[1m]`);
      expect(html).toContain('data-testid="model-mark"');
      expect(html).toContain(`data-tier="${tier}"`);
      expect(html).not.toContain('data-slot="app-icon-fallback"');
      const glyph = html.match(/class="lucide lucide-([a-z0-9-]+)/)?.[1];
      expect(glyph, tier).toBeTruthy();
      seen.add(glyph!);
    }
    expect(seen.size).toBe(4);
  });

  it("is coloured by the theme on the sequential ramp, small to xlarge", () => {
    const css = readFileSync("app/globals.css", "utf8");
    for (const [tier, token] of [["small", "low"], ["medium", "medium"], ["large", "high"], ["xlarge", "xhigh"]]) {
      expect(css).toMatch(new RegExp(`\\[data-testid="model-mark"\\]\\[data-tier="${tier}"\\]\\s*\\{\\s*color: var\\(--effort-${token}\\)`));
    }
  });

  it("leaves a vendor model on its brand path", () => {
    expect(render("anthropic-opus[1m]")).not.toContain("data-tier");
  });
});
