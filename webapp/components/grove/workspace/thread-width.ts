/**
 * The transcript's horizontal geometry: its measure, and its margins.
 *
 * `split` FILLS its pane. Upstream's centred 44rem measure is right for a chat
 * that owns a window; here the pane is already the narrower half of a split a
 * reader sized themselves, so capping it again spends that decision twice —
 * measured on a 2560px monitor, a 1531px transcript pane drew a 704px column
 * between two 400px gutters. The pane IS the measure, and dragging the handle
 * is how you change it.
 *
 * Alone, the transcript should use the monitor it is given — but not without
 * bound: an unbroken line across a 34-inch display is unreadable, so the cap is
 * generous rather than absent.
 *
 * It lives in its own module, free of React, because importing the Thread pulls
 * a vendored CSS import that the unit runner cannot process — and these are the
 * decisions in that file worth pinning with a test.
 */
export const THREAD_WIDTH = {
  split: "100%",
  full: "min(100%, 78rem)",
} as const;

/**
 * The margin either side of the transcript — ONE value for the message stream,
 * the plan card and the composer, because they share one column element and
 * must line up as a single edge.
 *
 * KEYED BY THE SAME MODE AS THE WIDTH ABOVE, because they are one decision: a
 * column that fills its pane has no gutters left over, so its margin is the
 * only thing holding the text off the panel edge and must stay small and
 * symmetric. `px-4` is a flat 16px there — upstream's own value for a
 * half-width pane, and what the container steps below already resolved to at
 * split widths before the cap was lifted.
 *
 * WHY `full` SCALES, and why with `@` and not `md:`. Upstream's flat `px-4` is
 * far too tight once the transcript owns the window: the 78rem cap stops
 * binding somewhere around a 1200px pane, so from there on the content simply
 * grew to fill, ending flush against the panel's rounded border. But the pane
 * is not the viewport, so a viewport breakpoint would apply the widest margin
 * to the narrowest column. The thread root already declares `@container`, so
 * these resolve against the THREAD's own width.
 *
 * The steps are deliberately few. This is breathing room, not a type scale.
 */
export const THREAD_INSET = {
  split: "px-4",
  full: "px-4 @3xl:px-10 @6xl:px-20",
} as const;
