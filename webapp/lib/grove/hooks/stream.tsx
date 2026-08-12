"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  GroveClient,
  subscribeToEventStream,
  type DashboardEvent,
  type DashboardSnapshotView,
  type WorkspaceActivityView,
  type WorkspacePaneView,
  type WorkspacePeekView,
} from "@/lib/grove/api";
import {
  applyWorkspaceState,
  peekFromActivity,
  streamAction,
  turnsProgressFingerprint,
} from "@/lib/grove/adapters";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

/**
 * The daemon's event stream, owned ONCE for the whole app.
 *
 * It is a provider rather than a per-route hook because "is the stream
 * connected" is now the gate on every polled query (see `queries.ts`), and a
 * per-route hook cannot answer that for a hook mounted somewhere else. Two
 * components each calling their own version opened two `EventSource`s to one
 * `/events` — measured — and each cost the daemon a full snapshot on connect.
 *
 * The snapshot lives in the react-query cache rather than in component state,
 * because `GET /activity` and the stream's connect-time `snapshot` frame are
 * the same `DashboardSnapshotView`. One cache entry therefore lets the stream
 * WRITE what the fallback query would otherwise have fetched, and lets any
 * component read the fleet without subscribing to anything.
 *
 * The pure fold lives in `adapters/activity`; everything here is the transport
 * — connect, self-heal, and fall back to a poll when there is no stream at all.
 */

/** How long a stream may go silent before we assume it died. The daemon
 * heartbeats every 15 s, so anything past this saw no heartbeat either. */
const STALE_MS = 20_000;

/** The fallback poll, used only while the stream is down (or absent, which is
 * how this hook behaves under a test renderer with no `EventSource`). */
const FALLBACK_POLL_MS = 4_000;

/**
 * How long a refetch that no delta could replace waits for its neighbours.
 *
 * Only `workspace_changed` reaches that path, and one structural change emits
 * several of them (the manager bus fires per lifecycle event), so without a
 * window a single create costs three full-snapshot reads.
 */
const REFETCH_COALESCE_MS = 1_000;

/** The longest a failed stream waits before trying again. Bounded well under
 * the daemon's 15 s heartbeat so a restarted daemon is picked up promptly. */
const RECONNECT_MAX_MS = 10_000;

/** One toast id for the connectivity edge, so a run of `onerror`/`onopen`
 * pairs updates one balloon in place instead of stacking one per retry. */
const CONNECTIVITY_TOAST_ID = "grove-connectivity";

/**
 * The connectivity toast is an EDGE, never a level — `connected` already
 * drives the persistent `ConnectionState` pill in the rail (see
 * `connectionStateProps`), so this only decides whether the TRANSITION into
 * or out of that state is worth a balloon.
 *
 * Pure so the transition table is verifiable without a socket, an
 * `EventSource`, or a mounted provider — the same reason `backstopInterval`
 * and `connectionStateProps` are pure functions beside their effectful hooks.
 *
 * `wasEverConnected` is what stops two false positives: a page load against a
 * daemon that is already down must not announce a connection it never had
 * ("lost" requires having HAD one), and the very first successful connect
 * must not celebrate a "reconnection" that never dropped.
 */
export function connectivityToastAction(
  connected: boolean,
  wasEverConnected: boolean,
): "lost" | "resumed" | null {
  if (connected) return wasEverConnected ? "resumed" : null;
  return wasEverConnected ? "lost" : null;
}

export interface ActivityStream {
  snapshot: DashboardSnapshotView | null;
  connected: boolean;
  /** Reconnect attempts since the last healthy connection — 0 while healthy. */
  attempt: number;
  /** Force a fresh connection; the daemon replies with a full snapshot. */
  reconnect: () => void;
  /** The fallback fetch's failure, for a surface that renders one. */
  error: Error | null;
  /** No snapshot has arrived from either the stream or the fallback yet. */
  isPending: boolean;
  /** Re-read `/activity` by hand — what a "Retry" control calls. */
  refetch: () => void;
}

const StreamContext = createContext<ActivityStream | null>(null);

/**
 * Mount EXACTLY once, above everything that reads the fleet.
 *
 * It sits beside the query client in `components/grove/providers` because the
 * cache is where it puts its results: the provider without the client has
 * nowhere to write, and every consumer needs both.
 */
export function GroveStreamProvider({ children }: { children: React.ReactNode }) {
  const value = useStreamTransport();
  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>;
}

/**
 * The live fleet plus the stream's health.
 *
 * Every consumer gets the SAME object, so `connected` means one thing across
 * the app — which is what makes it safe to gate a query's interval on.
 */
