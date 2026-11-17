"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import type { ArtifactAttachRequest, WorkspaceStateView } from "@/lib/grove/api";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

/**
 * One file artifact's bytes, fetched when a viewer opens.
 *
 * An artifact is a pointer to a file the agent keeps editing, so nothing is
 * cached across opens: every mount reads the file as it is now.
 *
 * @param workspaceId The workspace the artifact is attached to.
 * @param artifactId The artifact to read, or `null` while no viewer is open.
 */
export function useArtifactContent(
  workspaceId: string,
  artifactId: string | null,
): UseQueryResult<Blob> {
  return useQuery({
    queryKey: groveKeys.artifactContent(workspaceId, artifactId ?? ""),
    queryFn: () => groveClient.getArtifactContent(workspaceId, artifactId ?? ""),
    enabled: artifactId !== null,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

/** Both verbs change the workspace record, so both re-read it the same way. */
function useArtifactMutation<T>(
  run: (input: T) => Promise<WorkspaceStateView>,
): UseMutationResult<WorkspaceStateView, Error, T> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.workspaces });
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

/** Attach a file path or a link. A refused target surfaces the daemon's typed error. */
export function useAttachArtifact(
  workspaceId: string,
): UseMutationResult<WorkspaceStateView, Error, ArtifactAttachRequest> {
  return useArtifactMutation((request: ArtifactAttachRequest) =>
    groveClient.attachArtifact(workspaceId, request),
  );
}

/** Detach by artifact id. */
export function useDetachArtifact(
  workspaceId: string,
): UseMutationResult<WorkspaceStateView, Error, string> {
  return useArtifactMutation((artifactId: string) =>
    groveClient.detachArtifact(workspaceId, artifactId),
  );
}
