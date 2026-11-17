"use client";

import { useEffect } from "react";
import { create } from "zustand";

import { replyCount } from "@/lib/grove/adapters";
import type { DashboardSnapshotView } from "@/lib/grove/api";

/** Where each workspace's last-seen reply count is remembered, per browser. */
const STORAGE_KEY = "grove.fleet.seen-replies";

type SeenCounts = Readonly<Record<string, number>>;

interface UnreadUi {
  /** Reply count the reader has seen, per workspace id. Absent = never observed. */
  readonly seen: SeenCounts;
  /** Whether the stored map has been read; nothing is decided before it. */
  readonly restored: boolean;
  markSeen(workspaceId: string, replies: number): void;
  /** Record a first observation without marking anything unread. */
  adopt(counts: SeenCounts): void;
}

/**
 * Has this workspace produced a model response since the reader last looked?
 *
 * Pure so the rule is testable without a store. An UNOBSERVED workspace is
 * read, not unread: the first time this browser sees a workspace its current
 * count is adopted silently, or a deploy (or a fresh browser) would paint the
 * whole rail blue for replies nobody missed. Only growth past a recorded count
 * is news. A count that shrank (a remap, a respawn onto a new session) is not.
 */
export function hasUnread(seen: SeenCounts, workspaceId: string, replies: number): boolean {
  const last = seen[workspaceId];
  return last !== undefined && replies > last;
}

/**
 * The unread cue's state: the reply counts the reader has already seen.
 *
 * Per-browser by design. A read marker shared across devices needs a daemon
 * store and a write per view, which is a larger change than the cue is worth
 * today. The cue never feeds ranking: `sortedFleetRows` reads none of this.
 */
export const useUnreadUi = create<UnreadUi>((set) => ({
  seen: {},
  restored: false,
  markSeen: (workspaceId, replies) =>
    set((state) => {
      if (state.seen[workspaceId] === replies) return state;
      return { seen: persist({ ...state.seen, [workspaceId]: replies }) };
    }),
  adopt: (counts) =>
    set((state) => {
      const additions = Object.entries(counts).filter(([id]) => state.seen[id] === undefined);
      if (additions.length === 0) return state;
      return { seen: persist({ ...state.seen, ...Object.fromEntries(additions) }) };
    }),
}));

function persist(seen: SeenCounts): SeenCounts {
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(seen));
    } catch {
      // A full or disabled storage costs the cue its memory, never the page.
    }
  }
  return seen;
}

function restore(): SeenCounts {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, number] => typeof entry[1] === "number",
      ),
    );
  } catch {
    return {};
  }
}

/**
 * Restore the seen map, adopt every workspace not yet observed, and keep the
 * OPEN workspace marked seen while its page is visible. Call ONCE, from the
 * shell, which already holds the snapshot and the route.
 *
 * The restore is an effect for the same hydration reason as the sidebar's: the
 * server has no `localStorage`, so the first client render must match it.
 * "Visible" is `document.visibilityState`, never window focus — an unfocused
 * Grove beside an editor is still being read; a hidden tab is not.
 */
export function useUnreadTracking(
  snapshot: DashboardSnapshotView | undefined,
  openWorkspaceId: string | null,
): void {
  useEffect(() => {
    useUnreadUi.setState({ seen: restore(), restored: true });
  }, []);

  const restored = useUnreadUi((state) => state.restored);

  useEffect(() => {
    if (!restored || !snapshot) return;
    const counts: Record<string, number> = {};
    for (const project of snapshot.projects) {
      for (const workspace of project.workspaces) {
        counts[workspace.state.id] = replyCount(workspace);
      }
    }
    useUnreadUi.getState().adopt(counts);

    if (openWorkspaceId === null || counts[openWorkspaceId] === undefined) return;
    const markOpen = (): void => {
      if (document.visibilityState === "visible") {
        useUnreadUi.getState().markSeen(openWorkspaceId, counts[openWorkspaceId]!);
      }
    };
    markOpen();
    document.addEventListener("visibilitychange", markOpen);
    return () => document.removeEventListener("visibilitychange", markOpen);
  }, [restored, snapshot, openWorkspaceId]);
}

/** Whether one workspace's card should carry the unread dot right now. */
export function useHasUnread(workspaceId: string, replies: number): boolean {
  return useUnreadUi((state) => state.restored && hasUnread(state.seen, workspaceId, replies));
}