export function useActivityStream(): ActivityStream {
  const value = useContext(StreamContext);
  if (value === null) {
    throw new Error("useActivityStream must be used inside <GroveStreamProvider>");
  }
  return value;
}

/**
 * A polled query's interval, gated on the stream.
 *
 * The whole cadence table in `client.ts` is documented as a BACKSTOP for a
 * dropped stream; this one line is what makes that documentation true. Pure, so
 * the decision is testable without a socket or a query client.
 */
export function backstopInterval(connected: boolean, ms: number): number | false {
  return connected ? false : ms;
}

function useStreamTransport(): ActivityStream {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [reconnectKey, setReconnectKey] = useState(0);

  const lastEventAtRef = useRef(0);
  const coalescing = useRef<ReturnType<typeof setTimeout> | null>(null);
  const everConnectedRef = useRef(false);

  const reconnect = useCallback(() => setReconnectKey((key) => key + 1), []);

  // The one balloon for the whole app's connectivity, fired on the EDGE only.
  // `attempt` climbing on every retry must not re-trigger this: it is not in
  // the dependency array, and `connected` itself only flips at the two
  // transitions that matter.
  useEffect(() => {
    const action = connectivityToastAction(connected, everConnectedRef.current);
    if (action === "lost") {
      toast.error("Lost connection to the daemon", {
        id: CONNECTIVITY_TOAST_ID,
        description: "Grove keeps retrying in the background.",
      });
    } else if (action === "resumed") {
      toast.success("Reconnected to the daemon", { id: CONNECTIVITY_TOAST_ID });
    }
    if (connected) everConnectedRef.current = true;
  }, [connected]);

  // The one read of `/activity`, and the disconnected backstop. It still runs
  // once on mount: the cache starts empty, and waiting on the SSE handshake to
  // paint would leave a browser with no `EventSource` showing nothing at all.
  const fallback = useQuery({
    queryKey: groveKeys.activity,
    queryFn: () => groveClient.getActivity(),
    refetchInterval: backstopInterval(connected, FALLBACK_POLL_MS),
  });

  useEffect(() => {
    const invalidateSoon = (): void => {
      if (coalescing.current !== null) return;
      coalescing.current = setTimeout(() => {
        coalescing.current = null;
        void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
      }, REFETCH_COALESCE_MS);
    };

    const onEvent = (event: DashboardEvent): void => {
      lastEventAtRef.current = Date.now();
      const current = queryClient.getQueryData<DashboardSnapshotView>(groveKeys.activity) ?? null;
      const action = streamAction(current, event);

      switch (action.kind) {
        case "ignore":
          return;
        case "adopt":
          queryClient.setQueryData(groveKeys.activity, action.snapshot);
          return;
        case "apply":
          queryClient.setQueryData(groveKeys.activity, action.snapshot);
          refreshPeekFromStream(queryClient, action.workspace);
          return;
        case "drop":
          queryClient.setQueryData(groveKeys.activity, action.snapshot);
          // Everything keyed under the dead workspace is unreachable now, and
          // leaving it would serve a killed workspace's peek to whoever
          // navigates back to its URL.
          queryClient.removeQueries({ queryKey: groveKeys.workspace(action.workspaceId) });
          return;
        case "resync":
          void resyncWorkspace(queryClient, action.workspaceId);
          return;
        case "queue_changed":
          void queryClient.invalidateQueries({ queryKey: groveKeys.queue(action.workspaceId) });
          return;
        case "refetch":
          invalidateSoon();
          return;
      }
    };

    const stream = subscribeToEventStream(`${GroveClient.basePath}/events`, {
      onEvent,
      onOpen: () => {
        lastEventAtRef.current = Date.now();
        setConnected(true);
        setAttempt(0);
      },
      onError: () => {
        setConnected(false);
        setAttempt((count) => count + 1);
      },
    });
    if (!stream) return undefined;

    // An `EventSource` in a background tab can die WITHOUT firing `onerror`, so
    // `connected` stays true and the poll fallback never arms — a surface that
    // looks live and is frozen. Nothing else notices, so the stream needs its
    // own reflex on the way back.
    const onVisible = (): void => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastEventAtRef.current > STALE_MS) reconnect();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      if (coalescing.current !== null) {
        clearTimeout(coalescing.current);
        coalescing.current = null;
      }
      document.removeEventListener("visibilitychange", onVisible);
      stream.close();
    };
  }, [queryClient, reconnectKey, reconnect]);

  // An `EventSource` does NOT always heal itself. The spec only auto-retries a
  // DROPPED connection; a non-200 response fails the source permanently, and a
  // non-200 is exactly what the BFF returns for the whole time the daemon is
  // down. Measured: stop the daemon, start it again, and the stream stays dead
  // while the daemon serves happily. That used to cost only a stale banner,
  // because everything polled anyway — now the stream IS the freshness
  // mechanism, so an unhealed stream strands the app on its backstop forever.
  useEffect(() => {
    if (connected || attempt === 0) return undefined;
    const timer = setTimeout(reconnect, Math.min(1_000 * 2 ** (attempt - 1), RECONNECT_MAX_MS));
    return () => clearTimeout(timer);
  }, [connected, attempt, reconnect]);

  return {
    snapshot: fallback.data ?? null,
    connected,
    attempt,
    reconnect,
    error: fallback.error,
    isPending: fallback.isPending,
    refetch: () => void fallback.refetch(),
  };
}

