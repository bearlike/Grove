import type { LucideIcon } from "lucide-react";
// The `*Icon` spelling, always. Both resolve, so nothing fails when they drift
// — which is exactly why seven glyphs ended up imported both ways. Usage is
// `ChartColumnIcon` and a session is `HistoryIcon` per the entity table; the
// bare `BarChart3`/`Activity` here were the second name for each.
import { ChartColumnIcon, HistoryIcon, TreesIcon } from "lucide-react";

/** A top-level destination in the shell. */
export interface NavItem {
  readonly href: string;
  readonly label: string;
  readonly icon: LucideIcon;
  /**
   * WHERE the destination is offered — not where it EXISTS. Every entry here is
   * a section of the app, and `sectionFor` reads the whole list regardless of
   * placement.
   *
   * That distinction is the bug this field prevents: moving Sessions into the
   * account menu by DELETING it from this list would leave
   * `sectionFor("/sessions/…")` falling through to `Fleet`, so every session
   * page would quietly title itself with the wrong section. A destination
   * changing its home in the chrome must not change what the route is.
   */
  readonly placement: "rail" | "account";
}

/**
 * The shell's destinations, in the order they appear. This is the ONE list —
 * the rail renders the `rail` ones, the account menu renders the `account`
 * ones, and the header derives its default title from all of them, so a new
 * surface is added here and nowhere else.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Fleet", icon: TreesIcon, placement: "rail" },
  { href: "/usage", label: "Usage", icon: ChartColumnIcon, placement: "rail" },
  // "All Sessions", not "Sessions". It sits one click from a workspace's own
  // transcript, and the qualifier is what says this is the host-wide catalog
  // rather than the session you are looking at. ONE name in one place: the menu
  // item and the page's own header both read it from here, so the destination
  // cannot end up called two things.
  { href: "/sessions", label: "All Sessions", icon: HistoryIcon, placement: "account" },
];

/** The destinations the rail offers. `/` is excluded: the brand mark links there. */
export const RAIL_ITEMS: readonly NavItem[] = NAV_ITEMS.filter(
  (item) => item.placement === "rail" && item.href !== "/",
);

/** The destinations the account menu offers. */
export const ACCOUNT_ITEMS: readonly NavItem[] = NAV_ITEMS.filter(
  (item) => item.placement === "account",
);

/**
 * The section a path belongs to, which is what the header titles itself with
 * when the page passes nothing. Deriving it from the route is why no page has
 * to portal a title into the header the way the old dashboard did.
 *
 * An unlisted path (a workspace, a session) resolves to its section, and a page
 * that knows something better — a workspace's title — passes it explicitly.
 * Placement is deliberately NOT consulted: a section the rail does not show is
 * still a section.
 */
export function sectionFor(pathname: string): NavItem {
  const match = NAV_ITEMS.filter(
    (item) => item.href !== "/" && pathname.startsWith(item.href),
  );
  return match[0] ?? NAV_ITEMS[0]!;
}
