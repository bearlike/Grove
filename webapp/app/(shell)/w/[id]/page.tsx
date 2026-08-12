import type { Metadata } from "next";
import { cookies } from "next/headers";

import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";
import { Workspace } from "@/components/grove/workspace";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

/**
 * The one route that can honestly name what it is showing.
 *
 * A workspace `title` is a sentence a HUMAN typed into the create dialog —
 * "Frontend UI migration" — so it is safe in a tab, a screenshot and a bookmark
 * in a way none of this route's other strings are. The id is an identifier and
 * the repo root is a host path; the title is the only field that names the
 * *work* rather than the machine.
 *
 * IT FALLS BACK TO A NOUN, NEVER TO THE ID. Twenty tabs reading "Workspace" is
 * a mild failure; one tab reading `59d472a0b0ef…` is a worse one, because it is
 * both unreadable and an identifier leaking into the least private surface the
 * app has. Every failure below — no cookie, revoked session, dead daemon,
 * unknown workspace, a title that is not a usable string — lands on that noun.
 *
 * WHY IT TALKS TO THE DAEMON DIRECTLY rather than through `/api/grove/…`: this
 * runs on the server, and that proxy exists to attach a token to a request
 * arriving from a browser. Calling our own HTTP layer from inside the server
 * would need an absolute self-URL and a second hop to reach the same daemon.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  return { title: (await workspaceTitle(id)) ?? "Workspace" };
}

/** The workspace's human title, or `null` for every reason it might be absent. */
async function workspaceTitle(id: string): Promise<string | null> {
  try {
    const cookieId = (await cookies()).get(COOKIE_NAME)?.value;
    if (!cookieId) return null;
    const entry = await sharedCookieStore().lookup(cookieId);
    if (!entry) return null;

    const response = await fetch(`${daemonUrl}/workspaces/${encodeURIComponent(id)}`, {
      headers: { accept: "application/json", authorization: `Bearer ${entry.daemonToken}` },
      cache: "no-store",
    });
    if (!response.ok) return null;

    const state = (await response.json()) as { title?: unknown };
    // Narrowed at the boundary: an empty or non-string title must reach the
    // noun rather than printing `undefined` into the tab.
    return typeof state.title === "string" && state.title.trim() !== "" ? state.title : null;
  } catch {
    // Titling a tab is never worth failing a page render for.
    return null;
  }
}

/**
 * The workspace surface: transcript + work panel.
 *
 * The header is rendered by `Workspace`, not here: the breadcrumb wants the
 * workspace title and this route knows only the id.
 */
export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <Workspace id={id} />;
}
