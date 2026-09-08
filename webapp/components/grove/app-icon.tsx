"use client";

import { KeyRoundIcon } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";

import { isToolIconSlug as isIconSlug } from "@/lib/grove/icon-slug";
import { registerBundledIcons } from "./icon-bundle";

export { isIconSlug };

// Lazy on purpose: the Iconify runtime and its fetch machinery are only
// needed once a brand mark is on screen, so an SSR pass and a page that never
// opens a picker pay nothing for it. The module resolves to the vendor's own
// `Icon`; Grove supplies the fallback around it, never a re-drawn glyph.
const IconifyIcon = lazy(() =>
  import("@iconify/react").then((module) => {
    // Seed the loader's cache with the committed catalog artwork BEFORE the
    // first `Icon` renders, so a built-in mark needs no network at all. An
    // uncatalogued slug (a user's own MCP mapping) still resolves through the
    // API. See `icon-bundle.ts` for the measurement that motivated this.
    registerBundledIcons(module.addCollection);
    return { default: module.Icon };
  }),
);

/**
 * One icon by Iconify slug, with a stable footprint while it is unavailable.
 *
 * Three cases render the same lucide `KeyRoundIcon` at the same size: the slug
 * failed validation, the Iconify module has not loaded yet, and the icon data
 * has not arrived (or does not exist upstream). A row therefore never shifts
 * when the real mark lands, and an id no vendor table recognises still gets
 * a mark rather than a hole. Decorative by default — the label beside it
 * carries the name — so callers wanting an announced mark pass `aria-label`.
 */
export function AppIcon({
  slug,
  className = "size-4",
  ...props
}: {
  readonly slug: string | null | undefined;
  readonly className?: string;
  readonly "aria-label"?: string;
  readonly "data-testid"?: string;
}): ReactNode {
  // Both branches carry the caller's props (a test id, a label) so a census
  // counts one mark per row whether or not the vendor icon has landed yet.
  // The prop list is closed on purpose: Iconify's `Icon` narrows several SVG
  // attributes (`mode`, `rotate`, `flip`) to its own unions, so a blanket
  // SVG spread cannot type-check, and nothing here needs more than a name.
  const fallback = (
    <KeyRoundIcon aria-hidden className={className} data-slot="app-icon-fallback" {...props} />
  );
  if (!isIconSlug(slug)) return fallback;
  return (
    <Suspense fallback={fallback}>
      <IconifyIcon
        icon={slug}
        className={className}
        fallback={fallback}
        aria-hidden
        data-slot="app-icon"
        {...props}
      />
    </Suspense>
  );
}
