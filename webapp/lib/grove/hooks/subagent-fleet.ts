"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";

import { GroveClient, subscribeToEventStream } from "@/lib/grove/api";
import type { GroveStreamEvent, SubagentFleetData } from "@/lib/grove/api";
import { groveClient, POLL_MS } from "./client";
import { groveKeys } from "./keys";
import { backstopInterval, useActivityStream } from "./stream";

export type { SubagentFleetData } from "@/lib/grove/api";

/** A root-selected fleet needs one independent cache, never the transcript key. */
export function useSubagentFleet(
  workspaceId: string | null,
  rootSessionId: string | null,
): UseQueryResult<SubagentFleetData> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.fleet(workspaceId ?? "", rootSessionId ?? ""),
    queryFn: () => groveClient.getSubagentFleet(workspaceId!, rootSessionId!),
    enabled: workspaceId !== null && rootSessionId !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.fleet),
  });
}

/**
 * Subscribe exactly once for the visible root and write only its fleet cache.
 * A child update must never invalidate the parent transcript that owns the card.
 */
export function useSubagentFleetStream(
  workspaceId: string | null,
  rootSessionId: string | null,
  enabled: boolean,
): boolean {
  const queryClient = useQueryClient();
  const [stale, setStale] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    const currentGeneration = generation.current;
    setStale(false);
    if (!workspaceId || !rootSessionId || !enabled) return undefined;

    const key = groveKeys.fleet(workspaceId, rootSessionId);
    const stream = subscribeToEventStream<GroveStreamEvent>(GroveClient.fleetStreamUrl(workspaceId, rootSessionId), {
      onEvent: (event) => {
        if (generation.current !== currentGeneration || event.kind !== "fleet_snapshot") return;
        queryClient.setQueryData(key, event.fleet);
        setStale(false);
      },
      onError: () => {
        if (generation.current === currentGeneration) setStale(true);
      },
    });
    return () => stream?.close();
  }, [workspaceId, rootSessionId, enabled, queryClient]);

  return stale;
}
