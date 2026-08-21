"use client";

import {
  useQuery,
  useQueryClient,
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
import { publicClient } from "../api/public-client";

const PUBLIC_POLL_MS = 5_000;

const publicKeys = {
  workspace: (token: string) => ["grove", "public", token] as const,
  turns: (token: string) => ["grove", "public", token, "turns"] as const,
  diff: (token: string, path: string | undefined) =>
    ["grove", "public", token, "diff", path ?? null] as const,
};

/**
 * The public page's complete bounded overview.
 *
 * A public share has no SSE: `/events` is a host-wide fan-out and cannot become
 * a capability endpoint. Its polling is therefore the freshness mechanism,
 * not an SSE backstop. Do not use `backstopInterval` here — that helper gates
 * on the private activity stream, which this page has none of, and would leave
 * every shared page permanently stale.
 */
export function usePublicWorkspace(
  token: string,
): UseQueryResult<PublicWorkspaceView> {
  return useQuery({
    queryKey: publicKeys.workspace(token),
    queryFn: () => publicClient.overview(token),
    enabled: Boolean(token),
    refetchInterval: PUBLIC_POLL_MS,
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
            // window. If one appears between polls, start with the bounded tail
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
    refetchInterval: PUBLIC_POLL_MS,
  });
}

/** The shared working-tree patch, optionally narrowed to one changed file. */
export function usePublicDiff(
  token: string,
  path?: string,
): UseQueryResult<WorkspaceDiffView> {
  return useQuery({
    queryKey: publicKeys.diff(token, path),
    queryFn: () => publicClient.diff(token, path),
    enabled: Boolean(token),
    refetchInterval: PUBLIC_POLL_MS,
  });
}
