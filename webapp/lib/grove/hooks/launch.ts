"use client";

import { useMutation, useQuery, useQueryClient, type UseMutationResult, type UseQueryResult } from "@tanstack/react-query";

import type { components } from "@/lib/grove/api/types.gen";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

type WorkspaceDefaultsView = components["schemas"]["WorkspaceDefaultsView"];
type WorkspaceDefaultsSaveView = components["schemas"]["WorkspaceDefaultsSaveView"];
type DefaultsScope = components["schemas"]["DefaultsScope"];
type LaunchPreviewView = components["schemas"]["LaunchPreviewView"];
type LaunchOptionsView = components["schemas"]["LaunchOptionsView"];
type CreateWorkspaceRequest = components["schemas"]["CreateWorkspaceRequest"];

/** Read the resolved defaults that the selected repository's create path would use. */
export function useWorkspaceDefaults(repoRoot: string | null): UseQueryResult<WorkspaceDefaultsView> {
  return useQuery({
    queryKey: groveKeys.defaults(repoRoot ?? ""),
    queryFn: () => groveClient.getWorkspaceDefaults(repoRoot!),
    enabled: repoRoot !== null,
  });
}

/**
 * The engine's verdict on one serialised launch request, or nothing while the
 * request cannot be built. Keyed by the request itself, so every draft that
 * would send the same launch shares one answer and the cache does the dedupe.
 */
export function useLaunchPreview(request: string | null): UseQueryResult<LaunchPreviewView> {
  return useQuery({
    queryKey: groveKeys.launchPreview(request ?? ""),
    queryFn: () => groveClient.previewLaunch(JSON.parse(request!) as CreateWorkspaceRequest),
    enabled: request !== null,
    // A preview is a validation, not data a reader browses: re-asking on focus
    // would re-run it for nothing, and a stale one is replaced on the next edit.
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/**
 * Where a create in this repository could run: configured runtimes, the
 * implemented ones that are not configured, and each hub node's rows. Fetched
 * once per repo; container readiness inside it is the daemon's own short memo.
 */
export function useLaunchOptions(repoRoot: string | null): UseQueryResult<LaunchOptionsView> {
  return useQuery({
    queryKey: groveKeys.launchOptions(repoRoot ?? ""),
    queryFn: () => groveClient.getLaunchOptions(repoRoot!),
    enabled: repoRoot !== null,
    refetchOnWindowFocus: false,
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
