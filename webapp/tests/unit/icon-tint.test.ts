import { describe, expect, it } from "vitest";

import { iconTint } from "@/lib/grove/adapters/icon-tint";
import catalog from "@/lib/grove/adapters/tool-catalog.json";

const SLUGS = [...new Set((catalog.tools as { icon: string }[]).map(tool => tool.icon))];

describe("a disc is tinted from its own mark", () => {
  it("resolves a tint for every icon the catalog names", () => {
    // The census, not a sample: a slug that stopped resolving would draw a
    // neutral disc, which looks deliberate and is indistinguishable from a
    // decision. Asserting the count too, so a catalog emptied by a bad merge
    // cannot pass this vacuously.
    expect(SLUGS.length).toBeGreaterThan(20);
    const missing = SLUGS.filter(slug => iconTint(slug) === null);
    expect(missing).toEqual([]);
  });

  it("takes a chromatic accent from the artwork", () => {
    // Pinned against the committed bundle, so these change only when the
    // artwork does — which is exactly the event worth reviewing.
    expect(iconTint("material-icon-theme:console")).toBe("#ff7043");
    expect(iconTint("flat-color-icons:document")).toBe("#90caf9");
  });

  it("reports no tint rather than guessing, for anything it cannot read", () => {
    expect(iconTint("not-a-prefix:not-an-icon")).toBeNull();
    expect(iconTint(null)).toBeNull();
    expect(iconTint(undefined)).toBeNull();
    expect(iconTint("")).toBeNull();
  });

  it("never returns a colour that carries no identity", () => {
    // Structure — near-white, near-black, greyscale — is the page's own
    // vocabulary. A disc tinted with any of them says nothing while looking
    // like a bug, so those are filtered rather than ranked lower.
    for (const slug of SLUGS) {
      const tint = iconTint(slug)!;
      const n = Number.parseInt(tint.slice(1), 16);
      const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
      expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeGreaterThanOrEqual(24);
    }
  });
});
