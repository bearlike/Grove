"use client";

import { useEffect } from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import type {
  PublicWorkspaceView,
  SessionDetailView,
  SessionTurnView,
  WorkspaceDiffView,
} from "@/lib/grove/api";
import { mergeTurns, turnCursor } from "@/lib/grove/adapters";
import { INITIAL_TURN_WINDOW } from "./queries";
import { PublicClient, publicClient } from "../api/public-client";

const RECONNECT_MAX_MS = 10_000;

type PublicStreamOwner = {
  clients: Map<QueryClient, number>;
  source: EventSource | null;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  attempt: number;
  token: string;
};

/** Exported for `tool-body.ts`, which serves both namespaces and must not
 * respell a key table that already exists. */
export const publicKeys = {
  root: (token: string) => ["grove", "public", token] as const,
  workspace: (token: string) => ["grove", "public", token] as const,
  turns: (token: string) => ["grove", "public", token, "turns"] as const,
  tool: (token: string, toolUseId: string) =>
    ["grove", "public", token, "tools", toolUseId] as const,
  diff: (token: string, path: string | undefined) =>
    ["grove", "public", token, "diff", path ?? null] as const,
};

const publicStreams = new Map<string, PublicStreamOwner>();

/** Only state-bearing public frames make the cached reads stale. */
export function publicStreamAction(kind: string): "invalidate" | "ignore" {
  return kind === "snapshot" || kind === "changed" ? "invalidate" : "ignore";
}

/** Retry a public capability stream without permitting unbounded quiet retries. */
export function publicStreamReconnectDelay(attempt: number): number {
  return Math.min(1_000 * 2 ** Math.max(attempt - 1, 0), RECONNECT_MAX_MS);
}

/**
 * One EventSource per share capability, regardless of how many public reads its
 * page mounts.
 *
 * EventSource cannot attach the reader's passcode header. The public BFF
 * receives that header on an ordinary public read and scopes a cookie to this
 * endpoint, so this connection never exposes a host credential or creates a
 * second browser-side authentication path.
 */
function usePublicStream(token: string): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!token || typeof EventSource === "undefined") return undefined;

    const owner = publicStreams.get(token) ?? createPublicStreamOwner(token);
    owner.clients.set(queryClient, (owner.clients.get(queryClient) ?? 0) + 1);
    startPublicStream(owner);

    return () => unsubscribePublicStream(owner, queryClient);
  }, [queryClient, token]);
}

function createPublicStreamOwner(token: string): PublicStreamOwner {
  const owner: PublicStreamOwner = {
    clients: new Map(),
    source: null,
    reconnectTimer: null,
    attempt: 0,
    token,
  };
  publicStreams.set(token, owner);
  return owner;
}

function startPublicStream(owner: PublicStreamOwner): void {
  if (owner.source !== null || owner.clients.size === 0) return;

  const source = new EventSource(
    `${PublicClient.basePath}/${encodeURIComponent(owner.token)}/events`,
  );
  owner.source = source;

  const receive = (event: Event): void => {
    if (owner.source !== source || publicStreamAction(event.type) !== "invalidate") return;
    for (const queryClient of owner.clients.keys()) {
      void queryClient.invalidateQueries({ queryKey: publicKeys.root(owner.token) });
    }
  };

  source.addEventListener("snapshot", receive);
  source.addEventListener("changed", receive);
  source.onopen = () => {
    if (owner.source !== source) return;
    owner.attempt = 0;
    clearPublicStreamReconnect(owner);
  };
  source.onerror = () => {
    if (owner.source !== source) return;
    source.close();
    owner.source = null;
    schedulePublicStreamReconnect(owner);
  };
}

function schedulePublicStreamReconnect(owner: PublicStreamOwner): void {
  if (owner.clients.size === 0 || owner.reconnectTimer !== null) return;
  owner.attempt += 1;
  owner.reconnectTimer = setTimeout(() => {
    owner.reconnectTimer = null;
    startPublicStream(owner);
  }, publicStreamReconnectDelay(owner.attempt));
}

function clearPublicStreamReconnect(owner: PublicStreamOwner): void {
  if (owner.reconnectTimer === null) return;
  clearTimeout(owner.reconnectTimer);
  owner.reconnectTimer = null;
}

function unsubscribePublicStream(owner: PublicStreamOwner, queryClient: QueryClient): void {
  const references = owner.clients.get(queryClient) ?? 0;
  if (references > 1) {
    owner.clients.set(queryClient, references - 1);
    return;
  }
  owner.clients.delete(queryClient);
  if (owner.clients.size > 0) return;

  owner.source?.close();
  owner.source = null;
  clearPublicStreamReconnect(owner);
  publicStreams.delete(owner.token);
}

/** The public page's complete bounded overview. */
export function usePublicWorkspace(
  token: string,
): UseQueryResult<PublicWorkspaceView> {
  usePublicStream(token);
  return useQuery({
    queryKey: publicKeys.workspace(token),
    queryFn: () => publicClient.overview(token),
    enabled: Boolean(token),
  });
}

/**
 * The share's bounded live transcript.
 *
 * The public route deliberately preserves the authenticated route's cursor
 * contract, so it can use the same fail-safe merge: the first response is a
 * tail, a cursor follows every held window, and a non-incremental response is
 * taken wholesale rather than guessed into a potentially discontinuous range.
 */
export function usePublicTurns(
  token: string,
): UseQueryResult<SessionDetailView | null> {
  usePublicStream(token);
  const queryClient = useQueryClient();
  const key = publicKeys.turns(token);

  return useQuery({
    queryKey: key,
    queryFn: async () => {
      const held = queryClient.getQueryData<SessionDetailView | null>(key);
      const afterTurn = turnCursor(held ?? undefined);
      const options =
        afterTurn !== undefined
          ? { afterTurn }
          : // `null` is a real "no transcript yet" response, but it holds no
            // window. If one appears between reads, start with the bounded tail
            // rather than turning that first readable response into a full fetch.
            held === undefined || held === null
            ? { last: INITIAL_TURN_WINDOW }
            : {};
      const window = await publicClient.turns(token, options);
      if (window === null) return null;

      const merged = mergeTurns(held ?? undefined, window);
      if (merged.kind === "refetch") return publicClient.turns(token);
      if (merged.kind === "unchanged") {
        return {
          ...window,
          turns: held!.turns,
          first_turn_index: held!.first_turn_index,
        };
      }
      return {
        ...window,
        turns: merged.turns as SessionTurnView[],
        first_turn_index: merged.first_turn_index,
      };
    },
    enabled: Boolean(token),
  });
}

/** The shared working-tree patch, optionally narrowed to one changed file. */
export function usePublicDiff(
  token: string,
  path?: string,
): UseQueryResult<WorkspaceDiffView> {
  usePublicStream(token);
  return useQuery({
    queryKey: publicKeys.diff(token, path),
    queryFn: () => publicClient.diff(token, path),
    enabled: Boolean(token),
  });
}
