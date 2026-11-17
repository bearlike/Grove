"use client";

import { useMutation, useQuery, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";
import { toast } from "sonner";

import { groveClient, POLL_MS } from "./client";
import { groveKeys } from "./keys";

/** The backend owns pin order; browser storage never participates. */
export function useSidebarPins(): UseQueryResult<string[]> {
  return useQuery({
    queryKey: groveKeys.sidebarPins,
    queryFn: ({ signal }) => groveClient.getSidebarPins(signal),
    refetchInterval: POLL_MS.sidebarPins,
  });
}

/** Serialize local changes and re-read after settling so a stale poll cannot undo a pin. */
export function useSetSidebarPin(): UseMutationResult<string[], Error, { id: string; pinned: boolean }> {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: "sidebar-pins" },
    mutationFn: ({ id, pinned }: { id: string; pinned: boolean }) => groveClient.setSidebarPin(id, pinned),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: groveKeys.sidebarPins });
    },
    onSuccess: (pins) => queryClient.setQueryData(groveKeys.sidebarPins, pins),
    onError: (error) => toast.error("Could not update session pin", { description: error.message }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: groveKeys.sidebarPins }),
  });
}
