/**
 * The transcript's horizontal geometry: its measure, and its margins.
 *
 * `split` is a half-width column, where upstream's 44rem is already right.
 * Alone, the transcript should use the monitor it is given — but not without
 * bound: an unbroken line across a 34-inch display is unreadable, so the cap is
 * generous rather than absent.
 *
 * It lives in its own module, free of React, because importing the Thread pulls
 * a vendored CSS import that the unit runner cannot process — and these are the
 * decisions in that file worth pinning with a test.
 */
export const THREAD_WIDTH = {
  split: "44rem",
  full: "min(100%, 78rem)",
} as const;

/**
 * The margin either side of the transcript — ONE value for the message stream,
 * the plan card and the composer, because they share one column element and
 * must line up as a single edge.
 *
 * WHY IT SCALES, and why with `@` and not `md:`. Upstream's flat `px-4` is
 * right for a half-width pane and far too tight once the transcript owns the
 * window: the cap above stops binding somewhere around a 1200px pane, so from
 * there on the content simply grew to fill, ending flush against the panel's
 * rounded border. But the pane is not the viewport — in split view it is
 * roughly half of it — so a viewport breakpoint would apply the widest margin
 * to the narrowest column and eat a split pane alive. The thread root already
 * declares `@container`, so these resolve against the THREAD's own width: a
 * split pane keeps the 16px it has today, and only a transcript that actually
 * has the room pays for the margin.
 *
 * The steps are deliberately few. This is breathing room, not a type scale.
 */
export const THREAD_INSET = "px-4 @3xl:px-10 @6xl:px-20";