/**
 * Re-read ONE workspace's lifecycle record and graft it onto the fleet.
 *
 * The cheap half of `workspace_changed`: an `updated` moves title, description
 * or `ticket_refs`, none of which are in the activity fingerprint, so no delta
 * will ever bring them over — but a whole `/activity` to collect three fields
 * of one row is what this replaces. Best-effort by design: a failed read leaves
 * the row exactly as it was, and the next snapshot corrects it anyway.
 */
async function resyncWorkspace(queryClient: QueryClient, workspaceId: string): Promise<void> {
  try {
    const state = await groveClient.getWorkspace(workspaceId);
    queryClient.setQueryData(groveKeys.workspace(workspaceId), state);
    const snapshot = queryClient.getQueryData<DashboardSnapshotView>(groveKeys.activity) ?? null;
    const next = applyWorkspaceState(snapshot, state);
    if (next) queryClient.setQueryData(groveKeys.activity, next);
  } catch {
    // A workspace killed between the frame and this read 404s; the `killed`
    // frame that follows is what removes the row.
  }
}

/**
 * Refresh an open workspace's peek off the row the stream just delivered.
 *
 * Deliberately only touches an entry that ALREADY exists: seeding one would
 * hand `useWorkspacePeek` a peek whose pane capture is null and stop it fetching
 * the real one. Gating peek's interval WITHOUT this is what would make it
 * silently stale — its consumers read `peek.data`, not the activity row.
 */
function refreshPeekFromStream(
  queryClient: QueryClient,
  activity: WorkspaceActivityView,
): void {
  const key = groveKeys.peek(activity.state.id);
  const previous = queryClient.getQueryData<WorkspacePeekView>(key);
  if (!previous) return;
  queryClient.setQueryData(key, peekFromActivity(activity, previous));
}

/**
 * Refresh an open transcript the instant its session makes real progress.
 *
 * Rides the snapshot the page already holds — no second stream — and diffs a
 * fingerprint rather than the snapshot itself, so a heartbeat or a pane frame
 * costs nothing. Without this the transcript would only move on its 30 s
 * backstop while the terminal pane updated in about a second.
 */
export function useTranscriptInvalidation(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string | null,
  sessionId: string | null,
): void {
  const queryClient = useQueryClient();
  const fingerprint = workspaceId
    ? turnsProgressFingerprint(snapshot, workspaceId, sessionId)
    : "";
  const previous = useRef<string | null>(null);

  useEffect(() => {
    // Skip the first run: the initial fingerprint is not a change, and
    // invalidating on mount would double the transcript's own first fetch.
    if (previous.current !== null && previous.current !== fingerprint && workspaceId && sessionId) {
      void queryClient.invalidateQueries({ queryKey: groveKeys.turns(workspaceId, sessionId) });
    }
    previous.current = fingerprint;
  }, [fingerprint, workspaceId, sessionId, queryClient]);
}

/**
 * The live terminal pane for ONE workspace.
 *
 * Deliberately not a per-card subscription: the caller passes a single focused
 * id, so the page enforces at most one pane stream. `ansi` may be null for an
 * idle or dead pane, and the pane resets to undefined whenever the id or the
 * gate changes — a stale pane flashing on a newly focused workspace is worse
 * than an empty one.
 */
export function useWorkspacePane(
  id: string | null,
  enabled: boolean,
): WorkspacePaneView | undefined {
  const [pane, setPane] = useState<WorkspacePaneView | undefined>(undefined);

  useEffect(() => {
    setPane(undefined);
    if (!id || !enabled) return undefined;
    const stream = subscribeToEventStream(GroveClient.paneStreamUrl(id), {
      onEvent: (event) => {
        if (event.kind === "pane_snapshot" && event.pane) setPane(event.pane);
      },
    });
    return () => stream?.close();
  }, [id, enabled]);

  return pane;
}
