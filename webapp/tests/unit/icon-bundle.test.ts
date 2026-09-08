import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import catalog from "@/lib/grove/adapters/tool-catalog.json";
import bundle from "@/lib/grove/adapters/tool-icon-bundle.json";

/**
 * The bundle exists so a built-in tool mark costs NO third-party request.
 *
 * Measured on the deployed app 2026-09-15: three cross-origin requests to
 * `api.iconify.design` on every page load, repeated identically after a reload
 * because the loader's cache is module memory. A catalog icon missing from the
 * bundle silently restores that round trip and renders a fallback until it
 * lands — which looks like a slug typo rather than a stale build artifact, so
 * nothing about the symptom points at the cause.
 */
const iconsIn = (set: { icons: Record<string, unknown>; aliases?: Record<string, unknown> }) =>
  new Set([...Object.keys(set.icons), ...Object.keys(set.aliases ?? {})]);

describe("the committed icon bundle", () => {
  it("carries every icon the built-in catalog names", () => {
    const missing = (catalog.tools as { icon: string }[])
      .map((tool) => tool.icon)
      .filter((slug) => {
        const [prefix, name] = slug.split(":");
        const set = (bundle as Record<string, { icons: Record<string, unknown> }>)[prefix ?? ""];
        return !set || !iconsIn(set).has(name ?? "");
      });
    expect(missing).toEqual([]);
  });

  it("carries the two generic fallbacks, which no catalog entry names", () => {
    // An unknown tool and an unmapped MCP server resolve to these, so they are
    // reachable without appearing in `tools[]` — exactly the kind of icon a
    // catalog-only check would miss.
    for (const slug of ["flat-color-icons:services", "flat-color-icons:settings"]) {
      const [prefix, name] = slug.split(":");
      const set = (bundle as Record<string, { icons: Record<string, unknown> }>)[prefix ?? ""];
      expect(iconsIn(set)).toContain(name);
    }
  });

  it("registers the bundle from the lazy chunk, before the first Icon renders", () => {
    // A source census: the registration is a side effect inside a `lazy()`
    // loader, so no static render can observe it. What would regress is the
    // WIRING — an import that stops calling `addCollection` leaves every icon
    // going back to the network with nothing failing.
    const source = readFileSync(
      join(process.cwd(), "components/grove/app-icon.tsx"),
      "utf8",
    );
    expect(source).toContain("registerBundledIcons(module.addCollection)");
  });
});
