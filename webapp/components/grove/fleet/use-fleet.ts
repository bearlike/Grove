"use client";

import { useRouter } from "next/navigation";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  GroveClient,
  type CreateWorkspaceRequest,
  type DashboardSnapshotView,
  type WorkspaceStateView,
} from "@/lib/grove/api";
import { groveClient, groveKeys, useActivityStream } from "@/lib/grove/hooks";

/**
 * The fleet workstream's whole network surface, deliberately in ONE file.
 *
 * `lib/grove/hooks/` did not exist when this was written; when it lands, this
 * file is what folds into it, and nothing else under `components/grove/fleet/`
 * touches the network. The event stream has already folded in: it is now one
 * app-wide provider, because "is the stream connected" gates every polled
 * query and a second subscription here meant two `EventSource`s to one route.
 */

const client = GroveClient.create();

/** The shared cache entry the stream writes into — not a second key, or the
 * fleet would poll for a snapshot the provider already holds. */
const FLEET_KEY = groveKeys.activity;

/**
 * `dropped` is never reported, but NOT because `EventSource` heals itself —
 * measured, it does not when the BFF answers non-200. The provider owns the
 * backoff reconnect, so there is still nothing here for a user to press.
 */
export type StreamPhase = "online" | "reconnecting";

export interface FleetStream {
  readonly snapshot: DashboardSnapshotView | undefined;
  readonly error: Error | null;
  readonly isPending: boolean;
  readonly phase: StreamPhase;
  readonly refetch: () => void;
}

/**
 * The live fleet, in the shape the rail renders.
 *
 * A pure projection of the app's ONE stream (`GroveStreamProvider`) — this used
 * to open its own `EventSource` beside the workspace page's, so a workspace
 * route held two subscriptions to `/events` and the daemon sent two connect
 * snapshots. It also refetched all of `/activity` on every `session_activity`
 * frame, because it only accepted a frame carrying `event.snapshot`; the
 * provider applies those as deltas instead.
 */
export function useFleetStream(): FleetStream {
  const stream = useActivityStream();

  return {
    snapshot: stream.snapshot ?? undefined,
    error: stream.error,
    isPending: stream.isPending,
    // `attempt`, not `connected`: the stream is not connected for the first few
    // milliseconds of any page load either, and announcing "reconnecting" there
    // would flash a fault at every visit.
    phase: stream.attempt > 0 ? "reconnecting" : "online",
    refetch: stream.refetch,
  };
}

/**
 * The cached fleet, without subscribing to anything.
 *
 * Shares the provider's cache entry AND its fetcher, so it reads whatever the
 * stream last wrote and can never drift into a second definition of what
 * "the fleet" is fetched with.
 */
export function useFleetSnapshot(): UseQueryResult<DashboardSnapshotView, Error> {
  return useQuery({ queryKey: FLEET_KEY, queryFn: () => groveClient.getActivity() });
}

/**
 * Create a workspace and go straight to it — the point of creating one is to
 * watch it work, so landing back on the dashboard would just cost a click.
 */
export function useCreateWorkspace(): UseMutationResult<
  WorkspaceStateView,
  Error,
  CreateWorkspaceRequest
> {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: (request: CreateWorkspaceRequest) => client.createWorkspace(request),
    onSuccess: (workspace) => {
      void queryClient.invalidateQueries({ queryKey: FLEET_KEY });
      router.push(`/w/${workspace.id}`);
    },
  });
}
