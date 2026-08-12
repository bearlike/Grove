/**
 * The one spelling of the tab-title suffix.
 *
 * It exists as a constant rather than a literal in `app/layout.tsx` because
 * Next's template does not simply cascade to every descendant: **a segment that
 * sets a plain-string `title` CONSUMES the nearest ancestor's template and
 * leaves none behind for its own children.** Measured — `/sessions` (a layout
 * with `title: "All Sessions"`) rendered `All Sessions | Grove` correctly while
 * its child `/sessions/[id]` rendered a bare `Session` with no suffix at all.
 *
 * So any segment that both sets a title AND has titled children has to
 * re-declare the template, and that is the second place this string would have
 * been typed. The failure it prevents is invisible in review and only shows up
 * in a tab: one route in the app quietly losing the product name.
 */
export const TITLE_TEMPLATE = "%s | Grove";
