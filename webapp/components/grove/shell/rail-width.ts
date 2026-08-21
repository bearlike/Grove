/**
 * The rail's expanded width — 392px, the closest step on the 4px spacing scale
 * to 20% wider than the old `w-82` (393.6px would be exact).
 *
 * A MODULE, not a literal repeated per rail. `app-shell.tsx` has always carried
 * the rule that this is "the only rail width in the tree" — everything inside a
 * rail is `w-full` — because a second copy is what once let the docked measure
 * leak into the mobile Sheet. The public share view then added a second rail,
 * which made that rule false the moment it was written down. One export keeps
 * it true by construction rather than by everyone remembering.
 *
 * The COLLAPSED width deliberately does not live here. `w-12` is not a width,
 * it is an arithmetic fit — 8px + a 32px icon button + 8px — and only the app
 * shell's rail collapses at all.
 */
export const RAIL_WIDTH = "w-98";
