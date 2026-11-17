"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import type {
  AdoptProject,
  CloneProject,
  CreateProject,
  DashboardSnapshotView,
  ProjectCandidatesView,
  ProjectView,
} from "@/lib/grove/api";
import { withProject } from "@/lib/grove/adapters/activity";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

export type AddProjectRequest = CreateProject | CloneProject | AdoptProject;

/**
 * Repositories under the projects root that Grove does not list yet.
 *
 * Fetched only while a reader is on the step that lists them (`enabled`), never
 * polled: the scan stats a directory and reads each candidate's remotes, and the
 * answer only changes when somebody adds a project, which this module's own
 * mutation invalidates.
 */
export function useProjectCandidates(enabled: boolean): UseQueryResult<ProjectCandidatesView> {
  return useQuery({
    queryKey: groveKeys.projectCandidates,
    queryFn: () => groveClient.getProjectCandidates(),
    enabled,
    staleTime: 10_000,
  });
}

/**
 * Add one project, and show it at once.
 *
 * The daemon publishes a new project through its user-config watch, which
 * lands after this call answers, so a refetch on success can read the old
 * list. The response is the registered project, so it is folded straight into
 * the held snapshot (`withProject`); the daemon's next snapshot supersedes it.
 */
export function useAddProject(): UseMutationResult<ProjectView, Error, AddProjectRequest> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: AddProjectRequest) => groveClient.addProject(request),
    // The wizard states every refusal inline beside the field it is about, so
    // the global error toast would only say the same sentence a second time.
    meta: { toast: false },
    onSuccess: (project) => {
      const held = queryClient.getQueryData<DashboardSnapshotView>(groveKeys.activity);
      const next = held ? withProject(held, project) : null;
      if (next) queryClient.setQueryData(groveKeys.activity, next);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.projectCandidates });
    },
  });
}
