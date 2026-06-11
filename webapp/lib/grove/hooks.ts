"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GroveClient } from "./client";
import { applyDashboardEvent } from "./activity-stream";
import type {
  CommitSummaryView,
  DashboardEvent,
  DashboardSnapshotView,
  SessionDetailView,
  SessionSummaryView,
  WhoamiView,
  WorkspacePaneView,
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
/**
 * Recorded agent sessions for the detail page's Sessions panel. History-tier
 * cadence (15 s, same as commits): a session list changes when an agent run
 * starts/ends or a transcript grows — minutes apart, not seconds.
 */
export function useWorkspaceSessions(id: string) {
  return useQuery<SessionSummaryView[]>({
    queryKey: ["sessions", id],
    queryFn: () => client.getSessions(id),
    refetchInterval: 15_000,
    enabled: Boolean(id),
  });
}

/**
 * One session's conversation digest, fetched on expand only (`sessionId` null →
 * disabled, zero requests). Turns never poll fast: the digest is a transcript
 * read, the heaviest per-request endpoint here, and the live signal already
 * comes from peek/activity — 30 s keeps an expanded view fresh without burning
 * the daemon on parses nobody is watching.
 */
export function useSessionTurns(id: string, sessionId: string | null) {
  return useQuery<SessionDetailView>({
    queryKey: ["turns", id, sessionId],
    queryFn: () => client.getSessionTurns(id, sessionId as string, 100),
    refetchInterval: 30_000,
    enabled: Boolean(id) && Boolean(sessionId),
  });
}

/**
 * The cross-project Activity Dashboard stream.
 *
 * Primary path: a cookie-auth `EventSource` to the BFF `/api/grove/events` — the
 * daemon sends a `snapshot` on connect then live deltas, which the pure
 * `applyDashboardEvent` reducer folds into one snapshot. EventSource handles
 * reconnect (and `Last-Event-ID` replay) for free.
 *
 * Fallback: when SSE can't connect (an intermediary strips event-streams, or
 * we're in jsdom which has no `EventSource`), a `/activity` poll keeps the wall
 * live. It's enabled only while disconnected, so the happy path costs one stream
 * and zero polls.
 */
export interface ActivityStream {
  snapshot: DashboardSnapshotView | null;
  connected: boolean;
  /** Epoch ms of the most recent SSE event of ANY kind (incl. heartbeat); null until the first event. */
  lastEventAt: number | null;
  /** Last poll-fallback error while disconnected — the daemon-unreachable affordance. Null while the stream is live. */
  error: Error | null;
  /** Force a fresh pull: tear down + reconnect the stream (server resends `snapshot`) and reset the poll fallback. */
  refresh: () => void;
}

// The daemon heartbeats every 15 s; anything older means the tab slept through
// at least one beat, so the EventSource is presumed dead even if it never
// fired `onerror` (background-tab throttling / network sleep swallow it).
const STREAM_STALE_MS = 20_000;

export function useActivityStream(): ActivityStream {
  const [snapshot, setSnapshot] = useState<DashboardSnapshotView | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  // Ref twin of `lastEventAt` so the visibilitychange listener reads the
  // current value without re-subscribing on every event.
  const lastEventAtRef = useRef<number | null>(null);
  const stamp = useCallback(() => {
    lastEventAtRef.current = Date.now();
    setLastEventAt(lastEventAtRef.current);
  }, []);
  // Nonce that, when bumped, re-runs the EventSource effect — a fresh connection
  // makes the daemon resend the `snapshot` from scratch (the refresh mechanism).
  const [reconnectKey, setReconnectKey] = useState(0);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === "undefined") return; // jsdom / SSR → poll fallback only
    const es = new EventSource(`${GroveClient._basePath}/events`);
    // Every event — snapshot, delta, lifecycle, heartbeat — refreshes the
    // dashboard-wide "last updated" clock, so the UI proves the stream is live.
    const fold = (e: MessageEvent) => {
      stamp();
      setSnapshot((prev) => applyDashboardEvent(prev, JSON.parse(e.data) as DashboardEvent));
    };
    es.addEventListener("snapshot", fold);
    es.addEventListener("session_activity", fold);
    es.addEventListener("workspace_changed", () => {
      stamp();
      // Lifecycle wake-up carries no payload — re-fetch the full snapshot.
      client.getActivity().then(setSnapshot).catch(() => undefined);
    });
    es.addEventListener("heartbeat", stamp);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [reconnectKey, stamp]);

  // Stale-tab self-heal: a backgrounded tab's EventSource can die without ever
  // firing `onerror`, leaving a wall that looks live but is frozen. On return
  // to the tab, a missed heartbeat means reconnect — the daemon then resends a
  // fresh `snapshot` on the new connection.
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      const last = lastEventAtRef.current;
      if (last !== null && Date.now() - last > STREAM_STALE_MS) {
        setReconnectKey((n) => n + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  const poll = useQuery<DashboardSnapshotView>({
    queryKey: ["activity"],
    queryFn: () => client.getActivity(),
    refetchInterval: 4_000,
    enabled: !connected,
  });
  useEffect(() => {
    if (!connected && poll.data) {
      setSnapshot(poll.data);
      stamp();
    }
  }, [connected, poll.data, stamp]);

  const refresh = useCallback(() => {
    // Force-resend the snapshot over a fresh connection, and re-pull the poll
    // fallback so both transports re-fetch from scratch on click.
    setReconnectKey((n) => n + 1);
    void queryClient.invalidateQueries({ queryKey: ["activity"] });
  }, [queryClient]);

  return {
    snapshot,
    connected,
    lastEventAt,
    // While the stream is live the poll is disabled, so its stale error (if
    // any) must not surface as "daemon unreachable".
    error: connected ? null : poll.error,
    refresh,
  };
}

/**
 * Poll one workspace's agent pane for the dashboard's focused live view.
 *
 * Enabled only for the single expanded/focused card (`enabled`) — the
 * "summary wall + one live focus" shape, never N live panes. ~1 s cadence
 * matches the agent-pane refresh; disabled → no request.
 */
export function useWorkspacePane(id: string | null, enabled: boolean) {
  return useQuery<WorkspacePaneView>({
    queryKey: ["pane", id],
    queryFn: () => client.getPane(id as string),
    refetchInterval: 1_000,
    enabled: Boolean(id) && enabled,
  });
}
