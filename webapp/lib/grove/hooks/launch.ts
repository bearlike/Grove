"use client";

import { useMutation, useQuery, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";

import type { components } from "@/lib/grove/api/types.gen";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

type WorkspaceDefaultsView = components["schemas"]["WorkspaceDefaultsView"];
type WorkspaceDefaultsSaveView = components["schemas"]["WorkspaceDefaultsSaveView"];
type DefaultsScope = components["schemas"]["DefaultsScope"];

/** Read the resolved defaults that the selected repository's create path would use. */
export function useWorkspaceDefaults(repoRoot: string | null): UseQueryResult<WorkspaceDefaultsView> {
  return useQuery({
    queryKey: groveKeys.defaults(repoRoot ?? ""),
    queryFn: () => groveClient.getWorkspaceDefaults(repoRoot!),
    enabled: repoRoot !== null,
  });
}

export interface SaveDefaultsInput {
  readonly defaults: WorkspaceDefaultsView;
  readonly scope: DefaultsScope;
  readonly repoRoot?: string;
}

/**
 * Replace one defaults scope with the complete object.
 *
 * The engine writer replaces its whole `defaults` section, so this deliberately
 * accepts a complete wire shape rather than a partial update that would erase
 * omitted choices.
 */
export function useSaveDefaults(): UseMutationResult<WorkspaceDefaultsSaveView, Error, SaveDefaultsInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ defaults, scope, repoRoot }: SaveDefaultsInput) =>
      groveClient.saveWorkspaceDefaults(defaults, scope, repoRoot),
    onSuccess: (_result, { repoRoot }) => {
      if (repoRoot) void queryClient.invalidateQueries({ queryKey: groveKeys.defaults(repoRoot) });
    },
  });
}

type TicketRef = components["schemas"]["TicketRef"];

/**
 * Open tickets assigned to the tracker identity configured for one repository.
 *
 * `configured` is required rather than optional because it is the whole safety
 * of this call: an uncredentialed provider has no notion of "assigned to me",
 * so the request can only fail, and the honest response to that is to offer no
 * suggestion at all. Pass the flag straight from `useTicketProviders`.
 */
export function useAssignedTickets(
  repoRoot: string | null,
  configured: boolean,
): UseQueryResult<TicketRef[]> {
  return useQuery({
    queryKey: groveKeys.assignedTickets(repoRoot ?? ""),
    queryFn: () => groveClient.listAssignedTickets(repoRoot!),
    enabled: repoRoot !== null && configured,
  });
}

export type { DefaultsScope, TicketRef, WorkspaceDefaultsSaveView, WorkspaceDefaultsView };
