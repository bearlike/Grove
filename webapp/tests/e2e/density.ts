export const APP_DENSITY = 0.8;

/**
 * Mirrors `html { font-size: 80% }` in `app/globals.css`. The design-system
 * figures in these specs were measured at a 16px root, so rem-derived geometry
 * renders at this factor on fine-pointer browsers.
 */
export function dp(px: number): number {
  // ROUNDED TO 2dp, because the browser is. `12 * 0.8` is 9.600000000000001 in
  // binary floating point while `getComputedStyle` reports a clean `9.6px`, so
  // an exact `toHaveCSS` comparison against the bare product fails a value that
  // is precisely right — by an artifact of the arithmetic, not a defect in the
  // page. Two decimals is finer than any sub-pixel difference these specs can
  // distinguish, so nothing real is rounded away.
  return Math.round(px * APP_DENSITY * 100) / 100;
}
