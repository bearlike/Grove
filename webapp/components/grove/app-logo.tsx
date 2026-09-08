import { cn } from "@/lib/utils";

/**
 * Grove's APP ICON — the mark on its own tile, as an installed app draws it.
 *
 * The sibling of `brand-mark.tsx`, and the split is about GROUND rather than
 * about size. `BrandMark` is the backgroundless wheel, and it is correct
 * wherever Grove's own surface is already behind it: the rail header, a print
 * header, a row beside the wordmark. This one carries the cream tile and the
 * squircle, and it is correct where the mark has to hold its own — the landing
 * page's welcome block, where the mark stands alone above the composer and is
 * the same object the reader's dock, home screen and browser tab are showing.
 *
 * A RASTER, DELIBERATELY, WHERE THE MARK ITSELF IS A PATH. The tile is the
 * exact artwork shipped to every other icon surface (`app/icon.png`,
 * `apple-icon.png`, the manifest's four entries), so re-drawing it as SVG here
 * would be a second source for one picture and the two would drift — the
 * squircle's corner curve and the tile's cream are not values this file gets
 * to have an opinion about. It is served from `public/` at a size no larger
 * than it renders, and it is the only image on a route that otherwise paints
 * from inline paths.
 *
 * `priority` is NOT passed and must not be: this sits in the landing page's
 * first paint, but `next/image`'s preload competes with the composer's own
 * hydration for the same early bytes, and the mark has a reserved box, so a
 * late-arriving image shifts nothing.
 *
 * Decorative by default, for `BrandMark`'s reason: pass `label` only where the
 * mark stands alone with no wordmark beside it.
 */
export function AppLogo({
  className,
  label,
  // The caller may rename the region — the landing page addresses this element
  // as `launch-brand`, a name its own e2e suite and onboarding tour already
  // own. Defaulted rather than required so an ordinary caller needs no testid.
  "data-testid": testId = "app-logo",
}: {
  className?: string;
  label?: string;
  "data-testid"?: string;
}): React.ReactNode {
  return (
    // A plain `<img>` rather than `next/image`: this is a fixed-size,
    // already-optimised PNG served from `public/`, so the loader's resizing
    // and format negotiation have nothing to do, and the component would add
    // a wrapper element the callers' `size-*` classes would have to fight.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/icon-512.png"
      alt={label ?? ""}
      width={512}
      height={512}
      decoding="async"
      className={cn("size-full object-contain", className)}
      aria-hidden={label ? undefined : true}
      data-testid={testId}
    />
  );
}
