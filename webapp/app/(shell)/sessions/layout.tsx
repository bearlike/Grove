import type { Metadata } from "next";

import { sectionFor } from "@/components/grove/shell/nav";
import { TITLE_TEMPLATE } from "@/app/title";

/**
 * A layout that exists ONLY to carry a title.
 *
 * `page.tsx` here is a client component — it owns filter state and a query — and
 * a client component cannot export `metadata`. Next's answer is a server layout
 * beside it, so this file adds no markup and no wrapper element: it returns its
 * children untouched, and the page's own flex layout is unchanged.
 *
 * The name comes from `NAV_ITEMS` via `sectionFor`, so the tab, the account-menu
 * item and the page header all read "All Sessions" from one place. Typing it
 * here would be the third name for one destination.
 *
 * IT RE-DECLARES THE TEMPLATE, which looks redundant against the root layout and
 * is not: a plain-string title consumes the ancestor template and leaves none
 * for descendants, so without this `/sessions/[id]` renders a bare `Session`
 * with no product name. Measured, not inferred — see `app/title.ts`.
 */
export const metadata: Metadata = {
  title: { default: sectionFor("/sessions").label, template: TITLE_TEMPLATE },
};

export default function SessionsLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return children;
}
