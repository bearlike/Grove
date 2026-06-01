"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { GroveClient } from "./client";
import type {
  CommitSummaryView,
  WhoamiView,
  WorkspaceStateView,
  WorkspacePeekView,
} from "./types";

const client = GroveClient.default();

export function useWorkspaces() {
  return useQuery<WorkspaceStateView[]>({
    queryKey: ["workspaces"],
    queryFn: () => client.listWorkspaces(),
    refetchInterval: 5_000,
  });
}

/**
 * Daemon identity + uptime for the persistent footer. Refreshes every
 * 30 s — uptime is slow-changing relative to peek/workspaces, so a
 * hotter cadence would just burn the daemon for no UI gain. The
 * StatusBar interpolates seconds locally between fetches via a tick
 * effect so the displayed "up Xm Ys" still ticks live.
 */
export function useDaemonWhoami() {
  return useQuery<WhoamiView>({
    queryKey: ["whoami"],
    queryFn: () => client.getWhoami(),
    refetchInterval: 30_000,
  });
}

export function useWorkspace(id: string) {
  return useQuery<WorkspaceStateView>({
    queryKey: ["workspace", id],
    queryFn: () => client.getWorkspace(id),
    refetchInterval: 5_000,
    enabled: Boolean(id),
  });
}

export function useWorkspacePeek(id: string) {
  return useQuery<WorkspacePeekView>({
    queryKey: ["peek", id],
    queryFn: () => client.getPeek(id),
    refetchInterval: 2_000,
    enabled: Boolean(id),
  });
}

/**
 * Comprehensive branch history for the detail page. Daemon's
 * `GET /workspaces/{id}/commits` returns every commit on the workspace
 * branch since fork from base — the detail page renders the full list,
 * the TUI rail keeps using `peek.recent_commits`. Cadence is slower
 * (15 s) than peek because commit history changes much less often than
 * the agent pane.
 */
export function useWorkspaceCommits(id: string) {
  return useQuery<CommitSummaryView[]>({
    queryKey: ["commits", id],
    queryFn: () => client.getCommits(id),
    refetchInterval: 15_000,
    enabled: Boolean(id),
  });
}

/**
 * Fan out N parallel peek queries for the home grid. Cadence is 5 s
 * (slower than the detail page — list cards don't need 2 s freshness).
 * Returns a Map<id, peek | undefined>; undefined while the first fetch
 * is in flight or after a transient failure. Each peek is cached under
 * the same `["peek", id]` key the detail page uses, so navigating from
 * grid → detail reuses the warm peek instead of refetching.
 */
export function useWorkspacesPeeks(
  ids: string[],
): Map<string, WorkspacePeekView | undefined> {
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["peek", id],
      queryFn: () => client.getPeek(id),
      refetchInterval: 5_000,
      enabled: Boolean(id),
    })),
  });
  const map = new Map<string, WorkspacePeekView | undefined>();
  ids.forEach((id, i) => {
    map.set(id, queries[i]?.data);
  });
  return map;
}
