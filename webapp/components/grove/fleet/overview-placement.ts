/**
 * Where the session overview opens relative to its sidebar card.
 *
 * Radix places a tooltip beside its trigger and only lets the caller choose
 * which EDGE of the trigger to line up with. Centring on the card was the
 * default, so a card high in the rail pushed the panel up against the top of
 * the viewport. Choosing the edge from where the card sits makes the panel grow
 * toward the viewport's vertical centre instead, while staying beside the card.
 */

/** Viewport margin the panel never crosses. Radix shifts the panel inward to keep it. */
export const OVERVIEW_COLLISION_PADDING = 24;

/**
 * Within this fraction of the viewport's middle, a card is already central, so
 * centring on it is the closest the panel can get to the middle.
 */
const CENTRE_BAND = 0.06;

export type OverviewAlign = "start" | "center" | "end";

/**
 * Which card edge to anchor to so the panel grows toward the vertical centre.
 *
 * Args:
 *   cardCenterY: the card's vertical midpoint, in viewport pixels.
 *   viewportHeight: the viewport's height, in pixels.
 *
 * Returns:
 *   `start` for a card in the upper half (the panel grows downward), `end` for
 *   one in the lower half (it grows upward), and `center` near the middle or
 *   when either measurement is unusable.
 */
export function overviewAlign(cardCenterY: number, viewportHeight: number): OverviewAlign {
  if (!Number.isFinite(cardCenterY) || !(viewportHeight > 0)) return "center";
  const offset = cardCenterY / viewportHeight - 0.5;
  if (Math.abs(offset) <= CENTRE_BAND) return "center";
  return offset < 0 ? "start" : "end";
}
