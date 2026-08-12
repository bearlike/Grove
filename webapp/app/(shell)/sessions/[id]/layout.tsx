import type { Metadata } from "next";

/**
 * A static noun, deliberately — this route CANNOT name the thing it shows.
 *
 * Every identifier it holds is one a title may not carry. The path segment is
 * the session id, and the query string is `kind` plus `cwd` — a **host path**,
 * which is the single worst string that could end up in a tab title, a
 * screenshot or a bookmark. There is no human name to fall back to either: a
 * catalog row's title is derived from the transcript, so reading one would mean
 * an authenticated full-transcript fetch to label a tab.
 *
 * So "Session" is the honest answer rather than a lazy one. It overrides the
 * parent layout's "All Sessions", which would otherwise claim this page is the
 * catalog rather than one entry in it.
 */
export const metadata: Metadata = { title: "Session" };

export default function SessionLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return children;
}
