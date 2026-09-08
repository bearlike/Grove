import type { IconifyJSON } from "@iconify/react";

import bundle from "@/lib/grove/adapters/tool-icon-bundle.json";

/**
 * Register the built-in tool artwork with Iconify, once per page.
 *
 * WHY: Iconify's online loader is the right default for an ARBITRARY slug — a
 * user's supplementary MCP mapping can name any icon in any public set, and
 * nothing here could predict it. It is the wrong default for the BUILT-IN
 * catalog, which is a closed set this repo owns: measured on the deployed app
 * 2026-09-15, it cost three cross-origin requests to `api.iconify.design` on
 * every page load, repeated identically after a reload because the loader's
 * cache is module memory and dies with the page.
 *
 * `addCollection` seeds that same module cache from committed data, so the
 * loader finds every catalog icon already present and issues no request for it.
 * An uncatalogued slug still goes to the API exactly as before — this narrows
 * WHICH icons need the network, it does not remove the capability.
 *
 * Idempotent and side-effect-only, so importing it twice is free; `AppIcon`
 * calls it from the lazy chunk that loads Iconify, which keeps both off the
 * critical path and guarantees the registration happens before the first
 * `Icon` renders.
 */
let registered = false;

export function registerBundledIcons(
  addCollection: (data: IconifyJSON) => boolean,
): void {
  if (registered) return;
  registered = true;
  for (const collection of Object.values(bundle as Record<string, IconifyJSON>)) {
    addCollection(collection);
  }
}
